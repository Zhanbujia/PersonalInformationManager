"""步骤 3：切片与向量化。

两个决定：
- **切片而不是整篇**：整篇算一个向量的话，"其中某一段"永远检索不到。
- **向量失败不阻塞**：embedding 服务挂了就存空向量，关键词检索仍可用
  （`hybrid_search` 在 query_vec 为 None 时退化为纯全文检索）。
  宁可降级，也不要让"记一条"这件事整体失败。
"""

from __future__ import annotations

import logging

from app.config import settings
from app.infra.textproc import chunk_text, segment

logger = logging.getLogger(__name__)


async def build_chunks(text: str, embeddings) -> tuple[list[dict], str | None]:
    """返回 (切片列表, 使用的 embedding 模型名)。

    切片的 `search_text` 是 jieba 分词结果——这一步不能省，
    否则切片级的全文检索在中文上等于没有。
    """
    pieces = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    if not pieces:
        return [], None

    chunks = [
        {"text": t, "start": s, "end": e, "search_text": segment(t), "embedding": None}
        for t, s, e in pieces
    ]

    try:
        vectors = await embeddings.embed([c["text"] for c in chunks])
        for c, vec in zip(chunks, vectors, strict=True):
            c["embedding"] = vec
        return chunks, settings.embedding_model
    except Exception:  # noqa: BLE001 - 降级为纯关键词，不让入库失败
        logger.exception("向量化失败，降级为纯关键词检索 (chunks=%s)", len(chunks))
        return chunks, None
