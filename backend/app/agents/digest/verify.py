"""数字校验：占位符协议的另一半。

核心思路（这是"禁止模型写数字"和"每个数字都可核对"能同时成立的唯一办法）：

1. 程序先用 SQL 算出所有数字，组成 `stats`；
2. 模型只输出带 `{{stat:key}}` 占位符的正文，**正文里不允许出现任何数字字符**；
3. 程序校验"去掉占位符后没有任何数字"，再做强类型替换。

于是正文里出现的每一个数字，都必然来自某次替换，也就是必然来自 SQL。
这比"事后拿正则去正文里捞数字、再逐个比对"可靠得多——后者在
"3-5 个""两条"这类模糊表述上根本没法比。
"""

from __future__ import annotations

import re
from typing import Any

PLACEHOLDER = re.compile(r"\{\{stat:([a-zA-Z0-9_]+)\}\}")


class UnverifiedNumber(Exception):
    """正文里出现了无法溯源的数字。"""


class UnknownPlaceholder(Exception):
    """引用了不存在的统计项。"""


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        # 整数不显示小数点，避免"21.0 天"这种别扭写法
        return str(int(value)) if float(value).is_integer() else f"{value:g}"
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value) if value else "（空）"
    if isinstance(value, dict):
        return "、".join(f"{k}（{v}）" for k, v in value.items())
    return str(value)


def validate_template(raw: str, stats: dict[str, Any]) -> None:
    """校验模型输出的模板：不得有裸数字，不得引用不存在的统计项。"""
    stripped = PLACEHOLDER.sub("", raw)
    found = re.search(r"\d", stripped)
    if found:
        start = max(0, found.start() - 30)
        snippet = stripped[start : found.start() + 30].replace("\n", " ")
        raise UnverifiedNumber(
            f"正文出现了无法溯源的数字，位置附近：「…{snippet}…」。"
            "所有数字必须通过 {{stat:xxx}} 占位符引用统计值。"
        )

    used = set(PLACEHOLDER.findall(raw))
    unknown = sorted(used - set(stats))
    if unknown:
        raise UnknownPlaceholder(f"引用了不存在的统计项：{unknown}")


def render(raw: str, stats: dict[str, Any]) -> str:
    """校验 + 替换。校验不过就抛异常，绝不"先渲染再说"。"""
    validate_template(raw, stats)
    return PLACEHOLDER.sub(lambda m: _format_value(stats[m.group(1)]), raw)


def used_stats(raw: str) -> set[str]:
    return set(PLACEHOLDER.findall(raw))
