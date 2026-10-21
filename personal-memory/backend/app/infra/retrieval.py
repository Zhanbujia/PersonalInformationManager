"""检索：原生 SQL 的混合检索（向量 + 全文，RRF 融合）。

为什么不用 `langchain_postgres.PGVectorStore`：它会自己建表、自己开连接池，
连接不经过我们的 `SET LOCAL app.user_id`，也就绕开了行级安全策略。
对我们来说这是**安全边界**问题，不是性能问题。所以检索用几十行 SQL 自己写。

SQL 里刻意**没有** `where user_id = ?`：这是故意的，由 RLS 兜底。
多一道锁的意义就在于——即使有人漏写条件，数据也不会跨用户泄漏。

中文检索的两个关键点：
1. `chunks.search_text` 存的是 jieba 分词后的空格分隔文本，不是原文；
2. 查询串也同样先分词。两边都分词，`to_tsvector('simple', ...)` 才有意义。

RRF 的两个关键点（也是最容易写错的地方）：
1. 每一路的 `row_number()` 必须**在该路自己的排序里**算，不能放到 join 之后；
2. 融合分数是 `1/(k+rank)` 相加，某一路缺失就记 0。
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.config import settings
from app.infra.textproc import segment

_SELECT_HEAD = """
select coalesce(v.id, k.id)                    as chunk_id,
       coalesce(v.item_id, k.item_id)          as item_id,
       coalesce(v.text, k.text)                as text,
       coalesce(v.title, k.title)              as title,
       coalesce(v.captured_at, k.captured_at)  as captured_at,
       v.vec_score,
       k.kw_score,
       coalesce(1.0 / (%(rrf_k)s + v.vec_rank), 0)
     + coalesce(1.0 / (%(rrf_k)s + k.kw_rank), 0)  as rrf_score
from vec v
full outer join kw k on v.id = k.id
order by rrf_score desc, coalesce(v.vec_score, 0) desc
limit %(k)s
"""

_KW_CTE = """
kw as (
    select c.id, c.item_id, c.text, i.title, i.captured_at,
           ts_rank_cd(to_tsvector('simple', c.search_text),
                      plainto_tsquery('simple', %(kw)s))      as kw_score,
           row_number() over (
               order by ts_rank_cd(to_tsvector('simple', c.search_text),
                                   plainto_tsquery('simple', %(kw)s)) desc
           )                                                 as kw_rank
    from chunks c
    join items i on i.id = c.item_id
    where i.deleted_at is null
      and to_tsvector('simple', c.search_text) @@ plainto_tsquery('simple', %(kw)s)
      %(kw_filter)s
    limit %(pool)s
)
"""

VEC_AND_KW_SQL = (
    """
with vec as (
    select c.id, c.item_id, c.text, i.title, i.captured_at,
           1 - (c.embedding <=> %(vec)s::vector)          as vec_score,
           row_number() over (order by c.embedding <=> %(vec)s::vector) as vec_rank
    from chunks c
    join items i on i.id = c.item_id
    where c.embedding is not null
      and i.deleted_at is null
      %(vec_filter)s
    order by c.embedding <=> %(vec)s::vector
    limit %(pool)s
),
"""
    + _KW_CTE
    + _SELECT_HEAD
)

# 向量服务不可用时的降级形态：只有关键词那一路，向量列直接给 null
KW_ONLY_SQL = (
    """
with vec as (
    select null::uuid as id, null::uuid as item_id, null::text as text,
           null::text as title, null::timestamptz as captured_at,
           null::real as vec_score, null::bigint as vec_rank
    where false
),
"""
    + _KW_CTE
    + _SELECT_HEAD
)


def to_pgvector(vec: list[float]) -> str:
    """转成 pgvector 的字面量格式 '[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def build_filters(
    *,
    kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    alias: str = "i",
    params: dict[str, Any],
) -> str:
    """拼过滤条件。

    只允许白名单字段，值一律走参数绑定——不接受任何字符串拼接进来的用户输入。
    """
    clauses: list[str] = []
    if kind:
        params["f_kind"] = kind
        clauses.append(f"and {alias}.kind = %(f_kind)s")
    if date_from:
        params["f_from"] = date_from
        clauses.append(f"and {alias}.captured_at >= %(f_from)s::timestamptz")
    if date_to:
        params["f_to"] = date_to
        clauses.append(f"and {alias}.captured_at <= %(f_to)s::timestamptz")
    return "\n      ".join(clauses)


async def hybrid_search(
    conn: AsyncConnection,
    *,
    query_text: str,
    query_vec: list[float] | None,
    k: int | None = None,
    kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[dict], int]:
    """混合检索。返回 (命中列表, 耗时毫秒)。

    `query_vec` 为 None 时退化为纯关键词检索——向量服务挂了也要能搜。
    """
    started = time.perf_counter()
    k = k or settings.retrieval_k
    params: dict[str, Any] = {
        "k": k,
        "pool": k * settings.retrieval_pool_multiplier,
        "rrf_k": settings.rrf_k,
        # 两边都分词，索引与查询才对得上
        "kw": segment(query_text) or query_text,
    }
    filt = build_filters(kind=kind, date_from=date_from, date_to=date_to, params=params)

    if query_vec is None:
        sql = KW_ONLY_SQL
    else:
        params["vec"] = to_pgvector(query_vec)
        sql = VEC_AND_KW_SQL

    # 过滤片段必须**拼进 SQL 文本**，不能放进 params。
    # 它是 SQL 片段而不是值：一旦当成参数绑定，服务器收到的是一个孤立的 $n，
    # 报 "syntax error at or near $2"，而且过滤器为空时也一样会炸。
    # 安全性不受影响：片段内容只来自白名单列名和 %(f_*)s 占位符，
    # 真正的值（kind、日期）仍旧走参数绑定，用户输入进不了 SQL 文本。
    sql = sql.replace("%(vec_filter)s", filt).replace("%(kw_filter)s", filt)

    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    took_ms = int((time.perf_counter() - started) * 1000)
    return rows, took_ms


async def similar_items(
    conn: AsyncConnection,
    *,
    item_id: UUID,
    query_vec: list[float],
    threshold: float | None = None,
    top_n: int | None = None,
) -> list[dict]:
    """找与某条记录相似的**其它记录**（建关联用）。

    只做向量比对，不调模型——每次记录都调模型判关联，成本会失控。
    按 item 聚合取最高分，避免同一篇的多个切片刷榜。
    """
    threshold = threshold if threshold is not None else settings.link_similarity_threshold
    top_n = top_n or settings.link_top_n
    sql = """
    with scored as (
        select c.item_id,
               1 - (c.embedding <=> %(vec)s::vector) as score,
               c.text
        from chunks c
        join items i on i.id = c.item_id
        where c.embedding is not null
          and c.item_id <> %(item_id)s
          and i.deleted_at is null
        order by c.embedding <=> %(vec)s::vector
        limit 120
    ),
    best as (
        select distinct on (item_id) item_id, score, text
        from scored
        where score >= %(threshold)s
        order by item_id, score desc
    )
    select item_id, score, text from best order by score desc limit %(top_n)s
    """
    cur = await conn.execute(
        sql,
        {
            "vec": to_pgvector(query_vec),
            "item_id": str(item_id),
            "threshold": threshold,
            "top_n": top_n,
        },
    )
    return await cur.fetchall()
