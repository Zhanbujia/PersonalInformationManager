"""记录接口。

新建记录这件事必须"提交即返回"——用户按下回车就该看到东西进列表，
不能等模型分析完。所以这里只做三件很快的事：规范化、落库、投递任务（同一事务）。

分析在后台 worker 里做，前端轮询 `/items/{id}/status` 或者直接看列表里的状态标记。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthUser, current_user, get_db
from app.errors import DuplicateContent, NotFound
from app.infra.db import Database
from app.infra.textproc import content_hash, extract_url, normalize, segment
from app.repo import infra as infra_repo
from app.repo import records as records_repo
from app.schemas.models import (
    ItemCreatedOut,
    ItemCreateIn,
    ItemOut,
    ItemPatchIn,
    ItemStatusOut,
    Page,
)

router = APIRouter(prefix="/items", tags=["items"])


def _to_item_out(item: dict) -> dict:
    analysis = item.get("analysis")
    return {
        "id": item["id"],
        "kind": item["kind"],
        "title": item.get("title"),
        "bodyMd": item["body_md"],
        "rawInput": item["raw_input"],
        "sourceChannel": item["source_channel"],
        "sourceUrl": item.get("source_url"),
        "tags": list(item.get("tags") or []),
        "capturedAt": item["captured_at"],
        "updatedAt": item["updated_at"],
        "analysisStatus": item["analysis_status"],
        "analysisVersion": item["analysis_version"],
        "userPinned": item["user_pinned"],
        "analysis": (
            {
                "summary": analysis["summary"],
                "keyPoints": analysis["key_points"],
                "todosExtracted": analysis["todos_extracted"],
                "confidence": analysis["confidence"],
                "model": analysis["model"],
                "version": analysis["version"],
                "createdAt": analysis["created_at"],
            }
            if analysis
            else None
        ),
        "links": item.get("links") or [],
    }


@router.post("", response_model=ItemCreatedOut, status_code=201)
async def create_item(
    body: ItemCreateIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    body_md = normalize(body.raw_input)
    text_hash = content_hash(body_md)
    url = body.source_url or extract_url(body_md)

    async with db.tx(auth.id) as conn:
        created, duplicate = await records_repo.create_item(
            conn,
            user_id=auth.id,
            kind=body.kind,
            raw_input=body.raw_input,
            body_md=body_md,
            content_hash=text_hash,
            search_text=segment(body_md),
            source_channel=body.source_channel,
            source_url=url,
            title=body.title,
        )
        if duplicate is not None:
            raise DuplicateContent(
                "这条内容之前已经记过了",
                detail={"duplicateOf": str(duplicate["id"])},
            )
        assert created is not None

        # 投递任务和落库在同一个事务里：不会出现"记录存了但永远不分析"
        await infra_repo.enqueue(
            conn,
            kind="ingest",
            payload={"itemId": str(created["id"]), "userId": str(auth.id)},
        )

    return {
        "id": created["id"],
        "analysisStatus": created["analysis_status"],
        "capturedAt": created["captured_at"],
        "duplicate": False,
    }


@router.get("", response_model=Page[ItemOut])
async def list_items(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    kind: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    status: str | None = None,
) -> dict:
    async with db.tx(auth.id) as conn:
        rows, total = await records_repo.list_items(
            conn, limit=limit, offset=offset, kind=kind, tag=tag, q=q, status=status
        )
    return {
        "items": [_to_item_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{item_id}", response_model=ItemOut)
async def get_item(
    item_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        item = await records_repo.get_item(conn, item_id)
        if not item:
            raise NotFound("记录不存在或已删除")
        item["links"] = await records_repo.get_links(conn, item_id)
    return _to_item_out(item)


@router.patch("/{item_id}", response_model=ItemOut)
async def patch_item(
    item_id: UUID,
    body: ItemPatchIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    normalized = normalize(body.body_md) if body.body_md is not None else None
    async with db.tx(auth.id) as conn:
        item = await records_repo.patch_item(
            conn,
            item_id,
            title=body.title,
            body_md=normalized,
            kind=body.kind,
            tags=body.tags,
            body_search_text=segment(normalized) if normalized is not None else None,
            body_hash=content_hash(normalized) if normalized is not None else None,
        )
        if not item:
            raise NotFound("记录不存在或已删除")
        item["links"] = await records_repo.get_links(conn, item_id)
    return _to_item_out(item)


@router.delete("/{item_id}", status_code=204)
async def delete_item(
    item_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> None:
    """软删除。

    原文留在库里（`deleted_at` 非空），但所有查询都带 `deleted_at is null`，
    用户视角就是删掉了。这样做是为了给"误删"留一条后路。
    真要硬删走 `scripts/purge.py`，那是显式动作。
    """
    async with db.tx(auth.id) as conn:
        ok = await records_repo.soft_delete_item(conn, item_id)
    if not ok:
        raise NotFound("记录不存在或已删除")


@router.get("/{item_id}/status", response_model=ItemStatusOut)
async def item_status(
    item_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        cur = await conn.execute(
            "select id, analysis_status, analysis_version from items "
            "where id = %s and deleted_at is null",
            (str(item_id),),
        )
        row = await cur.fetchone()
    if not row:
        raise NotFound("记录不存在或已删除")
    return {
        "id": row["id"],
        "analysisStatus": row["analysis_status"],
        "analysisVersion": row["analysis_version"],
        "errorCode": None,
    }


@router.post("/{item_id}/reanalyze", response_model=ItemStatusOut, status_code=202)
async def reanalyze(
    item_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    """重新分析。用户看到"分析失败"时点那个按钮就是走这里。"""
    async with db.tx(auth.id) as conn:
        item = await records_repo.get_item(conn, item_id, with_analysis=False)
        if not item:
            raise NotFound("记录不存在或已删除")
        await records_repo.set_analysis_status(conn, item_id, "pending")
        await infra_repo.enqueue(
            conn,
            kind="ingest",
            payload={"itemId": str(item_id), "userId": str(auth.id)},
        )
    return {
        "id": item_id,
        "analysisStatus": "pending",
        "analysisVersion": item["analysis_version"],
        "errorCode": None,
    }
