"""待办数据访问。

两处容易出错的地方：
1. **幂等**：前端生成 `client_id`，唯一约束在 `(user_id, client_id)`。
   连点两次只会建一条。注意 `client_id` 可为 null，而 PostgreSQL 的唯一约束
   允许多个 null——手动建的待办不受影响。
2. **分组排序不放模型里**：今天/本周/以后/无期限是确定逻辑，用 SQL 的 case 算，
   让模型去算这个只会引入不确定性。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

_COLUMNS = "id, text, status, due_date, priority, source_item_id, created_at, done_at"


async def create_todo(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    text: str,
    due_date: date | None,
    priority: int,
    source_item_id: UUID | None,
    source_quote: str | None,
    client_id: str | None,
) -> dict:
    cur = await conn.execute(
        f"""
        insert into todos (user_id, text, due_date, priority, source_item_id, source_quote, client_id)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (user_id, client_id) do nothing
        returning {_COLUMNS}
        """,
        (
            str(user_id),
            text,
            due_date,
            priority,
            None if source_item_id is None else str(source_item_id),
            source_quote,
            client_id,
        ),
    )
    row = await cur.fetchone()
    if row:
        return row

    # 命中幂等键：返回已存在的那条，而不是报错
    cur = await conn.execute(
        f"select {_COLUMNS} from todos where user_id = %s and client_id = %s",
        (str(user_id), client_id),
    )
    existing = await cur.fetchone()
    if existing is None:  # 理论上不会发生，保底不返回 None
        raise RuntimeError("创建待办失败：既没有插入，也找不到已存在的记录")
    return existing


async def list_todos(
    conn: AsyncConnection,
    *,
    status: str | None = "open",
    due_before: date | None = None,
    limit: int = 200,
) -> list[dict]:
    where = ["user_id = current_user_id()"]
    params: dict[str, Any] = {"limit": limit}
    if status:
        where.append("status = %(status)s")
        params["status"] = status
    if due_before:
        where.append("due_date is not null and due_date <= %(due_before)s")
        params["due_before"] = due_before

    cur = await conn.execute(
        f"""
        select {_COLUMNS} from todos
        where {" and ".join(where)}
        order by (status = 'open') desc, priority asc, due_date asc nulls last, created_at desc
        limit %(limit)s
        """,
        params,
    )
    return await cur.fetchall()


async def patch_todo(
    conn: AsyncConnection,
    todo_id: UUID,
    *,
    text: str | None = None,
    status: str | None = None,
    due_date: date | None = None,
    priority: int | None = None,
) -> dict | None:
    sets: list[str] = []
    params: dict[str, Any] = {"id": str(todo_id)}

    if text is not None:
        sets.append("text = %(text)s")
        params["text"] = text
    if status is not None:
        sets.append("status = %(status)s")
        params["status"] = status
        # done_at 与状态联动：只有 done 才有完成时间
        sets.append("done_at = case when %(status)s = 'done' then now() else null end")
    if due_date is not None:
        sets.append("due_date = %(due)s")
        params["due"] = due_date
    if priority is not None:
        sets.append("priority = %(priority)s")
        params["priority"] = priority

    if not sets:
        cur = await conn.execute(f"select {_COLUMNS} from todos where id = %(id)s", params)
        return await cur.fetchone()

    cur = await conn.execute(
        f"update todos set {', '.join(sets)} where id = %(id)s returning {_COLUMNS}",
        params,
    )
    return await cur.fetchone()


async def group_for_review(conn: AsyncConnection) -> list[dict]:
    """待办整理：按 今天 / 本周 / 以后 / 无期限 分组。

    分组边界由 SQL 算，不交给模型。`current_date` 用数据库时区，
    部署时把容器 TZ 设成 Asia/Shanghai 即可。
    """
    cur = await conn.execute(
        """
        select id, text, status, due_date, priority, source_item_id, created_at, done_at,
               case
                   when due_date is null then 'someday'
                   when due_date <= current_date then 'today'
                   when due_date <= current_date + 7 then 'week'
                   else 'later'
               end as bucket
        from todos
        where user_id = current_user_id() and status = 'open'
        order by priority asc, due_date asc nulls last, created_at desc
        limit 500
        """
    )
    return await cur.fetchall()


async def stats(conn: AsyncConnection, start: date, end: date) -> dict:
    """阶段总结用的待办统计，全部由 SQL 算。"""
    cur = await conn.execute(
        """
        select
            count(*) as total,
            count(*) filter (where status = 'done') as done,
            count(*) filter (where status = 'open') as open,
            count(*) filter (where status = 'open' and due_date < current_date) as overdue
        from todos
        where user_id = current_user_id()
          and created_at >= %(start)s::date
          and created_at < (%(end)s::date + 1)
        """,
        {"start": start, "end": end},
    )
    row = await cur.fetchone()
    return {k: int(v or 0) for k, v in row.items()}
