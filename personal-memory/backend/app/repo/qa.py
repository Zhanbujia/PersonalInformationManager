"""问答会话与消息。"""

from __future__ import annotations

import json
from uuid import UUID

from psycopg import AsyncConnection


async def create_session(conn: AsyncConnection, *, user_id: UUID, title: str) -> UUID:
    cur = await conn.execute(
        "insert into qa_sessions (user_id, title) values (%s, %s) returning id",
        (str(user_id), title[:80]),
    )
    return (await cur.fetchone())["id"]


async def get_session(conn: AsyncConnection, session_id: UUID) -> dict | None:
    cur = await conn.execute(
        "select id, title, created_at, updated_at from qa_sessions where id = %s",
        (str(session_id),),
    )
    return await cur.fetchone()


async def touch_session(conn: AsyncConnection, session_id: UUID) -> None:
    await conn.execute(
        "update qa_sessions set updated_at = now() where id = %s", (str(session_id),)
    )


async def delete_session(conn: AsyncConnection, session_id: UUID) -> bool:
    cur = await conn.execute("delete from qa_sessions where id = %s", (str(session_id),))
    return cur.rowcount > 0


async def rename_session(conn: AsyncConnection, session_id: UUID, title: str) -> dict | None:
    cur = await conn.execute(
        """
        update qa_sessions set title = %s, updated_at = now()
        where id = %s
        returning id, title
        """,
        (title[:80], str(session_id)),
    )
    return await cur.fetchone()


async def add_message(
    conn: AsyncConnection,
    *,
    session_id: UUID,
    user_id: UUID,
    role: str,
    content: str,
    citations: list[dict] | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cached_tokens: int | None = None,
    model: str | None = None,
    attachments: list[dict] | None = None,
) -> UUID:
    cur = await conn.execute(
        """
        insert into qa_messages
            (session_id, user_id, role, content, citations, tokens_in, tokens_out,
             cached_tokens, model, attachments)
        values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
        returning id
        """,
        (
            str(session_id),
            str(user_id),
            role,
            content,
            json.dumps(citations or [], ensure_ascii=False),
            tokens_in,
            tokens_out,
            cached_tokens,
            model,
            json.dumps(attachments or [], ensure_ascii=False),
        ),
    )
    return (await cur.fetchone())["id"]


async def list_messages(conn: AsyncConnection, session_id: UUID, limit: int = 100) -> list[dict]:
    cur = await conn.execute(
        """
        select id, role, content, citations, attachments, model, created_at
        from qa_messages
        where session_id = %s
        order by created_at asc
        limit %s
        """,
        (str(session_id), limit),
    )
    return await cur.fetchall()


async def list_sessions(conn: AsyncConnection, limit: int = 50) -> list[dict]:
    cur = await conn.execute(
        """
        select s.id, s.title, s.created_at, s.updated_at,
               (select count(*) from qa_messages m where m.session_id = s.id) as message_count
        from qa_sessions s
        where s.user_id = current_user_id()
        order by s.updated_at desc
        limit %s
        """,
        (limit,),
    )
    return await cur.fetchall()


async def recent_history(conn: AsyncConnection, session_id: UUID, turns: int = 4) -> list[dict]:
    """取最近若干轮对话，用于把上下文接上。

    只取 user/assistant，tool 消息不进（那是执行细节，重放会干扰模型）。
    """
    cur = await conn.execute(
        """
        select role, content from (
            select role, content, created_at from qa_messages
            where session_id = %s and role in ('user','assistant')
            order by created_at desc
            limit %s
        ) t order by created_at asc
        """,
        (str(session_id), turns * 2),
    )
    return await cur.fetchall()
