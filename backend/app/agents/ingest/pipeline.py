"""路径一：记录入库的四步流水线。

**这里没有 Agent，也没有循环。** 四步是固定的：规范化 → 分析 → 向量化 → 建关联。
每一步做什么、失败怎么办，都是写死的，不存在"模型决定下一步"。

为什么这样设计：一条记录的处理成本必须低、延迟必须可预测。
把它塞进一个 agent 循环，会让"记一句话"变成"一次需要 2~4 秒的推理任务"，
而且同一段话每次分类结果可能不同。

每一步都单独开事务，**不在 LLM 调用期间持有数据库事务**——
一次模型调用要几秒，持有事务会占着连接不放。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import UUID

from app.agents.ingest.embed import build_chunks
from app.agents.ingest.extract import Extractor
from app.agents.ingest.link import link_item
from app.agents.ingest.prompt import INGEST_VERSION
from app.config import settings
from app.infra.db import Database
from app.infra.textproc import content_hash, normalize, segment, truncate_for_analysis
from app.repo import infra as infra_repo
from app.repo import records

logger = logging.getLogger(__name__)


@dataclass
class IngestContext:
    db: Database
    extractor: Extractor
    embeddings: object


def _idem(user_id: UUID, step: str, key: str) -> str:
    """幂等键必须带 user_id：`runs` 的唯一约束是 (kind, idem_key)，
    不同用户记了同样一段话，内容哈希会撞在一起。"""
    return f"ingest:{user_id}:{step}:{key}"


async def run_ingest(ctx: IngestContext, *, user_id: UUID, item_id: UUID) -> dict:
    """执行整条流水线。返回摘要（供日志与测试用）。

    失败处理：任何一步失败都把 `analysis_status` 置为 failed 并记录 error_code，
    **但原文一定还在**。前端显示"分析失败，点击重试"，用户点一下就能重跑。
    """
    started = time.perf_counter()
    stats: dict = {"item_id": str(item_id), "steps": {}}

    # ---------- 步骤 1：规范化 ----------
    async with ctx.db.tx(user_id) as conn:
        item = await records.get_item_for_worker(conn, item_id)
        if item is None:
            logger.warning("记录不存在或已删除 item=%s", item_id)
            return {"skipped": "not_found"}

        body = normalize(item["body_md"] or item["raw_input"])
        if not body:
            await records.set_analysis_status(conn, item_id, "skipped")
            return {"skipped": "empty"}

        text_hash = content_hash(body)
        if body != item["body_md"]:
            # 规范化结果与库里不一致（比如老数据），修一次
            cur = await conn.execute(
                "update items set body_md = %s, search_text = %s, content_hash = %s where id = %s",
                (body, segment(body), text_hash, str(item_id)),
            )
            if cur.rowcount == 0:
                logger.error("规范化回写命中 0 行 item=%s（可能是 RLS 未设置 user_id）", item_id)
                raise RuntimeError("规范化回写失败：影响行数为 0")

        await records.set_analysis_status(conn, item_id, "running")
    stats["steps"]["normalize"] = "ok"

    # ---------- 步骤 2：分析（调模型）----------
    analyze_text = truncate_for_analysis(
        body, settings.analyze_head_chars, settings.analyze_tail_chars
    )
    try:
        async with ctx.db.tx(user_id) as conn:
            run = await infra_repo.start_run(
                conn,
                user_id=user_id,
                kind="ingest",
                idem_key=_idem(user_id, "analyze", text_hash),
                step="analyze",
                input_hash=text_hash,
            )
            reuse = run is None
        if reuse:
            logger.info("分析结果复用（幂等命中） item=%s", item_id)
            stats["steps"]["analyze"] = "reused"
        else:
            assert run is not None
            attempt_started = time.perf_counter()
            analysis, key_points, todos, (tin, tout) = await ctx.extractor.run(analyze_text)

            async with ctx.db.tx(user_id) as conn:
                version = await records.next_analysis_version(conn, item_id)
                await records.insert_analysis(
                    conn,
                    item_id=item_id,
                    version=version,
                    summary=analysis.summary,
                    key_points=key_points,
                    todos_extracted=todos,
                    confidence=float(analysis.confidence),
                    model=settings.fast_model
                    if settings.extract_model_tier == "fast"
                    else settings.strong_model,
                    prompt_version=INGEST_VERSION,
                    raw_json=analysis.model_dump(),
                )
                # 模型判定的 kind / title / tags 回写到主表（用户改过的不动）
                await conn.execute(
                    """
                    update items
                       set kind = case when user_pinned then kind else %s end,
                           title = case when user_pinned then title else %s end,
                           tags = case when user_pinned then tags else %s end
                     where id = %s
                    """,
                    (analysis.kind, analysis.title[:200], analysis.tags[:8], str(item_id)),
                )
                await infra_repo.finish_run(
                    conn,
                    run_id=run["id"],
                    status="succeeded",
                    output_ref={"version": version, "keyPoints": len(key_points)},
                    tokens_in=tin,
                    tokens_out=tout,
                    latency_ms=int((time.perf_counter() - attempt_started) * 1000),
                )
                await infra_repo.record_usage(
                    conn,
                    user_id=user_id,
                    path="ingest",
                    model=settings.fast_model,
                    tokens_in=tin,
                    tokens_out=tout,
                )
            stats["steps"]["analyze"] = "ok"
            stats["analysis"] = {
                "kind": analysis.kind,
                "title": analysis.title,
                "keyPoints": len(key_points),
                "todos": len(todos),
            }

    except Exception as exc:  # noqa: BLE001
        logger.exception("分析失败 item=%s", item_id)
        async with ctx.db.tx(user_id) as conn:
            await records.set_analysis_status(conn, item_id, "failed")
        stats["steps"]["analyze"] = f"failed:{type(exc).__name__}"
        stats["error"] = str(exc)[:300]
        stats["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return stats

    # ---------- 步骤 3：切片 + 向量 ----------
    try:
        chunks, embed_model = await build_chunks(body, ctx.embeddings)
        async with ctx.db.tx(user_id) as conn:
            n = await records.replace_chunks(
                conn, item_id=item_id, user_id=user_id, chunks=chunks, model=embed_model
            )
        stats["steps"]["embed"] = f"ok:{n}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("切片失败 item=%s", item_id)
        stats["steps"]["embed"] = f"failed:{type(exc).__name__}"

    # ---------- 步骤 4：建关联 ----------
    try:
        async with ctx.db.tx(user_id) as conn:
            links = await link_item(conn, item_id=item_id, user_id=user_id)
        stats["steps"]["link"] = f"ok:{len(links)}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("建关联失败 item=%s", item_id)
        stats["steps"]["link"] = f"failed:{type(exc).__name__}"

    # ---------- 收尾 ----------
    async with ctx.db.tx(user_id) as conn:
        # 处理期间用户可能把这条删了，收尾前确认一次，别给已删除的记录改状态
        item = await records.get_item_for_worker(conn, item_id)
        if item is None:
            stats["analysis_status"] = "skipped"
            stats["skipped"] = "deleted_during_ingest"
            return stats
        has_analysis = await records.latest_analysis(conn, item_id) is not None
        await records.set_analysis_status(conn, item_id, "done" if has_analysis else "failed")

    stats["analysis_status"] = "done" if has_analysis else "failed"
    stats["latency_ms"] = int((time.perf_counter() - started) * 1000)
    logger.info("入库完成 %s", stats)
    return stats
