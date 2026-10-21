"""问答附件的数据访问。

附件独立于 items：用户问问题时临时上传的文件，不该出现在收件箱/资料库里。
"""

from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection

_COLUMNS = (
    "id, user_id, session_id, file_name, mime_type, file_size, storage_key, "
    "ext, kind, extracted_text, status, error, created_at"
)


async def create_attachment(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    file_name: str,
    mime_type: str | None,
    file_size: int | None,
    storage_key: str,
    ext: str | None,
    kind: str,
    extracted_text: str | None,
    status: str = "ready",
    error: str | None = None,
) -> dict:
    cur = await conn.execute(
        """
        insert into chat_attachments
            (user_id, file_name, mime_type, file_size, storage_key, ext, kind,
             extracted_text, status, error)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning id, file_name, mime_type, file_size, kind, status, error
        """,
        (
            str(user_id),
            file_name,
            mime_type,
            file_size,
            storage_key,
            ext,
            kind,
            extracted_text,
            status,
            error,
        ),
    )
    return await cur.fetchone()


async def get_attachment(conn: AsyncConnection, attachment_id: UUID) -> dict | None:
    cur = await conn.execute(
        f"select {_COLUMNS} from chat_attachments where id = %s", (str(attachment_id),)
    )
    return await cur.fetchone()


async def get_many(conn: AsyncConnection, ids: list[UUID]) -> list[dict]:
    if not ids:
        return []
    cur = await conn.execute(
        f"select {_COLUMNS} from chat_attachments where id = any(%s::uuid[])",
        ([str(i) for i in ids],),
    )
    return await cur.fetchall()


async def attach_to_session(
    conn: AsyncConnection, ids: list[UUID], session_id: UUID
) -> None:
    if not ids:
        return
    await conn.execute(
        "update chat_attachments set session_id = %s where id = any(%s::uuid[])",
        (str(session_id), [str(i) for i in ids]),
    )


async def delete_attachment(conn: AsyncConnection, attachment_id: UUID) -> bool:
    cur = await conn.execute(
        "delete from chat_attachments where id = %s", (str(attachment_id),)
    )
    return cur.rowcount > 0
