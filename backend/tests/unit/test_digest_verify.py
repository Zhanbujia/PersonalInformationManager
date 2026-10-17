"""数字校验的单测。

这是"总结里每个数字都可核对"这条承诺的守卫。
如果这些测试挂了，说明总结开始可能编数字了，属于必须修的那类问题。
"""

from __future__ import annotations

import pytest

from app.agents.digest.verify import (
    UnknownPlaceholder,
    UnverifiedNumber,
    render,
    used_stats,
)

STATS = {
    "itemCount": 87,
    "todoTotal": 12,
    "todoDone": 8,
    "activeDays": 21,
    "topTopics": ["向量检索", "产品设计"],
}


def test_render_replaces_placeholders():
    raw = "你记录了 {{stat:itemCount}} 条，完成 {{stat:todoDone}} 条待办。"
    out = render(raw, STATS)
    assert out == "你记录了 87 条，完成 8 条待办。"


def test_render_list_value_joined():
    out = render("主题集中在 {{stat:topTopics}}。", STATS)
    assert out == "主题集中在 向量检索、产品设计。"


def test_bare_number_rejected():
    """模型自己写数字 = 无法溯源，必须拒绝。"""
    with pytest.raises(UnverifiedNumber):
        render("你记录了 87 条内容。", STATS)


def test_bare_number_even_when_equal_to_stat():
    """即使数字正好等于真实值也不算通过——因为它不可证明是替换来的。"""
    with pytest.raises(UnverifiedNumber):
        render("你记录了 {{stat:itemCount}} 条，也就是 87 条。", STATS)


def test_chinese_numeral_is_allowed():
    """中文数字不在拦截范围内：它不参与"数量可核对"，而且强行禁会写不出正常中文。"""
    out = render("这两条线索有共同点。", STATS)
    assert out == "这两条线索有共同点。"


def test_unknown_placeholder_rejected():
    with pytest.raises(UnknownPlaceholder):
        render("你记录了 {{stat:notExist}} 条。", STATS)


def test_used_stats():
    assert used_stats("{{stat:itemCount}} 和 {{stat:todoDone}}") == {"itemCount", "todoDone"}


def test_float_formatting():
    assert render("{{stat:ratio}}", {"ratio": 0.5}) == "0.5"
    assert render("{{stat:whole}}", {"whole": 21.0}) == "21"


def test_empty_list():
    assert render("{{stat:empty}}", {"empty": []}) == "（空）"
