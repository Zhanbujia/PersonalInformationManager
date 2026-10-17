"""阶段总结接口。

生成是异步的（一次要调一次强档模型并做多表统计，90 秒内完成），
所以 `POST /digests` 只返回 jobId，前端轮询 `GET /digests/jobs/{id}`。

为什么不做成同步：同步就要把 HTTP 连接挂 30~90 秒，
中间任何一次网络抖动都会让用户以为失败了。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.agents.digest.generate import default_period
from app.api.deps import AuthUser, current_user, get_db, get_worker_db
from app.errors import InvalidInput, NotFound
from app.infra.db import Database
from app.repo import digests as digest_repo
from app.repo import infra as infra_repo
from app.schemas.models import DigestCreateIn, DigestOut

router = APIRouter(prefix="/digests", tags=["digests"])


@router.post("", status_code=202)
async def create_digest(
    body: DigestCreateIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    if body.kind == "todo_review":
        raise InvalidInput("待办整理请用 GET /todos/grouped，不需要生成总结")

    start, end = (
        (body.period_start, body.period_end)
        if body.period_start and body.period_end
        else default_period(body.kind)
    )
    if not start or not end:
        raise InvalidInput("缺少时间范围")
    if start > end:
        raise InvalidInput("开始日期不能晚于结束日期")
    if (end - start).days > 400:
        raise InvalidInput("时间范围太长，请控制在一年以内")

    async with db.tx(auth.id) as conn:
        job_id = await infra_repo.enqueue(
            conn,
            kind="digest",
            payload={
                "userId": str(auth.id),
                "kind": body.kind,
                "periodStart": start.isoformat(),
                "periodEnd": end.isoformat(),
            },
        )
    return {"jobId": job_id, "digestId": None, "estimatedSeconds": 45}


@router.get("/jobs/{job_id}")
async def job_status(
    job_id: int,
    auth: AuthUser = Depends(current_user),
    worker_db: Database = Depends(get_worker_db),
) -> dict:
    """轮询任务状态。

    读 `jobs` 用的是 worker 连接：app 角色对 jobs 只有 insert 权限，
    这样它读不到别人投了什么任务。代价是任务状态要由 worker 角色来查，
    而这里只暴露状态与产出 id，不返回 payload 内容。
    """
    async with worker_db.system_tx() as conn:
        row = await infra_repo.job_progress(conn, job_id)
    if not row:
        raise NotFound("任务不存在")

    payload_user = None
    result = row.get("result") or {}
    if result:
        payload_user = result.get("userId")
    if payload_user and payload_user != str(auth.id):
        # 不是自己的任务，一律当作不存在
        raise NotFound("任务不存在")

    status = "done" if row["finished_at"] else ("failed" if row["attempts"] >= row["max_attempts"] else "running")
    return {
        "jobId": row["id"],
        "kind": row["kind"],
        "status": status,
        "digestId": result.get("digestId"),
        "error": row.get("last_error") if status == "failed" else None,
    }


@router.get("/{digest_id}", response_model=DigestOut)
async def get_digest(
    digest_id: UUID,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        row = await digest_repo.get_digest(conn, digest_id)
    if not row:
        raise NotFound("总结不存在")
    return _to_out(row)


@router.get("", response_model=list[DigestOut])
async def list_digests(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    async with db.tx(auth.id) as conn:
        rows = await digest_repo.list_digests(conn, limit=limit)
        full = []
        for r in rows:
            item = await digest_repo.get_digest(conn, r["id"])
            if item:
                full.append(_to_out(item))
    return full


def _to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "periodStart": row["period_start"],
        "periodEnd": row["period_end"],
        "contentMd": row["content_md"],
        "stats": row["stats"] or {},
        "actionItems": row.get("action_items") or [],
        "stale": row["stale"],
        "model": row.get("model"),
        "createdAt": row["created_at"],
    }


@router.get("/range/preview")
async def preview_range(kind: str = Query(default="weekly")) -> dict:
    """给前端算默认时间范围用，免得前后端各写一套日期逻辑。"""
    start, end = default_period(kind)
    return {"periodStart": start.isoformat(), "periodEnd": end.isoformat()}
