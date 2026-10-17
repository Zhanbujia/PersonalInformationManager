"""路径二的 5 个工具。

三个设计原则：

1. **只有 5 个**。工具越多，模型选错的概率越高，而且每个工具的说明都要占上下文。
   这 5 个覆盖了全部需求，将来要加（导出、统计）走同一套注册机制。
2. **凭证与会话隔离由服务端保证**。工具的闭包里带着 `user_id` 和连接池，
   模型无法传、也无法改——它只能给参数，身份是外面注定的。
3. **返回给模型的是"带编号的片段"**，编号就是引用角标的来源。
   模型引用 `[3]` 时，`[3]` 必然对应一次真实的检索结果，这就是引用可验证的基础。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.agents.ask.context import AskContext
from app.infra import retrieval
from app.infra.db import Database
from app.repo import records as records_repo
from app.repo import todos as todos_repo

logger = logging.getLogger(__name__)

MAX_SNIPPET = 500


def _truncate(text: str, limit: int = MAX_SNIPPET) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _register_evidence(ctx: AskContext, chunk_id, item_id, title, quote) -> int:
    """给片段分配一个引用编号。同一个 chunk 复用同一个编号。"""
    for n, ev in ctx.evidence.items():
        if str(ev["chunkId"]) == str(chunk_id):
            return n
    n = ctx.next_citation_no()
    ctx.evidence[n] = {
        "chunkId": str(chunk_id),
        "itemId": str(item_id),
        "title": title,
        "quote": _truncate(quote, 240),
    }
    return n


async def _embed_query(ctx: AskContext, query: str) -> list[float] | None:
    """查询向量。拿不到就返回 None，检索自动降级为纯关键词。"""
    if ctx.embeddings is None:
        return None
    try:
        return await ctx.embeddings.embed_one(query)
    except Exception:  # noqa: BLE001
        logger.warning("查询向量化失败，降级为关键词检索", exc_info=True)
        return None


def build_tools(ctx: AskContext, db: Database) -> list[BaseTool]:
    """按请求构建工具集。

    每次请求新建一批而不是全局单例，因为工具要绑定当前用户的数据库上下文。
    新建的开销可以忽略（就是几个闭包）。
    """

    @tool
    async def search_records(
        query: str, k: int = 8, kind: str | None = None
    ) -> str:
        """在用户的全部记录里做关键词+语义混合检索，返回最相关的若干片段。

        Args:
            query: 检索词或一句话描述。中文即可，不用拆词。
            k: 返回条数，默认 8，最多 15。
            kind: 只看某类记录，可选 note/idea/link/file/todo，不填为全部。
        """
        ctx.tool_calls += 1
        k = max(1, min(int(k or 8), 15))
        cache_key = f"search:{query}:{k}:{kind}"
        if cache_key in ctx.search_cache:
            return json.dumps(ctx.search_cache[cache_key], ensure_ascii=False)

        vec = await _embed_query(ctx, query)
        async with db.tx(ctx.user_id) as conn:
            rows, took_ms = await retrieval.hybrid_search(
                conn, query_text=query, query_vec=vec, k=k, kind=kind
            )

        items = []
        for r in rows:
            n = _register_evidence(ctx, r["chunk_id"], r["item_id"], r.get("title"), r["text"])
            items.append(
                {
                    "n": n,
                    "title": r.get("title") or "（无标题）",
                    "text": _truncate(r["text"]),
                    "capturedAt": r["captured_at"].isoformat() if r.get("captured_at") else None,
                }
            )
        payload = {"hits": items, "tookMs": took_ms, "count": len(items)}
        ctx.search_cache[cache_key] = payload
        return json.dumps(payload, ensure_ascii=False)

    @tool
    async def get_record(item_id: str) -> str:
        """取某条记录的完整正文和已有摘要。当片段信息不够、需要看全文时使用。

        Args:
            item_id: 记录 id，来自 search_records 的返回结果，不要自己编。
        """
        ctx.tool_calls += 1
        try:
            uid = uuid.UUID(item_id)
        except ValueError:
            return json.dumps({"error": "item_id 格式不对，请用检索结果里给出的 id"}, ensure_ascii=False)

        async with db.tx(ctx.user_id) as conn:
            item = await records_repo.get_item(conn, uid)
        if not item:
            return json.dumps({"error": "没有找到这条记录（可能已删除）"}, ensure_ascii=False)

        # 全文也登记为一个可引用来源，供模型引用具体段落
        n = _register_evidence(ctx, uid, uid, item.get("title"), item["body_md"])
        analysis = item.get("analysis") or {}
        return json.dumps(
            {
                "n": n,
                "title": item.get("title") or "（无标题）",
                "kind": item["kind"],
                "capturedAt": item["captured_at"].isoformat(),
                "summary": analysis.get("summary"),
                "body": _truncate(item["body_md"], 4000),
            },
            ensure_ascii=False,
        )

    @tool
    async def search_by_time(start: str, end: str, kind: str | None = None) -> str:
        """按时间范围列出记录，用于"上周记了什么""这个月有哪些想法"这类问题。

        Args:
            start: 起始日期，格式 YYYY-MM-DD。
            end: 结束日期，格式 YYYY-MM-DD，包含当天。
            kind: 只看某类记录，可选 note/idea/link/file/todo，不填为全部。
        """
        ctx.tool_calls += 1
        try:
            date.fromisoformat(start)
            date.fromisoformat(end)
        except ValueError:
            return json.dumps({"error": "日期格式应为 YYYY-MM-DD"}, ensure_ascii=False)

        params: dict[str, Any] = {"start": start, "end": end, "limit": 50}
        kind_clause = ""
        if kind:
            kind_clause = "and kind = %(kind)s"
            params["kind"] = kind

        async with db.tx(ctx.user_id) as conn:
            cur = await conn.execute(
                f"""
                select id, title, kind, captured_at, left(body_md, 200) as excerpt
                from items
                where deleted_at is null
                  and captured_at >= %(start)s::date
                  and captured_at < (%(end)s::date + 1)
                  {kind_clause}
                order by captured_at desc
                limit %(limit)s
                """,
                params,
            )
            rows = await cur.fetchall()

        items = [
            {
                "n": _register_evidence(ctx, r["id"], r["id"], r.get("title"), r["excerpt"]),
                "itemId": str(r["id"]),
                "title": r.get("title") or "（无标题）",
                "kind": r["kind"],
                "capturedAt": r["captured_at"].isoformat(),
                "excerpt": _truncate(r["excerpt"] or ""),
            }
            for r in rows
        ]
        return json.dumps({"count": len(items), "items": items}, ensure_ascii=False)

    @tool
    async def list_todos(status: str = "open", due_before: str | None = None) -> str:
        """查看待办列表。

        Args:
            status: open 未完成 / done 已完成 / dropped 已放弃，默认 open。
            due_before: 只看该日期（含）之前到期的，格式 YYYY-MM-DD，不填为全部。
        """
        ctx.tool_calls += 1
        due = None
        if due_before:
            try:
                due = date.fromisoformat(due_before)
            except ValueError:
                return json.dumps({"error": "due_before 格式应为 YYYY-MM-DD"}, ensure_ascii=False)

        async with db.tx(ctx.user_id) as conn:
            rows = await todos_repo.list_todos(conn, status=status, due_before=due, limit=100)

        return json.dumps(
            {
                "count": len(rows),
                "todos": [
                    {
                        "text": r["text"],
                        "status": r["status"],
                        "dueDate": r["due_date"].isoformat() if r["due_date"] else None,
                        "priority": r["priority"],
                    }
                    for r in rows
                ],
            },
            ensure_ascii=False,
        )

    @tool
    async def create_todo(
        text: str, due_date: str | None = None, source_item_id: str | None = None
    ) -> str:
        """帮用户新建一条待办。只有在用户明确要求"记一条待办"时才调用。

        Args:
            text: 待办内容，一句话。
            due_date: 截止日期，格式 YYYY-MM-DD，用户没提就不填。
            source_item_id: 如果这条待办来自某条记录，填那条记录的 id。
        """
        ctx.tool_calls += 1
        parsed_due = None
        if due_date:
            try:
                parsed_due = date.fromisoformat(due_date)
            except ValueError:
                return json.dumps({"error": "due_date 格式应为 YYYY-MM-DD"}, ensure_ascii=False)

        src = None
        if source_item_id:
            try:
                src = uuid.UUID(source_item_id)
            except ValueError:
                src = None

        async with db.tx(ctx.user_id) as conn:
            todo = await todos_repo.create_todo(
                conn,
                user_id=ctx.user_id,
                text=text.strip()[:1000],
                due_date=parsed_due,
                priority=2,
                source_item_id=src,
                source_quote=None,
                client_id=f"agent-{uuid.uuid4()}",
            )
        return json.dumps(
            {"ok": True, "text": todo["text"], "dueDate": str(todo["due_date"] or "")},
            ensure_ascii=False,
        )

    return [search_records, get_record, search_by_time, list_todos, create_todo]


TOOL_NAMES = ["search_records", "get_record", "search_by_time", "list_todos", "create_todo"]
