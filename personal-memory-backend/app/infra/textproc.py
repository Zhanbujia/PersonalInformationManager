"""文本处理：规范化、分词、切片、哈希。

这一层不碰数据库、不碰模型，是纯函数，所以可以完整单测。

关于中文分词：PostgreSQL 的全文检索没有中文词典，`simple` 解析器会把一整句中文
当成一个词元，等于没有索引。所以**必须**在写入前用 jieba 切成空格分隔的形式，
再交给 `to_tsvector('simple', ...)`。这就是 `segment()` 存在的原因，
也是检索质量的关键一环。
"""

from __future__ import annotations

import hashlib
import re

# 归一化时统一掉的空白形式
_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
# 句末标点：中英文都算
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")

_CJK = re.compile(r"[\u3400-\u9fff]")

# 中文停用词（极简版，只去高频虚词，避免把语义词干掉）
_STOPWORDS = frozenset(
    "的 了 和 是 在 我 有 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这 那 与 及 或 但 而 被 把 让 给 对 从 为 以 于 之 其 此 等 个 些 中 后 前 里 时".split()
)

_jieba = None


def _get_jieba():
    global _jieba
    if _jieba is None:
        import logging

        import jieba

        jieba.setLogLevel(logging.WARNING)
        _jieba = jieba
    return _jieba


def normalize(raw: str) -> str:
    """规范化：统一换行、去行尾空白、压缩连续空行。原文不动，只产出 body_md。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0) if m else None


def segment(text: str, *, drop_stopwords: bool = True) -> str:
    """把文本切成空格分隔的词，供 `to_tsvector('simple', ...)` 建索引/查询。

    英文原样保留并小写；中文用 jieba 的搜索模式（更细粒度，召回更好）。
    """
    if not text:
        return ""
    tokens: list[str] = []
    for raw_tok in _get_jieba().cut_for_search(text):
        tok = raw_tok.strip()
        if not tok:
            continue
        if _CJK.search(tok):
            if drop_stopwords and tok in _STOPWORDS:
                continue
            tokens.append(tok)
        else:
            # 纯符号/空白丢掉；英文数字保留
            if re.fullmatch(r"[\w.\-+]+", tok):
                tokens.append(tok.lower())
    return " ".join(tokens)


def _split_by_sentence(paragraph: str) -> list[str]:
    parts = [p for p in _SENT_SPLIT.split(paragraph) if p]
    return parts or [paragraph]


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[tuple[str, int, int]]:
    """按段落/句子边界切片，返回 [(切片文本, 起始偏移, 结束偏移)]。

    不硬切：优先在段落边界断开，段落太长再按句子断，句子还太长（极少见）才按字数断。

    **偏移量由构造保证正确**：切片的文本一律取 `text[start:end]`，
    而不是把若干片段拼起来再猜位置。拼接会引入换行符差异，
    让"原文高亮"整体错位——这种错误很难被发现，但一眼就能被用户看出来。
    """
    text = text or ""
    if not text.strip():
        return []
    if len(text) <= size:
        s, e = _trim(text, 0)
        return [(text[s:e], s, e)]

    # 先按段落切，同时记录每段在原文中的位置
    spans: list[tuple[int, int]] = []
    pos = 0
    for para in text.split("\n\n"):
        if para:
            spans.append((pos, pos + len(para)))
        pos += len(para) + 2  # 加上分隔用的 "\n\n"

    # 段落太长就按句子再切，句子还太长就按字数切
    units: list[tuple[int, int]] = []
    for start, end in spans:
        para_len = end - start
        if para_len <= size:
            units.append((start, end))
            continue
        sent_start = start
        for sent in _SENT_SPLIT.split(text[start:end]):
            if not sent:
                continue
            s_end = sent_start + len(sent)
            if len(sent) <= size:
                units.append((sent_start, s_end))
            else:
                step = max(1, size - overlap)
                for i in range(sent_start, s_end, step):
                    units.append((i, min(i + size, s_end)))
            sent_start = s_end

    # 把小单元合并成不超过 size 的片；切点始终落在单元边界，所以不会切断句子
    pieces: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end = 0
    for u_start, u_end in units:
        if cur_start is None:
            cur_start, cur_end = u_start, u_end
        elif u_end - cur_start <= size:
            cur_end = u_end
        else:
            pieces.append((cur_start, cur_end))
            # 重叠：把上一片末尾若干字符带进下一片，保证跨片语义不断裂
            back = max(u_start, cur_end - overlap) if overlap else u_start
            cur_start, cur_end = back, u_end
    if cur_start is not None:
        pieces.append((cur_start, cur_end))

    result: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for s, e in pieces:
        s2, e2 = _trim(text, s, e)
        if s2 >= e2:
            continue
        chunk = text[s2:e2]
        key = content_hash(chunk)
        if key in seen:
            continue
        seen.add(key)
        result.append((chunk, s2, e2))
    return result


def _trim(text: str, start: int, end: int | None = None) -> tuple[int, int]:
    """去掉首尾空白并把偏移收进去，保证 text[start:end] 仍然等于切片文本。"""
    end = len(text) if end is None else end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def truncate_for_analysis(text: str, head: int = 4000, tail: int = 2000) -> str:
    """超长文本截断：保留头尾，因为信息密度通常在两端。"""
    if len(text) <= head + tail:
        return text
    return text[:head] + "\n\n……（中间省略）……\n\n" + text[-tail:]
