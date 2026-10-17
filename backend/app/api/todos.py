"""待办接口。

一个容易被忽略的点：**分组、排序、计数都由 SQL 算，不交给模型**。
模型只参与"这句话是不是待办""它隐含的截止时间是什么"这类判断，
而这两件事在入库流水线里已经做完了（`todos_extracted`）。
所以这个接口完全是确定性的。
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthUser, current_user, get_db
from app.errors import NotFound
from app.infra.db import Database
from app.repo import todos as todos_repo
from app.schemas.models import TodoCreateIn, TodoGroupOut, TodoOut, TodoPatchIn

router = APIRouter(prefix="/todos", tags=["todos"])

_BUCKET_LABELS = {
    "today": "今天",
    "week": "本周",
    "later": "以后",
    "someday": "没有期限",
}


@router.get("", response_model=list[TodoOut])
async def list_todos(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    status: str | None = Query(default="open"),
    due_before: date | None = None,
) -> list[dict]:
    async with db.tx(auth.id) as conn:
        return await todos_repo.list_todos(conn, status=status, due_before=due_before)


@router.post("", response_model=TodoOut, status_code=201)
async def create_todo(
    body: TodoCreateIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    """新建待办。

    带 `clientId` 时是幂等的：连点两次只会有一条。
    这个约束在数据库层（`unique(user_id, client_id)`），不靠前端防抖。
    """
    async with db.tx(auth.id) as conn:
        return await todos_repo.create_todo(
            conn,
            user_id=auth.id,
            text=body.text.strip(),
            due_date=body.due_date,
            priority=body.priority,
            source_item_id=body.source_item_id,
            source_quote=None,
            client_id=body.client_id,
        )


@router.patch("/{todo_id}", response_model=TodoOut)
async def patch_todo(
    todo_id: UUID,
    body: TodoPatchIn,
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> dict:
    async with db.tx(auth.id) as conn:
        row = await todos_repo.patch_todo(
            conn,
            todo_id,
            text=body.text,
            status=body.status,
            due_date=body.due_date,
            priority=body.priority,
        )
    if not row:
        raise NotFound("待办不存在")
    return row


@router.get("/grouped", response_model=list[TodoGroupOut])
async def grouped(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
) -> list[dict]:
    """待办整理：今天 / 本周 / 以后 / 没有期限。

    分组边界由 SQL 的 `current_date` 决定，容器时区设成 Asia/Shanghai 即为北京时间。
    """
    async with db.tx(auth.id) as conn:
        rows = await todos_repo.group_for_review(conn)

    buckets: dict[str, list[dict]] = {k: [] for k in _BUCKET_LABELS}
    for r in rows:
        buckets.setdefault(r["bucket"], []).append(
            {
                "id": r["id"],
                "text": r["text"],
                "status": r["status"],
                "dueDate": r["due_date"],
                "priority": r["priority"],
                "sourceItemId": r["source_item_id"],
                "createdAt": r["created_at"],
                "doneAt": r["done_at"],
            }
        )
    return [
        {"bucket": k, "label": label, "todos": buckets.get(k, [])}
        for k, label in _BUCKET_LABELS.items()
    ]
