"""步骤 4：建关联。

只做向量比对，**不调模型**。理由很直接：每记一条都让模型判断"它和哪条相关"，
一天 30 条就是 30 次额外调用，成本会失控，而收益很小——0.75 以上的余弦相似度
已经足够覆盖"确实相关"的情形。真正需要模型裁决的疑难关联，每周批量跑一次就够。
"""

from __future__ import annotations

import logging

from psycopg import AsyncConnection

from app.config import settings
from app.infra import retrieval
from app.repo import records

logger = logging.getLogger(__name__)


def mean_vector(vectors: list[list[float]]) -> list[float] | None:
    """把多个切片向量平均成一个"整篇向量"。

    取平均而不是取第一个：一篇长文的主题通常分散在多个切片里，
    只拿首片会让"后半段讲的东西"关联不上。
    """
    vecs = [v for v in vectors if v]
    if not vecs:
        return None
    dim = len(vecs[0])
    n = len(vecs)
    return [sum(v[i] for v in vecs) / n for i in range(dim)]


async def link_item(
    conn: AsyncConnection, *, item_id, user_id
) -> list[dict]:
    """找出相似记录并写 `item_links`。返回写入的关联（供测试断言）。"""
    rows = await records.list_chunks(conn, item_id)
    if not rows:
        return []

    # 从数据库读回向量（避免整条链路都把向量传在内存里）
    cur = await conn.execute(
        "select embedding::text as vec from chunks where item_id = %s and embedding is not null",
        (str(item_id),),
    )
    vec_texts = await cur.fetchall()
    if not vec_texts:
        return []

    vectors: list[list[float]] = []
    for r in vec_texts:
        raw = r["vec"].strip("[]")
        vectors.append([float(x) for x in raw.split(",") if x])

    center = mean_vector(vectors)
    if center is None:
        return []

    candidates = await retrieval.similar_items(
        conn,
        item_id=item_id,
        query_vec=center,
        threshold=settings.link_similarity_threshold,
        top_n=settings.link_top_n,
    )
    if not candidates:
        return []

    links = [
        {
            "item_id": c["item_id"],
            "relation": "similar",
            "score": round(float(c["score"]), 4),
            "reason": None,
        }
        for c in candidates
    ]
    await records.upsert_links(conn, user_id=user_id, src_item_id=item_id, links=links)
    logger.info("建关联 item=%s links=%s", item_id, len(links))
    return links
