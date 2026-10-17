"""文本处理的单测。

这几个函数是纯的，所以测试可以写得很硬：输入什么、输出必须是什么。
其中切片偏移量最重要——前端要靠它高亮原文，错一位就高亮错地方。
"""

from __future__ import annotations

from app.infra.textproc import (
    chunk_text,
    content_hash,
    extract_url,
    normalize,
    segment,
    truncate_for_analysis,
)


def test_normalize_unifies_newlines_and_blanks():
    raw = "第一行  \r\n\r\n\r\n\r\n第二行\t\r\n"
    assert normalize(raw) == "第一行\n\n第二行"


def test_content_hash_stable():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")


def test_extract_url():
    assert extract_url("看这个 https://example.com/a?b=1 挺好") == "https://example.com/a?b=1"
    assert extract_url("没有链接") is None


def test_segment_chinese_produces_spaces():
    out = segment("混合检索里BM25负责精确匹配")
    assert " " in out
    assert "检索" in out


def test_segment_drops_stopwords_only():
    tokens = segment("的 了 混合检索").split()
    assert "的" not in tokens
    assert "了" not in tokens
    # jieba 搜索模式可能把词切得更细，这里只要求内容词仍在
    assert any("检索" in t or "混合" in t for t in tokens), tokens


def test_chunk_offsets_are_exact():
    text = "第一段内容。\n\n第二段内容长一点，用来测试偏移。\n\n第三段。"
    chunks = chunk_text(text, size=12, overlap=0)
    assert chunks, "应当至少切出一片"
    for chunk, start, end in chunks:
        # 最关键的断言：切片文本必须等于原文的对应区间
        assert text[start:end] == chunk, f"偏移错位: {chunk!r} != {text[start:end]!r}"


def test_chunk_short_text_single_piece():
    text = "很短的一句话"
    chunks = chunk_text(text, size=500)
    assert len(chunks) == 1
    assert chunks[0] == (text, 0, len(text))


def test_chunk_empty_text():
    assert chunk_text("", size=500) == []
    assert chunk_text("   \n\n  ", size=500) == []


def test_chunk_overlap_does_not_duplicate_identical_text():
    text = "重复。\n\n重复。\n\n重复。"
    chunks = chunk_text(text, size=5, overlap=0)
    texts = [c[0] for c in chunks]
    assert len(texts) == len(set(texts)), "完全相同的切片只应保留一份"


def test_truncate_keeps_head_and_tail():
    text = "头" * 5000 + "中" * 5000 + "尾" * 5000
    out = truncate_for_analysis(text, head=100, tail=100)
    assert out.startswith("头")
    assert out.endswith("尾")
    assert "省略" in out
