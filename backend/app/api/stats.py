"""用量与健康检查。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from app.api.deps import AuthUser, current_user, get_db
from app.config import settings
from app.infra.db import Database
from app.repo import infra as infra_repo

router = APIRouter(tags=["stats"])


@router.get("/stats/usage")
async def usage(
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    month: str | None = None,
) -> dict:
    month = month or date.today().strftime("%Y-%m")
    async with db.tx(auth.id) as conn:
        rows = await infra_repo.usage_of_month(conn, auth.id, month)

    total_in = sum(r["tokens_in"] for r in rows)
    total_out = sum(r["tokens_out"] for r in rows)
    total_cached = sum(r["cached_tokens"] for r in rows)

    return {
        "month": month,
        "totalIn": total_in,
        "totalOut": total_out,
        "totalCached": total_cached,
        "quota": settings.daily_token_quota,
        "byPath": [
            {
                "day": r["day"],
                "path": r["path"],
                "model": r["model"],
                "tokensIn": r["tokens_in"],
                "tokensOut": r["tokens_out"],
                "cachedTokens": r["cached_tokens"],
                "calls": r["calls"],
            }
            for r in rows
        ],
    }
