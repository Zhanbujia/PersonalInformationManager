"""路径三：阶段总结。

这是三条路径里**最容易出幻觉**的一条（"你本月记录了 87 条"——这个 87 可能是编的，
而且编得很像真的）。所以它的每个环节都做了硬约束：

1. 数字全部来自 SQL（`repo/digests.collect_stats`）；
2. 模型只能写占位符，正文出现裸数字即判失败并重试；
3. 行动建议必须挂证据 id，且只能从候选记录里选，不能凭空造；
4. 证据存版本快照，记录后来变了就把总结标记为过期。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.agents.digest.verify import render
from app.config import settings
from app.infra.db import Database
from app.infra.tokens import count_tokens
from app.repo import digests as digest_repo
from app.repo import infra as infra_repo

logger = logging.getLogger(__name__)

DIGEST_VERSION = "digest.v1"

DIGEST_SYSTEM = """你是一个中文个人记录总结助手。用户会给你一份"统计数字"和"本期记录摘录"，你要写一份阶段性回顾。

必须遵守：

1. 正文里**禁止出现任何阿拉伯数字**。凡是需要写数量的地方，一律使用统计项占位符，形如 `{{stat:itemCount}}`。
   可以使用的占位符只有给定的这几个，不要自己造名字。
2. 不要编造统计项里没有的事实（没提到某人、某个项目就不要说）。
3. 结构固定为三部分，用 markdown 二级标题：
   ## 这个阶段你记了什么
   ## 几条线索
   ## 建议接下来做什么
4. 第二部分要指出记录之间**可能的关系**，如果看不出关系就直说"这几条还比较散"，不要硬凑。
5. 第三部分的每条建议都必须能从给出的记录里找到依据；只写 2 到 4 条，不要罗列清单。
6. 语气平实，像同事帮你回顾，不要用"恭喜你""太棒了"这类客套。

