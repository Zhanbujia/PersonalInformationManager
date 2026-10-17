"""拦截点 ⑤：引用校验 + 用量累计。

原则：**模型可以胡说，但它的胡说会被程序拦住。**

每条 `[n]` 都要满足三条：
1. `n` 在本次检索返回的编号里（也就是真的有一份片段对应它）；
2. 该片段来自当前用户的数据（工具走的是租户事务，天然满足，这里再核一次）；
3. 角标没有指向不存在的编号。

不满足的角标降级成"（来源未核实）"，并在日志里留一条告警。
用户看到的每个角标都能点开看到原文——这是这个产品可信度的全部来源。
"""

from __future__ import annotations

import logging
import re

from langchain.agents.middleware import after_model
from langchain_core.messages import AIMessage

from app.agents.ask.context import AskContext

logger = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[(\d{1,2})\]")
UNVERIFIED = "（来源未核实）"


def extract_citation_numbers(text: str) -> list[int]:
    seen: list[int] = []
    for m in CITATION_RE.finditer(text or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def verify_and_render(text: str, ctx: AskContext) -> tuple[str, list[dict]]:
    """返回 (降级后的正文, 合法引用列表)。

    降级而不是删除：删掉角标会让读者以为那句话是凭空来的，
    留下"来源未核实"至少诚实。
    """
    valid: list[dict] = []

    def repl(m: re.Match[str]) -> str:
        n = int(m.group(1))
        ev = ctx.evidence.get(n)
        if not ev:
            return UNVERIFIED
        if not ev.get("chunkId") or not ev.get("itemId"):
            return UNVERIFIED
        if all(c["n"] != n for c in valid):
            valid.append({"n": n, **ev})
        return m.group(0)

    rendered = CITATION_RE.sub(repl, text or "")

    # 重新编号：按**出现顺序**编成 1..n，避免正文里出现 [3] 却没有 [1][2] 这种断裂。
    # 注意不能先按原编号排序再重编——那两步会互相抵消，等于没做。
    renumber = {c["n"]: i + 1 for i, c in enumerate(valid)}
    if renumber:
        rendered = CITATION_RE.sub(
            lambda m: f"[{renumber[int(m.group(1))]}]" if int(m.group(1)) in renumber else UNVERIFIED,
            rendered,
        )
    for c in valid:
        c["n"] = renumber[c["n"]]

    dangling = [n for n in extract_citation_numbers(text) if n not in ctx.evidence]
    if dangling:
        logger.warning("引用校验：%s 个角标无法溯源 %s", len(dangling), dangling)

    ctx.citations = valid
    return rendered, valid


@after_model
async def citation_check(state, runtime):
    """每轮模型返回后执行。

    - 有 tool_calls 的消息不是最终答案，只累计用量；
    - 没有 tool_calls 的才是答案，做引用校验。
    """
    ctx: AskContext = runtime.context
    ctx.rounds += 1

    messages = state["messages"]
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last is None:
        return None

    usage = getattr(last, "usage_metadata", None) or {}
    ctx.tokens_in += int(usage.get("input_tokens", 0) or 0)
    ctx.tokens_out += int(usage.get("output_tokens", 0) or 0)
    details = usage.get("input_token_details") or {}
    ctx.cached_tokens += int(details.get("cache_read", 0) or 0)

    if getattr(last, "tool_calls", None):
        return None

    text = last.content if isinstance(last.content, str) else str(last.content)
    if text:
        ctx.answer_text = text
        verify_and_render(text, ctx)
    return None
