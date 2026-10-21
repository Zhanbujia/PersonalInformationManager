"""路径二：智能问答（SSE 流式）。

三条必须坚持的做法：

1. **流式**。首字节目标 1.5 秒，先告诉用户"正在检索"，再逐字给答案。
   `status` 事件是真实进度（检索确实要几百毫秒），不是假动画。
2. **引用随流给**。最后一条 `done` 事件带上全部合法引用，前端据此渲染角标跳转。
3. **可中断**。用户关页面就停止，本轮只读（除非用户明确让建待办），取消无副作用。

附件说明：
- 文档在**上传时**同步抽文本（存 `chat_attachments.extracted_text`），提问时拼进用户消息；
- 图片不抽文本，直接以 base64 `image_url` 随消息发给多模态模型（DeepSeek v4-flash 支持）。

`recursion_limit` 放在 config 根层——放错位置（塞进 configurable）会静默失效，
然后一个打转的 Agent 会跑上千步才被默认值拦住。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.errors import GraphRecursionError

from app.agents.ask.context import AskContext
from app.agents.ask.graph import agent_runtime_config, build_ask_agent
from app.agents.ask.prompt import history_hint
from app.api.deps import AuthUser, current_user, get_db, get_embeddings, get_models
from app.config import settings
from app.errors import InvalidInput, NotFound, QuotaExceeded
from app.infra.db import Database
from app.infra.fileparse import SUPPORTED_EXTS, FileParseError, UnsupportedFileType, extract_text
from app.infra.filestore import FileStore
from app.infra.llm import Embeddings, Models
from app.infra.sse import SSE_HEADERS, sse
from app.repo import chat_attachments as attach_repo
from app.repo import infra as infra_repo
from app.repo import qa as qa_repo

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ask"])

# 图片直接走多模态，不做 OCR
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
# 单张图片超过这个大小就不随请求发送（base64 会再胀 1/3）
MAX_IMAGE_BYTES = 6 * 1024 * 1024

FALLBACK_ANSWER = (
    "我查了几轮，暂时没有找到足够的线索。"
    "你可以换个问法，或者先把相关内容记下来/传上来再问我。"
)


def _build_messages(
    query: str, history: list[dict], turns: int, user_content: str | list
) -> list:
    """组装输入消息。历史在这里裁剪（最近 N 轮），不进 Agent 靠中间件压缩。"""
    messages: list = []
    for h in history:
        if h["role"] == "user":
            messages.append(HumanMessage(content=h["content"]))
        elif h["role"] == "assistant":
            messages.append(AIMessage(content=h["content"]))
    messages.append(HumanMessage(content=user_content))
    return messages


async def _compose_user_content(
    query: str, turns: int, attachments: list[dict], store: FileStore
) -> str | list:
    """把问题 + 附件拼成用户消息。有图片时返回 content blocks，否则返回纯文本。"""
    text = query if turns == 0 else f"{history_hint(turns)}\n\n{query}"

    docs = [a for a in attachments if a["kind"] == "doc"]
    images = [a for a in attachments if a["kind"] == "image"]

    blocks: list[str] = []
    budget = settings.chat_attach_max_chars
    for a in docs:
        if a["status"] != "ready":
            blocks.append(f"【附件：{a['file_name']}】（解析失败：{a.get('error') or '未知原因'}）")
            continue
        body = (a.get("extracted_text") or "").strip()
        if not body:
            continue
        if len(body) > budget:
            body = body[:budget] + "\n……（附件内容过长，已截断）……"
        blocks.append(f"【附件：{a['file_name']}】\n{body}")
        budget -= len(body)
        if budget <= 0:
            break
    if blocks:
        text += "\n\n以下是用户随问题上传的附件内容，请结合这些内容回答：\n\n" + "\n\n".join(blocks)

    if not images:
        return text

    content: list[dict] = [{"type": "text", "text": text}]
    for a in images[: settings.chat_attach_max_images]:
        try:
            path = store.abs_path(a["storage_key"])
            data = await asyncio.to_thread(path.read_bytes)
        except Exception:  # noqa: BLE001
            content.append({"type": "text", "text": f"\n（图片 {a['file_name']} 读取失败）"})
            continue
        if len(data) > MAX_IMAGE_BYTES:
            content.append({"type": "text", "text": f"\n（图片 {a['file_name']} 过大，未随请求发送）"})
            continue
        mime = a.get("mime_type") or ""
        if not mime.startswith("image/"):
            mime = mimetypes.guess_type(a["file_name"])[0] or "image/png"
        b64 = base64.b64encode(data).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )
    return content


def _attachment_meta(attachments: list[dict]) -> list[dict]:
    return [
        {
            "id": str(a["id"]),
            "fileName": a["file_name"],
            "mimeType": a["mime_type"],
            "fileSize": a["file_size"],
            "kind": a["kind"],
            "status": a["status"],
        }
        for a in attachments
    ]


async def _persist_assistant(ctx: AskContext, text: str) -> None:
    """降级答案手动落库（Agent 抛异常时 after_agent 不会执行）。"""
    if ctx.db is None or ctx.session_id is None:
        return
    try:
        async with ctx.db.tx(ctx.user_id) as conn:
            await qa_repo.add_message(
                conn,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                role="assistant",
                content=text,
                citations=ctx.citations,
                tokens_in=ctx.tokens_in,
                tokens_out=ctx.tokens_out,
                model=ctx.model_name or None,
            )
            await qa_repo.touch_session(conn, ctx.session_id)
    except Exception:  # noqa: BLE001
        logger.exception("降级答案落库失败 session=%s", ctx.session_id)


@router.post("/ask/attachments")
async def upload_attachment(
    file: UploadFile = File(...),
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    """上传问答附件：文档抽文本，图片留给多模态。"""
    filename = (file.filename or "").strip()
    if not filename:
        raise InvalidInput("缺少文件名")
    ext = Path(filename).suffix.lower()
    is_image = ext in IMAGE_EXTS
    if not is_image and ext not in SUPPORTED_EXTS:
        allowed = "、".join(sorted(e.lstrip(".") for e in (SUPPORTED_EXTS | IMAGE_EXTS)))
        raise InvalidInput(f"不支持的附件类型 {ext or '（无扩展名）'}；支持：{allowed}")

    store = FileStore()
    key = store.new_key(auth.id, ext)
    try:
        size, _digest = await store.save_upload(
            key, file, max_bytes=settings.max_upload_mb * 1024 * 1024
        )
    except ValueError as exc:
        raise InvalidInput(f"附件过大，最大 {settings.max_upload_mb} MB") from exc
    if size == 0:
        store.remove(key)
        raise InvalidInput("文件是空的")

    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    kind = "image" if is_image else "doc"
    text: str | None = None
    status = "ready"
    error: str | None = None

    if kind == "doc":
        try:
            text = await asyncio.to_thread(
                extract_text,
                store.abs_path(key),
                ext,
                max_chars=settings.chat_attach_max_chars,
            )
            if not text.strip():
                status = "failed"
                error = "未提取到文字（可能是扫描件或空文件）"
        except (UnsupportedFileType, FileParseError, OSError) as exc:
            status = "failed"
            error = str(exc)[:300]
        except Exception as exc:  # noqa: BLE001
            logger.exception("附件解析异常 file=%s", filename)
            status = "failed"
            error = f"解析异常：{exc}"[:300]

    try:
        async with db.tx(auth.id) as conn:
            row = await attach_repo.create_attachment(
                conn,
                user_id=auth.id,
                file_name=filename[:255],
                mime_type=mime[:120],
                file_size=size,
                storage_key=key,
                ext=ext,
                kind=kind,
                extracted_text=text,
                status=status,
                error=error,
            )
    except Exception:
        store.remove(key)
        raise

    return {
        "id": row["id"],
        "fileName": row["file_name"],
        "mimeType": row["mime_type"],
        "fileSize": row["file_size"],
        "kind": row["kind"],
        "status": row["status"],
        "error": row["error"],
        "textLength": len(text or ""),
    }


@router.get("/ask/attachments/{attachment_id}")
async def attachment_file(
    attachment_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> FileResponse:
    """取附件原文件（图片预览/下载用）。"""
    async with db.tx(auth.id) as conn:
        a = await attach_repo.get_attachment(conn, attachment_id)
    if not a:
        raise NotFound("附件不存在")
    try:
        path = FileStore().abs_path(a["storage_key"])
    except ValueError as exc:
        raise NotFound("附件路径异常") from exc
    if not path.exists():
        raise NotFound("附件已丢失")
    return FileResponse(
        path,
        filename=a["file_name"] or "attachment",
        media_type=a["mime_type"] or "application/octet-stream",
    )


@router.post("/ask")
async def ask(
    body: dict,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    models: Models = Depends(get_models),
    embeddings: Embeddings = Depends(get_embeddings),
):
    query = (body.get("query") or "").strip()
    attachment_ids_raw = body.get("attachmentIds") or []
    if not isinstance(attachment_ids_raw, list):
        raise InvalidInput("attachmentIds 必须是数组")
    if not query and not attachment_ids_raw:
        raise InvalidInput("问题不能为空")
    if len(query) > 2000:
        raise InvalidInput("问题太长，请控制在 2000 字以内")
    if not query:
        query = "请结合附件回答。"

    turn_id = str(body.get("clientTurnId") or uuid.uuid4())
    session_id_raw = body.get("sessionId")

    # ---- 会话 ----
    async with db.tx(auth.id) as conn:
        if session_id_raw:
            try:
                session_id = UUID(str(session_id_raw))
            except ValueError as exc:
                raise InvalidInput("sessionId 格式不正确") from exc
            session = await qa_repo.get_session(conn, session_id)
            if not session:
                raise InvalidInput("会话不存在")
        else:
            session_id = await qa_repo.create_session(
                conn, user_id=auth.id, title=query[:40] or "新对话"
            )

        # ---- 额度 ----
        used = await infra_repo.tokens_used_today(conn, auth.id)
        if used >= settings.daily_token_quota:
            raise QuotaExceeded("今天的问答额度已用完，明天再来")

        # ---- 历史 ----
        history_rows = await qa_repo.recent_history(conn, session_id, settings.keep_recent_turns)

    # ---- 附件 ----
    attachments: list[dict] = []
    attachment_ids: list[UUID] = []
    if attachment_ids_raw:
        for raw in attachment_ids_raw:
            try:
                attachment_ids.append(UUID(str(raw)))
            except ValueError as exc:
                raise InvalidInput("attachmentId 格式不正确") from exc
        async with db.tx(auth.id) as conn:
            attachments = await attach_repo.get_many(conn, attachment_ids)
        if len(attachments) != len(set(attachment_ids)):
            raise InvalidInput("附件不存在或无权访问")

    store = FileStore()
    turns = len([h for h in history_rows if h["role"] == "user"])
    user_content = await _compose_user_content(query, turns, attachments, store)

    async with db.tx(auth.id) as conn:
        await qa_repo.add_message(
            conn,
            session_id=session_id,
            user_id=auth.id,
            role="user",
            content=query,
            attachments=_attachment_meta(attachments),
        )
        if attachment_ids:
            await attach_repo.attach_to_session(conn, attachment_ids, session_id)

    ctx = AskContext.from_user(auth.id, db, embeddings)
    ctx.session_id = session_id
    ctx.turn_id = turn_id
    ctx.query = query
    ctx.model_name = settings.strong_model
    ctx._started_at = time.time()  # type: ignore[attr-defined]

    messages = _build_messages(query, history_rows, turns, user_content)

    agent = build_ask_agent(models.strong, ctx, db)
    config = agent_runtime_config(ctx, thread_id=f"{auth.id}:{session_id}")

    async def event_stream():
        yield sse("meta", {"turnId": turn_id, "sessionId": str(session_id)})
        yield sse("status", {"stage": "retrieving"})

        emitted_answer = False
        try:
            async for mode, chunk in agent.astream(
                {"messages": messages},
                config=config,
                stream_mode=["messages", "updates"],
                context=ctx,
            ):
                if mode == "messages":
                    token, meta = chunk
                    if meta.get("langgraph_node") == "tools":
                        continue
                    # 兼容两种形态：流式模型产出 AIMessageChunk，
                    # 非流式模型（含本地 mock）在消息流里直接产出 AIMessage。
                    if not isinstance(token, (AIMessageChunk, AIMessage)):
                        continue
                    text = token.content
                    if isinstance(text, list):
                        text = "".join(
                            b.get("text", "") for b in text if isinstance(b, dict)
                        )
                    if not text:
                        continue
                    if not emitted_answer:
                        emitted_answer = True
                        yield sse("status", {"stage": "answering"})
                    yield sse("token", {"t": text})
                else:
                    # updates：工具开始执行时给个真实进度提示
                    if isinstance(chunk, dict) and "tools" in chunk:
                        yield sse("status", {"stage": "reading"})

        except GraphRecursionError:
            # 兜底：模型并行/反复调工具把步数耗尽。不把原始异常抛给用户，
            # 给一个可读的降级答案，并手动落库（after_agent 不会执行了）。
            logger.warning("问答触发递归上限，返回降级答案 turn=%s", turn_id)
            ctx.answer_text = FALLBACK_ANSWER
            yield sse("status", {"stage": "answering"})
            yield sse("token", {"t": FALLBACK_ANSWER})
            await _persist_assistant(ctx, FALLBACK_ANSWER)

        except Exception:  # noqa: BLE001
            logger.exception("问答失败 turn=%s", turn_id)
            yield sse(
                "error",
                {
                    "code": "model_unavailable",
                    "message": "回答失败了，请稍后重试",
                    "recoverable": True,
                },
            )
            return

        # 引用：中间件已经校验过一遍，这里做最后一道"数据库里真的存在"的核对
        citations = await _final_citation_check(db, auth.id, ctx.citations)
        for c in citations:
            yield sse("citation", c)

        yield sse(
            "done",
            {
                "citations": citations,
                "tokens": {"in": ctx.tokens_in, "out": ctx.tokens_out},
                "truncated": ctx.truncated,
                "rounds": ctx.rounds,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _final_citation_check(db: Database, user_id: UUID, citations: list[dict]) -> list[dict]:
    """最后一道校验：角标指向的 chunk 必须真实存在且属于当前用户。"""
    if not citations:
        return []
    ids = [c["chunkId"] for c in citations if c.get("chunkId")]
    if not ids:
        return []

    async with db.tx(user_id) as conn:
        cur = await conn.execute(
            "select id from chunks where id = any(%s::uuid[])",
            (ids,),
        )
        alive = {str(r["id"]) for r in await cur.fetchall()}

    result = []
    for c in citations:
        if str(c.get("chunkId")) in alive:
            result.append(
                {
                    "n": c["n"],
                    "chunkId": c["chunkId"],
                    "itemId": c["itemId"],
                    "title": c.get("title"),
                    "quote": c.get("quote"),
                    "verified": True,
                }
            )
    return result


@router.get("/sessions")
async def list_sessions(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        rows = await qa_repo.list_sessions(conn)
    return {
        "sessions": [
            {
                "id": r["id"],
                "title": r["title"],
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
                "messageCount": int(r["message_count"]),
            }
            for r in rows
        ]
    }


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: UUID,
    body: dict,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    title = (body.get("title") or "").strip()
    if not title:
        raise InvalidInput("标题不能为空")
    async with db.tx(auth.id) as conn:
        row = await qa_repo.rename_session(conn, session_id, title)
    if not row:
        raise NotFound("会话不存在")
    return row


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> None:
    async with db.tx(auth.id) as conn:
        ok = await qa_repo.delete_session(conn, session_id)
    if not ok:
        raise NotFound("会话不存在")


@router.get("/sessions/{session_id}/messages")
async def session_messages(
    session_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        session = await qa_repo.get_session(conn, session_id)
        if not session:
            raise InvalidInput("会话不存在")
        rows = await qa_repo.list_messages(conn, session_id)
    return {
        "session": {
            "id": session["id"],
            "title": session["title"],
            "createdAt": session["created_at"],
            "updatedAt": session["updated_at"],
        },
        "messages": [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "citations": r["citations"] or [],
                "attachments": r["attachments"] or [],
                "model": r["model"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ],
    }