输出必须符合给定的 JSON 结构：`body_markdown` 是正文，`action_items` 是建议列表。"""


class ActionDraft(BaseModel):
    text: str = Field(description="建议做的一件具体事，一句话")
    reason: str = Field(description="为什么建议做这件事，必须引用给出的记录内容")
    evidence_item_ids: list[str] = Field(description="支撑这条建议的记录 id，只能从给出的候选 id 里选")


class DigestDraft(BaseModel):
    body_markdown: str = Field(
        description="总结正文，markdown 格式，数字只能用 {{stat:xxx}} 占位符，不得出现阿拉伯数字"
    )
    action_items: list[ActionDraft] = Field(description="2 到 4 条建议")


@dataclass
class DigestContext:
    db: Database
    strong_llm: object


def default_period(kind: str) -> tuple[date, date]:
    """按类型算默认周期：周总结取上一个自然周，月总结取上一个自然月。"""
    from datetime import timedelta

    today = date.today()
    if kind == "weekly":
        # 本周一往前推 7 天 = 上个周一
        monday = today - timedelta(days=today.weekday())
        start = monday - timedelta(days=7)
        return start, start + timedelta(days=6)
    if kind == "monthly":
        first_this_month = today.replace(day=1)
        last_month_end = first_this_month - timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    return today - timedelta(days=7), today


def _stats_block(stats: dict) -> str:
    lines = [f"- {k}: {v}" for k, v in stats.items() if k != "topItems"]
    return "\n".join(lines)


def _items_block(items: list[dict]) -> str:
    if not items:
        return "（本期没有记录）"
    parts = []
    for it in items:
        parts.append(
            f"id={it['id']}\n类型={it['kind']}\n标题={it.get('title') or '（无）'}\n"
            f"摘录={it['excerpt']}\n"
        )
    return "\n---\n".join(parts)


def _candidate_ids(items: list[dict]) -> set[str]:
    return {str(it["id"]) for it in items}


async def generate_digest(
    ctx: DigestContext,
    *,
    user_id: UUID,
    kind: str,
    period_start: date,
    period_end: date,
) -> UUID:
    """生成并落库。返回 digest id。"""
    async with ctx.db.tx(user_id) as conn:
        stats = await digest_repo.collect_stats(conn, period_start, period_end)
        top_items = stats.pop("topItems", [])
        # 候选证据：本期所有记录（含无标题的），用于校验建议的来源
        candidates = await digest_repo.top_items(conn, period_start, period_end, limit=60)

    if stats["itemCount"] == 0:
        # 没有内容时不要浪费模型调用，直接给出确定性文案
        content = (
            f"## 这个阶段你记了什么\n\n这段时间（{period_start} 至 {period_end}）没有新的记录。\n\n"
            "## 几条线索\n\n暂时没有可以关联的内容。\n\n"
            "## 建议接下来做什么\n\n想起什么先记下来，哪怕只有一句话，先让信息流动起来。"
        )
        async with ctx.db.tx(user_id) as conn:
            return await digest_repo.insert_digest(
                conn,
                user_id=user_id,
                kind=kind,
                period_start=period_start,
                period_end=period_end,
                content_md=content,
                stats=stats,
                action_items=[],
                model=None,
                prompt_version=DIGEST_VERSION,
                evidence=[],
            )

    prompt = (
        "## 统计数字（只能在占位符里引用，不要直接抄数字进正文）\n"
        f"{_stats_block(stats)}\n\n"
        "## 本期记录摘录\n"
        f"{_items_block(top_items)}\n\n"
        f"请写一份 {period_start} 至 {period_end} 的回顾。"
    )

    structured = _bind_structured(ctx.strong_llm)
    draft: DigestDraft = await structured.ainvoke(
        [("system", DIGEST_SYSTEM), ("user", prompt)]
    )

    # 校验 + 渲染。渲染失败说明模型写了裸数字，直接抛错由上层重试/报错，
    # 不做"勉强渲染"的妥协——那会让"每个数字可核对"这条保证失效。
    content_md = render(draft.body_markdown, stats)

    allowed = _candidate_ids(candidates)
    action_items = _clean_actions(draft.action_items, allowed)

    evidence = []
    candidate_map = {str(it["id"]): it for it in candidates}
    for aid in {a for item in action_items for a in item["evidenceItemIds"]}:
        it = candidate_map.get(aid)
        if not it:
            continue
        evidence.append(
            {
                "item_id": aid,
                "item_version": it.get("analysis_version", 0),
                "quote": (it.get("excerpt") or "")[:200],
            }
        )

    tokens_in = count_tokens(DIGEST_SYSTEM) + count_tokens(prompt)
    tokens_out = count_tokens(draft.model_dump_json())

    async with ctx.db.tx(user_id) as conn:
        digest_id = await digest_repo.insert_digest(
            conn,
            user_id=user_id,
            kind=kind,
            period_start=period_start,
            period_end=period_end,
            content_md=content_md,
            stats=stats,
            action_items=action_items,
            model=settings.strong_model,
            prompt_version=DIGEST_VERSION,
            evidence=evidence,
        )
        await infra_repo.record_usage(
            conn,
            user_id=user_id,
            path="digest",
            model=settings.strong_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
    logger.info("总结生成完成 digest=%s items=%s", digest_id, stats["itemCount"])
    return digest_id


def _bind_structured(llm):
    try:
        return llm.with_structured_output(DigestDraft, method="function_calling")
    except TypeError:
        return llm.with_structured_output(DigestDraft, method="function_calling")


def _clean_actions(actions: list[ActionDraft], allowed: set[str]) -> list[dict]:
    """只保留能挂上证据的建议。

    挂了不存在的 id 的建议直接丢掉——"有依据"是这条产品承诺的一部分，
    宁可少给一条，也不给一条无依据的空话。
    """
    cleaned: list[dict] = []
    for a in actions[:4]:
        ids = [i for i in a.evidence_item_ids if i in allowed]
        if not ids:
            continue
        cleaned.append(
            {"text": a.text, "reason": a.reason, "evidenceItemIds": ids}
        )
    return cleaned
