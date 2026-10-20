"""步骤 2：分析（唯一一次调模型的环节）。

设计要点：
- `temperature=0`，同一段话每次结果一致，便于复现和测试；
- 用 `with_structured_output` 让供应商侧保证 JSON 结构，**不在提示词里教格式**；
- quote 必须能在原文中定位到，定位不到视为抽取失败 —— 重试一次，仍失败则记 failed；
- **绝不丢弃用户原文**。这是底线，分析失败只是"没整理"，不是"没保存"。
"""

from __future__ import annotations

import logging

from app.agents.ingest.prompt import (
    INGEST_RETRY_SUFFIX,
    INGEST_SYSTEM,
    ingest_user_message,
)
from app.agents.ingest.schema import ItemAnalysis, SpanLocated
from app.infra.tokens import count_tokens

logger = logging.getLogger(__name__)


class SpanMismatch(Exception):
    """模型给的 quote 在原文里找不到。"""

    def __init__(self, quote: str) -> None:
        super().__init__(f"quote 未能在原文中定位：{quote[:40]!r}")
        self.quote = quote


class SchemaViolation(Exception):
    """输出不符合业务约束（比如摘要里写了数字）。"""


def _locate(text: str, quote: str) -> tuple[int, int]:
    """在原文中定位 quote。

    先精确找，再容忍空白差异地找（模型偶尔会把全角空格换成半角）。
    两种都找不到才算失败——这不是宽松，是"仍然可验证"。
    """
    q = quote.strip()
    if not q:
        raise SpanMismatch(quote)

    idx = text.find(q)
    if idx >= 0:
        return idx, idx + len(q)

    # 空白归一后再试：把原文和 quote 的连续空白都压成单个空格，位置靠映射还原
    compact_chars: list[str] = []
    pos_map: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            compact_chars.append(" ")
            pos_map.append(i)
            prev_space = True
        else:
            compact_chars.append(ch)
            pos_map.append(i)
            prev_space = False
    compact = "".join(compact_chars)
    compact_q = " ".join(q.split())

    idx = compact.find(compact_q)
    if idx < 0:
        raise SpanMismatch(quote)
    start = pos_map[idx]
    end = pos_map[idx + len(compact_q) - 1] + 1
    return start, end


def locate_spans(analysis: ItemAnalysis, text: str) -> tuple[list[dict], list[dict]]:
    """把 quote 列表转成带偏移的片段列表。任何一段定位失败都会抛 SpanMismatch。"""
    key_points: list[dict] = []
    for sp in analysis.key_points[:5]:
        start, end = _locate(text, sp.quote)
        key_points.append(SpanLocated(quote=sp.quote, start=start, end=end).model_dump())

    todos: list[dict] = []
    for sp in analysis.todos[:5]:
        start, end = _locate(text, sp.quote)
        todos.append(SpanLocated(quote=sp.quote, start=start, end=end).model_dump())
    return key_points, todos


def check_no_digits(text: str) -> None:
    """摘要里不许有数字。

    这条规则听起来苛刻，其实是为了让"总结里每个数字都可核对"成为可能：
    一旦模型能自由写数字，你就必须逐句核对，那等于没校验。
    """
    import re

    if re.search(r"\d", text):
        raise SchemaViolation("摘要中出现了数字，必须去掉（数量由程序统计）")


class Extractor:
    def __init__(self, llm) -> None:
        self._llm = llm
        try:
            # function_calling 兼容性最好：OpenAI / DeepSeek / Moonshot 等
            # OpenAI 兼容网关都支持工具调用；DeepSeek 不支持 json_schema 这种
            # response_format，会直接 400。
            self._structured = llm.with_structured_output(ItemAnalysis, method="function_calling")
        except TypeError:
            self._structured = llm.with_structured_output(ItemAnalysis, method="function_calling")

    async def analyze(self, text: str) -> tuple[ItemAnalysis, int, int]:
        """返回 (分析结果, 输入 token, 输出 token)。"""
        messages = [
            ("system", INGEST_SYSTEM),
            ("user", ingest_user_message(text)),
        ]
        result: ItemAnalysis = await self._structured.ainvoke(messages)
        tokens_in = count_tokens(INGEST_SYSTEM) + count_tokens(text)
        tokens_out = count_tokens(result.model_dump_json())
        return result, tokens_in, tokens_out

    async def run(self, text: str) -> tuple[ItemAnalysis, list[dict], list[dict], tuple[int, int]]:
        """抽取 + 校验 + 一次带错误信息回喂的重试。

        重试不是为了掩盖失败，而是因为模型对"引号里的字必须逐字"这件事
        偶尔会走神；把具体错误告诉它，第二次通常就对了。
        连续失败两次就如实标记 failed，不要无限重试烧钱。
        """
        last_error: Exception | None = None
        total_in = 0
        total_out = 0

        for attempt in range(2):
            try:
                if attempt == 0:
                    analysis, tin, tout = await self.analyze(text)
                else:
                    assert last_error is not None
                    messages = [
                        ("system", INGEST_SYSTEM),
                        ("user", ingest_user_message(text)),
                        ("user", INGEST_RETRY_SUFFIX.format(error=str(last_error))),
                    ]
                    analysis = await self._structured.ainvoke(messages)
                    tin = count_tokens(text) + count_tokens(str(last_error))
                    tout = count_tokens(analysis.model_dump_json())

                total_in += tin
                total_out += tout

                check_no_digits(analysis.summary)
                key_points, todos = locate_spans(analysis, text)
                return analysis, key_points, todos, (total_in, total_out)

            except Exception as exc:  # noqa: BLE001 - 需要区分类型后统一处理
                last_error = exc
                logger.warning(
                    "提取失败 attempt=%s err=%s", attempt + 1, exc, exc_info=isinstance(exc, SpanMismatch)
                )

        assert last_error is not None
        raise last_error
