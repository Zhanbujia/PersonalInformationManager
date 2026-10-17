"""阶段总结的数据访问，以及生成它需要的统计。

**所有数字都在这里算**，模型一个数字都不许写。
这是"总结里每个数字都可核对"能成立的唯一前提。
"""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from psycopg import AsyncConnection


async def collect_stats(conn: AsyncConnection, start: date, end: date) -> dict:
    """一份总结所需的全部原始数字。

    每一项都能被 `verify.py` 拿去和正文里的占位符对齐，
    也都能被人工复核——这是"可验证"的具体含义。
    """
    cur = await conn.execute(
        """
        select
            count(*)::int                                                  as item_count,
            count(*) filter (where kind = 'idea')::int                      as idea_count,
            count(*) filter (where kind = 'todo')::int                      as todo_note_count,
            count(distinct date_trunc('day', captured_at))::int            as active_days
        from items
        where user_id = current_user_id()
          and deleted_at is null
          and captured_at >= %(start)s::date
          and captured_at < (%(end)s::date + 1)
        """,
        {"start": start, "end": end},
    )
    base = await cur.fetchone()

    cur = await conn.execute(
        """
        select tag, count(*)::int as n
        from items, unnest(tags) as tag
        where user_id = current_user_id()
          and deleted_at is null
          and captured_at >= %(start)s::date
          and captured_at < (%(end)s::date + 1)
        group by tag
        order by n desc, tag
        limit 8
        """,
        {"start": start, "end": end},
    )
    topics = await cur.fetchall()

    cur = await conn.execute(
        """
        select
            count(*)::int                                          as todo_total,
            count(*) filter (where status = 'done')::int            as todo_done,
            count(*) filter (where status = 'open')::int            as todo_open,
            count(*) filter (where status = 'open' and due_date < current_date)::int as todo_overdue
        from todos
        where user_id = current_user_id()
          and created_at >= %(start)s::date
          and created_at < (%(end)s::date + 1)
        """,
        {"start": start, "end": end},
    )
    todos = await cur.fetchone()

    cur = await conn.execute(
        """
        select count(*)::int as n
        from item_links l
        join items i on i.id = l.src_item_id
        where l.user_id = current_user_id()
          and i.deleted_at is null
          and l.created_at >= %(start)s::date
          and l.created_at < (%(end)s::date + 1)
        """,
        {"start": start, "end": end},
    )
    link_count = (await cur.fetchone())["n"]

    top_titles = await top_items(conn, start, end, limit=12)

    return {
        "itemCount": base["item_count"],
        "ideaCount": base["idea_count"],
        "activeDays": base["active_days"],
        "topTopics": [t["tag"] for t in topics],
        "topicCounts": {t["tag"]: t["n"] for t in topics},
        "todoTotal": todos["todo_total"],
        "todoDone": todos["todo_done"],
        "todoOpen": todos["todo_open"],
        "todoOverdue": todos["todo_overdue"],
        "linkCount": link_count,
        "topItems": top_titles,
    }


async def top_items(
    conn: AsyncConnection, start: date, end: date, limit: int = 12
) -> list[dict]:
    """该时段内"信息量最大"的记录，作为总结的叙述素材。

    排序依据是关联数 + 正文长度：被关联多的通常更重要，长文通常更有内容。
    这两个都是可解释的，不用模型判断重要性。
    """
    cur = await conn.execute(
        """
        select i.id, i.title, i.kind, i.analysis_version,
               left(i.body_md, 400) as excerpt,
               length(i.body_md) as body_len,
               (select count(*) from item_links l where l.src_item_id = i.id
                 or l.dst_item_id = i.id) as link_count
        from items i
        where i.user_id = current_user_id()
          and i.deleted_at is null
          and i.captured_at >= %(start)s::date
          and i.captured_at < (%(end)s::date + 1)
        order by link_count desc, body_len desc
        limit %(limit)s
        """,
        {"start": start, "end": end, "limit": limit},
    )
    return await cur.fetchall()


async def insert_digest(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    kind: str,
    period_start: date,
    period_end: date,
    content_md: str,
    stats: dict,
    action_items: list[dict],
    model: str | None,
    prompt_version: str,
    evidence: list[dict],
) -> UUID:
    """写总结 + 证据快照，同一事务。

    证据里存 `item_version`（当时的 analysis_version）。
    结论只对生成它的那个版本负责：记录后来被重新分析，总结会被触发器标记为过期。
    """
    cur = await conn.execute(
        """
        insert into digests (user_id, kind, period_start, period_end, content_md,
                             stats, action_items, model, prompt_version)
        values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        returning id
        """,
        (
            str(user_id),
            kind,
            period_start,
            period_end,
            content_md,
            json.dumps(stats, ensure_ascii=False, default=str),
            json.dumps(action_items, ensure_ascii=False, default=str),
            model,
            prompt_version,
        ),
    )
    digest_id = (await cur.fetchone())["id"]

    if evidence:
        rows = [
            (str(digest_id), str(e["item_id"]), int(e["item_version"]), e.get("quote"))
            for e in evidence
        ]
        async with conn.cursor() as c:
            await c.executemany(
                """
                insert into digest_evidence (digest_id, item_id, item_version, quote)
                values (%s, %s, %s, %s)
                on conflict (digest_id, item_id) do nothing
                """,
                rows,
            )
    return digest_id


async def get_digest(conn: AsyncConnection, digest_id: UUID) -> dict | None:
    cur = await conn.execute(
        """
        select id, kind, period_start, period_end, content_md, stats,
               action_items, stale, model, created_at
        from digests where id = %s
        """,
        (str(digest_id),),
    )
    return await cur.fetchone()


async def list_digests(conn: AsyncConnection, limit: int = 20) -> list[dict]:
    cur = await conn.execute(
        """
        select id, kind, period_start, period_end, stale, created_at,
               left(content_md, 200) as preview
        from digests
        where user_id = current_user_id()
        order by created_at desc
        limit %s
        """,
        (limit,),
    )
    return await cur.fetchall()


async def evidence_versions(conn: AsyncConnection, digest_id: UUID) -> list[dict]:
    cur = await conn.execute(
        """
        select e.item_id, e.item_version, i.analysis_version as current_version, i.title
        from digest_evidence e
        join items i on i.id = e.item_id
        where e.digest_id = %s
        """,
        (str(digest_id),),
    )
    return await cur.fetchall()
