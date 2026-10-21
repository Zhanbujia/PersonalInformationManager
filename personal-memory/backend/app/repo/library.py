"""资料库：文件夹与文件列表的数据访问。

资料库只认 `kind='file'` 的记录；文件夹一级、不嵌套。
所有函数接收一个已经 `SET LOCAL app.user_id` 的连接，**不再手写 user_id 条件**（RLS 兜底）。

列表只返回轻量投影（正文只取前 240 字作摘要），全文预览走 `GET /items/{id}`，
避免一次列表把几十篇大文档的正文全拉出来。
"""

from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection

# 列表投影：正文只取前缀，避免大字段拖慢列表
LIST_COLUMNS = """
    id, title, file_name, mime_type, file_size, file_ext, parse_error,
    analysis_status, analysis_version, user_pinned, collection_id,
    captured_at, updated_at, left(body_md, 240) as excerpt
"""


async def list_collections(conn: AsyncConnection) -> list[dict]:
    cur = await conn.execute(
        """
        select c.id, c.name, c.created_at,
               (select count(*) from items i
                 where i.collection_id = c.id
                   and i.deleted_at is null
                   and i.kind = 'file') as file_count
        from collections c
        order by c.created_at asc
        """
    )
    return await cur.fetchall()


async def create_collection(conn: AsyncConnection, *, user_id: UUID, name: str) -> dict | None:
    """同名（忽略大小写）时返回 None。"""
    cur = await conn.execute(
        """
        insert into collections (user_id, name) values (%s, %s)
        on conflict do nothing
        returning id, name, created_at
        """,
        (str(user_id), name),
    )
    return await cur.fetchone()


async def rename_collection(conn: AsyncConnection, collection_id: UUID, name: str) -> dict | None:
    cur = await conn.execute(
        """
        update collections set name = %s, updated_at = now()
        where id = %s
        returning id, name, created_at
        """,
        (name, str(collection_id)),
    )
    return await cur.fetchone()


async def delete_collection(conn: AsyncConnection, collection_id: UUID) -> bool:
    """删文件夹前把文件挪回「未分类」，文件本身不删。"""
    await conn.execute(
        "update items set collection_id = null, updated_at = now() where collection_id = %s",
        (str(collection_id),),
    )
    cur = await conn.execute("delete from collections where id = %s", (str(collection_id),))
    return cur.rowcount > 0


async def uncategorized_count(conn: AsyncConnection) -> int:
    cur = await conn.execute(
        "select count(*) as n from items "
        "where deleted_at is null and kind = 'file' and collection_id is null"
    )
    return (await cur.fetchone())["n"]


async def list_files(
    conn: AsyncConnection,
    *,
    collection_id: str | None,
    uncategorized: bool,
    q: str | None,
    ext: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    where = ["deleted_at is null", "kind = 'file'"]
    params: dict = {}

    if uncategorized:
        where.append("collection_id is null")
    elif collection_id:
        where.append("collection_id = %(cid)s")
        params["cid"] = collection_id

    if ext:
        ext_norm = ext if ext.startswith(".") else f".{ext}"
        where.append("file_ext = %(ext)s")
        params["ext"] = ext_norm.lower()

    if q:
        where.append("(title ilike %(like)s or file_name ilike %(like)s or body_md ilike %(like)s)")
        params["like"] = f"%{q}%"

    clause = " and ".join(where)

    cur = await conn.execute(f"select count(*) as n from items where {clause}", params)
    total = (await cur.fetchone())["n"]

    params["limit"] = limit
    params["offset"] = offset
    cur = await conn.execute(
        f"""
        select {LIST_COLUMNS}
        from items
        where {clause}
        order by captured_at desc
        limit %(limit)s offset %(offset)s
        """,
        params,
    )
    return await cur.fetchall(), total


async def move_file(
    conn: AsyncConnection, item_id: UUID, collection_id: UUID | None
) -> bool:
    """移动文件到文件夹；`None` 表示挪回未分类。"""
    cur = await conn.execute(
        """
        update items set collection_id = %s, updated_at = now()
        where id = %s and kind = 'file' and deleted_at is null
        """,
        (None if collection_id is None else str(collection_id), str(item_id)),
    )
    return cur.rowcount > 0
