"""片段级检索（给"搜一下"用，不是给 Agent 用）。

和 `/items?q=` 的区别：
- `/items?q=` 搜的是**整篇**（`items.search_text`），返回记录列表，用于列表页筛选；
- `/search` 搜的是**切片**（`chunks.search_text` + 向量），返回带上下文的片段，
  用于"我记得说过类似这么一句话"这种模糊定位。

两者共用同一套分词与 RRF 融合逻辑，但索引不同、粒度不同。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuthUser, current_user, get_db, get_embeddings
from app.errors import InvalidInput
from app.infra import retrieval
from app.infra.db import Database
from app.infra.llm import Embeddings

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    q: str = Query(min_length=1, max_length=300),
    auth: AuthUser = Depends(current_user),
    db: Database = Depends(get_db),
    embeddings: Embeddings = Depends(get_embeddings),
    k: int = Query(default=8, ge=1, le=30),
    kind: str | None = None,
) -> dict:
    query = q.strip()
    if not query:
        raise InvalidInput("搜索词不能为空")

    vec = None
    try:
        vec = await embeddings.embed_one(query)
    except Exception:  # noqa: BLE001 - 向量服务不可用就退化为纯关键词
        vec = None

    async with db.tx(auth.id) as conn:
        rows, took_ms = await retrieval.hybrid_search(
            conn, query_text=query, query_vec=vec, k=k, kind=kind
        )

    return {
        "hits": [
            {
                "chunkId": r["chunk_id"],
                "itemId": r["item_id"],
                "title": r.get("title"),
                "text": r["text"],
                "vecScore": float(r["vec_score"]) if r["vec_score"] is not None else None,
                "kwScore": float(r["kw_score"]) if r["kw_score"] is not None else None,
                "rrfScore": float(r["rrf_score"]),
                "capturedAt": r["captured_at"],
            }
            for r in rows
        ],
        "tookMs": took_ms,
        "degraded": vec is None,
    }
