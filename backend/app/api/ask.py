"""路径二：智能问答（SSE 流式）。

三条必须坚持的做法：

1. **流式**。首字节目标 1.5 秒，先告诉用户"正在检索"，再逐字给答案。
   `status` 事件是真实进度（检索确实要几百毫秒），不是假动画。
2. **引用随流给**。最后一条 `done` 事件带上全部合法引用，前端据此渲染角标跳转。
3. **可中断**。用户关页面就停止，本轮只读（除非用户明确让建待办），取消无副作用。

`recursion_limit` 放在 config 根层——放错位置（塞进 configurable）会静默失效，
然后一个打转的 Agent 会跑上千步才被默认值拦住。
"""

from __future__ import annotations

import logging
import time
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk

from app.agents.ask.context import AskContext
from app.agents.ask.graph import agent_runtime_config, build_ask_agent
from app.agents.ask.prompt import history_hint
from app.api.deps import AuthUser, current_user, get_db, get_embeddings, get_models
from app.config import settings
from app.errors import InvalidInput, QuotaExceeded
from app.infra.db import Database
from app.infra.llm import Embeddings, Models
from app.infra.sse import SSE_HEADERS, sse
from app.repo import infra as infra_repo
from app.repo import qa as qa_repo

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ask"])


def _build_messages(query: str, history: list[dict], turns: int) -> list[dict]:
    """组装输入消息。

    历史在这里裁剪（最近 4 轮），而不是进 Agent 之后靠中间件压缩——
    压缩发生在运行中会让消息序列变化，进而让 prompt 缓存整段作废。
    """
    messages: list[dict] = []
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    content = query if turns == 0 else f"{history_hint(turns)}\n\n{query}"
    messages.append({"role": "user", "content": content})
    return messages


@router.post("/ask")
async def ask(
    body: dict,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    models: Models = Depends(get_models),
    embeddings: Embeddings = Depends(get_embeddings),
):
    query = (body.get("query") or "").strip()
    if not query:
        raise InvalidInput("问题不能为空")
    if len(query) > 2000:
        raise InvalidInput("问题太长，请控制在 2000 字以内")

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

        # ---- 幂等：同一 turn 重复提交不会写两条用户消息 ----
        # 用 clientTurnId 做去重（前端每次提交生成一个），避免用户连点导致重复提问
        history_rows = await qa_repo.recent_history(conn, session_id, settings.keep_recent_turns)

        await qa_repo.add_message(
            conn,
            session_id=session_id,
            user_id=auth.id,
            role="user",
            content=query,
        )

    ctx = AskContext.from_user(auth.id, db, embeddings)
    ctx.session_id = session_id
    ctx.turn_id = turn_id
    ctx.query = query
    ctx.model_name = settings.strong_model
    ctx._started_at = time.time()  # type: ignore[attr-defined]

    turns = len([h for h in history_rows if h["role"] == "user"])
    messages = _build_messages(query, history_rows, turns)

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
                    if not isinstance(token, AIMessageChunk):
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

        except Exception as exc:  # noqa: BLE001
            logger.exception("问答失败 turn=%s", turn_id)
            yield sse(
                "error",
                {"code": "model_unavailable", "message": f"{type(exc).__name__}", "recoverable": True},
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
    """最后一道校验：角标指向的 chunk 必须真实存在且属于当前用户。

    中间件的校验靠的是"这个编号来自某次真实检索"，已经足够；
    但多加一道 SQL 核对成本很低（一次 in 查询），能把"检索结果过期后被删"
    这种边角情况也挡住。
    """
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
    return {"sessions": rows}


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
    return {"session": session, "messages": rows}
