"""记录、分析结果、切片、关联的数据访问。

所有函数都接收一个已经 `SET LOCAL app.user_id` 的连接，
也就是说：**这里的 SQL 不需要再写 `where user_id = ?`**（除了分页列表这类要排序的），
RLS 会兜住。写显式条件是为了让索引能用上，不是为了安全。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


# --------------------------------------------------------------------------- items


async def create_item(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    kind: str,
    raw_input: str,
    body_md: str,
    content_hash: str,
    search_text: str,
    source_channel: str,
    source_url: str | None,
    title: str | None,
) -> tuple[dict | None, dict | None]:
    """插入记录。返回 (新记录, 重复的已有记录)——两者必有其一。

    去重靠 `unique(user_id, content_hash)`。`on conflict do nothing` 时拿不到
    returning，所以再查一次已有行，把"重复"这件事如实告诉用户，
    而不是静默丢弃或报 500。
    """
    cur = await conn.execute(
        """
        insert into items (user_id, kind, raw_input, body_md, content_hash,
                           search_text, source_channel, source_url, title)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (user_id, content_hash) do nothing
        returning id, analysis_status, captured_at
        """,
        (
            str(user_id),
            kind,
            raw_input,
            body_md,
            content_hash,
            search_text,
            source_channel,
            source_url,
            title,
        ),
    )
    row = await cur.fetchone()
    if row:
        return row, None

    cur = await conn.execute(
        """
        select id, analysis_status, captured_at from items
        where user_id = %s and content_hash = %s
        """,
        (str(user_id), content_hash),
    )
    return None, await cur.fetchone()


async def get_item(conn: AsyncConnection, item_id: UUID, *, with_analysis: bool = True) -> dict | None:
    cur = await conn.execute(
        """
        select id, kind, title, body_md, raw_input, source_channel, source_url, tags,
               captured_at, updated_at, analysis_status, analysis_version, user_pinned
        from items
        where id = %s and deleted_at is null
        """,
        (str(item_id),),
    )
    item = await cur.fetchone()
    if not item:
        return None
    if with_analysis:
        item["analysis"] = await latest_analysis(conn, item_id)
    return item


_ITEM_LIST_COLUMNS = """
    id, kind, title, body_md, raw_input, source_channel, source_url, tags,
    captured_at, updated_at, analysis_status, analysis_version, user_pinned
