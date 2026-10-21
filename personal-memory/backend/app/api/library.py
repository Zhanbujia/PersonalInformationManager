"""资料库接口：文件夹 + 文件列表。

文件本身仍走 `/items` 的上传 / 详情 / 下载 / 删除，这里只补两件事：
文件夹的增删改查，以及按文件夹/关键词/类型筛选文件列表。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.api.deps import AuthUser, current_user, get_db
from app.errors import DuplicateContent, InvalidInput, NotFound
from app.infra.db import Database
from app.repo import library as library_repo
from app.schemas.models import ApiModel

router = APIRouter(prefix="/library", tags=["library"])


class CollectionIn(ApiModel):
    name: str = Field(min_length=1, max_length=80)


class MoveIn(ApiModel):
    collection_id: UUID | None = None


def _file_out(r: dict) -> dict:
    return {
        "id": r["id"],
        "title": r.get("title"),
        "fileName": r.get("file_name"),
        "mimeType": r.get("mime_type"),
        "fileSize": r.get("file_size"),
        "fileExt": r.get("file_ext"),
        "parseError": r.get("parse_error"),
        "analysisStatus": r["analysis_status"],
        "analysisVersion": r["analysis_version"],
        "collectionId": r.get("collection_id"),
        "capturedAt": r["captured_at"],
        "updatedAt": r["updated_at"],
        "excerpt": r.get("excerpt") or "",
    }


@router.get("/collections")
async def list_collections(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        rows = await library_repo.list_collections(conn)
        uncategorized = await library_repo.uncategorized_count(conn)
    collections = [
        {
            "id": r["id"],
            "name": r["name"],
            "fileCount": int(r["file_count"]),
            "createdAt": r["created_at"],
        }
        for r in rows
    ]
    total = uncategorized + sum(c["fileCount"] for c in collections)
    return {"collections": collections, "uncategorized": uncategorized, "total": total}


@router.post("/collections", status_code=201)
async def create_collection(
    body: CollectionIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    name = body.name.strip()
    if not name:
        raise InvalidInput("文件夹名不能为空")
    async with db.tx(auth.id) as conn:
        row = await library_repo.create_collection(conn, user_id=auth.id, name=name)
    if row is None:
        raise DuplicateContent("同名文件夹已存在")
    return {"id": row["id"], "name": row["name"], "fileCount": 0, "createdAt": row["created_at"]}


@router.patch("/collections/{collection_id}")
async def rename_collection(
    collection_id: UUID,
    body: CollectionIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    name = body.name.strip()
    if not name:
        raise InvalidInput("文件夹名不能为空")
    async with db.tx(auth.id) as conn:
        cur = await conn.execute(
            "select 1 from collections where lower(name) = lower(%s) and id <> %s",
            (name, str(collection_id)),
        )
        if await cur.fetchone():
            raise DuplicateContent("同名文件夹已存在")
        row = await library_repo.rename_collection(conn, collection_id, name)
    if row is None:
        raise NotFound("文件夹不存在")
    return {"id": row["id"], "name": row["name"], "createdAt": row["created_at"]}


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> None:
    async with db.tx(auth.id) as conn:
        ok = await library_repo.delete_collection(conn, collection_id)
    if not ok:
        raise NotFound("文件夹不存在")


@router.get("/files")
async def list_files(
    collection_id: UUID | None = Query(default=None, alias="collectionId"),
    uncategorized: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=100),
    ext: str | None = Query(default=None, max_length=12),
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        rows, total = await library_repo.list_files(
            conn,
            collection_id=str(collection_id) if collection_id else None,
            uncategorized=uncategorized,
            q=(q or "").strip() or None,
            ext=(ext or "").strip() or None,
            limit=limit,
            offset=offset,
        )
    return {
        "items": [_file_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/files/{item_id}")
async def move_file(
    item_id: UUID,
    body: MoveIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        if body.collection_id is not None:
            cur = await conn.execute(
                "select 1 from collections where id = %s", (str(body.collection_id),)
            )
            if not await cur.fetchone():
                raise InvalidInput("文件夹不存在")
        ok = await library_repo.move_file(conn, item_id, body.collection_id)
    if not ok:
        raise NotFound("文件不存在")
    return {"ok": True}