"""


async def list_items(
    conn: AsyncConnection,
    *,
    limit: int,
    offset: int,
    kind: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    status: str | None = None,
) -> tuple[list[dict], int]:
    """列表 + 总数。搜索走 `items.search_text` 的 gin 索引（jieba 分词后写入）。"""
    where = ["deleted_at is null"]
    params: dict[str, Any] = {}

    if kind:
        where.append("kind = %(kind)s")
        params["kind"] = kind
    if tag:
        where.append("%(tag)s = any(tags)")
        params["tag"] = tag
    if status:
        where.append("analysis_status = %(status)s")
        params["status"] = status
    if q:
        # 注意：这里用 websearch_to_tsquery 之前的 plainto_tsquery 更稳，
        # 但 search_text 已分词，plainto 足够；再用 trgm 兜一层模糊匹配
        where.append(
            "(to_tsvector('simple', search_text) @@ plainto_tsquery('simple', %(q)s)"
            " or body_md ilike %(like)s)"
        )
        params["q"] = q
        params["like"] = f"%{q}%"

    clause = " and ".join(where)
    cur = await conn.execute(f"select count(*) as n from items where {clause}", params)
    total = (await cur.fetchone())["n"]

    params["limit"] = limit
    params["offset"] = offset
    cur = await conn.execute(
        f"""
        select {_ITEM_LIST_COLUMNS}
        from items
        where {clause}
        order by captured_at desc
        limit %(limit)s offset %(offset)s
        """,
        params,
    )
    return await cur.fetchall(), total


async def patch_item(
    conn: AsyncConnection,
    item_id: UUID,
    *,
    title: str | None,
    body_md: str | None,
    kind: str | None,
    tags: list[str] | None,
    body_search_text: str | None = None,
    body_hash: str | None = None,
) -> dict | None:
    """用户手改。改过的记录置 `user_pinned=true`，模型不再覆盖。

    返回 None 表示没找到或无权改（RLS 下受影响行数为 0）。
    """
    sets = ["updated_at = now()", "user_pinned = true"]
    params: dict[str, Any] = {"id": str(item_id)}

    if title is not None:
        sets.append("title = %(title)s")
        params["title"] = title
    if body_md is not None:
        sets.append("body_md = %(body)s")
        params["body"] = body_md
        sets.append("content_hash = %(hash)s")
        params["hash"] = body_hash or ""
        sets.append("search_text = %(stext)s")
        params["stext"] = body_search_text or ""
    if kind is not None:
        sets.append("kind = %(kind)s")
        params["kind"] = kind
    if tags is not None:
        sets.append("tags = %(tags)s")
        params["tags"] = tags

    cur = await conn.execute(
        f"""
        update items set {", ".join(sets)}
        where id = %(id)s and deleted_at is null
        returning id
        """,
        params,
    )
    row = await cur.fetchone()
    if not row:
        return None
    return await get_item(conn, item_id)


async def soft_delete_item(conn: AsyncConnection, item_id: UUID) -> bool:
    cur = await conn.execute(
        "update items set deleted_at = now() where id = %s and deleted_at is null",
        (str(item_id),),
    )
    return cur.rowcount > 0


async def set_analysis_status(
    conn: AsyncConnection, item_id: UUID, status: str
) -> int:
    cur = await conn.execute(
        "update items set analysis_status = %s, updated_at = now() where id = %s",
        (status, str(item_id)),
    )
    return cur.rowcount


async def get_item_for_worker(conn: AsyncConnection, item_id: UUID) -> dict | None:
    """worker 取原文用：只要还没被删，什么状态都拿。"""
    cur = await conn.execute(
        "select id, body_md, raw_input, kind, source_url, analysis_version, user_pinned "
        "from items where id = %s and deleted_at is null",
        (str(item_id),),
    )
    return await cur.fetchone()


# --------------------------------------------------------------------------- analyses


async def latest_analysis(conn: AsyncConnection, item_id: UUID) -> dict | None:
    cur = await conn.execute(
        """
        select summary, key_points, todos_extracted, confidence, model, version, created_at
        from item_analyses
        where item_id = %s
        order by version desc
        limit 1
        """,
        (str(item_id),),
    )
    return await cur.fetchone()


async def next_analysis_version(conn: AsyncConnection, item_id: UUID) -> int:
    cur = await conn.execute(
        "select coalesce(max(version), 0) + 1 as v from item_analyses where item_id = %s",
        (str(item_id),),
    )
    return (await cur.fetchone())["v"]


async def insert_analysis(
    conn: AsyncConnection,
    *,
    item_id: UUID,
    version: int,
    summary: str,
    key_points: list[dict],
    todos_extracted: list[dict],
    confidence: float,
    model: str,
    prompt_version: str,
    raw_json: dict,
) -> None:
    await conn.execute(
        """
        insert into item_analyses
            (item_id, version, summary, key_points, todos_extracted,
             confidence, model, prompt_version, raw_json)
        values (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
        """,
        (
            str(item_id),
            version,
            summary,
            json.dumps(key_points, ensure_ascii=False),
            json.dumps(todos_extracted, ensure_ascii=False),
            confidence,
            model,
            prompt_version,
            json.dumps(raw_json, ensure_ascii=False, default=str),
        ),
    )


# --------------------------------------------------------------------------- chunks


async def replace_chunks(
    conn: AsyncConnection,
    *,
    item_id: UUID,
    user_id: UUID,
    chunks: list[dict],
    model: str | None,
) -> int:
    """整体替换某条记录的切片。

    先删后插，放在同一事务里：重跑流水线不会留下孤儿切片。
    这也是幂等的一部分——重算两次结果一样。
    """
    await conn.execute("delete from chunks where item_id = %s", (str(item_id),))
    if not chunks:
        return 0

    rows = []
    for idx, c in enumerate(chunks):
        emb = c.get("embedding")
        rows.append(
            (
                str(item_id),
                str(user_id),
                idx,
                c["text"],
                c.get("search_text", ""),
                c["start"],
                c["end"],
                None if emb is None else "[" + ",".join(f"{x:.6f}" for x in emb) + "]",
                model,
            )
        )

    async with conn.cursor() as cur:
        await cur.executemany(
            """
            insert into chunks (item_id, user_id, idx, text, search_text,
                                start_off, end_off, embedding, model)
            values (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
            """,
            rows,
        )
    return len(rows)


async def list_chunks(conn: AsyncConnection, item_id: UUID) -> list[dict]:
    cur = await conn.execute(
        "select idx, text, start_off, end_off from chunks where item_id = %s order by idx",
        (str(item_id),),
    )
    return await cur.fetchall()


async def chunks_without_vectors(conn: AsyncConnection, item_id: UUID) -> int:
    cur = await conn.execute(
        "select count(*) as n from chunks where item_id = %s and embedding is null",
        (str(item_id),),
    )
    return (await cur.fetchone())["n"]


# --------------------------------------------------------------------------- links


async def upsert_links(
    conn: AsyncConnection, *, user_id: UUID, src_item_id: UUID, links: list[dict]
) -> int:
    if not links:
        return 0
    rows = [
        (
            str(user_id),
            str(src_item_id),
            str(link["item_id"]),
            link.get("relation", "similar"),
            float(link["score"]),
            link.get("reason"),
        )
        for link in links
    ]
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            insert into item_links (user_id, src_item_id, dst_item_id, relation, score, reason)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (src_item_id, dst_item_id)
            do update set score = excluded.score, relation = excluded.relation,
                          reason = coalesce(excluded.reason, item_links.reason)
            """,
            rows,
        )
    return len(rows)


async def get_links(conn: AsyncConnection, item_id: UUID, limit: int = 10) -> list[dict]:
    """出链与入链都算关联：A 关联到 B，从 B 也应该看得到 A。"""
    cur = await conn.execute(
        """
        select i.id as item_id, i.title, l.relation, l.score, l.reason
        from item_links l
        join items i on i.id = l.dst_item_id
        where l.src_item_id = %(id)s and i.deleted_at is null
        union
        select i.id as item_id, i.title, l.relation, l.score, l.reason
        from item_links l
        join items i on i.id = l.src_item_id
        where l.dst_item_id = %(id)s and i.deleted_at is null
        order by score desc
        limit %(limit)s
        """,
        {"id": str(item_id), "limit": limit},
    )
    return await cur.fetchall()


async def delete_outgoing_links(conn: AsyncConnection, item_id: UUID) -> int:
    cur = await conn.execute("delete from item_links where src_item_id = %s", (str(item_id),))
    return cur.rowcount
