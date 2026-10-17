"""token 计数。

优先用 tiktoken 精确计数；如果拿不到编码表（离线环境会下载失败），
退回按字符估算——宁可估个大概，也不要因为统计失败让请求挂掉。
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_encoder: Any = None
_encoder_failed = False


def _get_encoder() -> Any:
    global _encoder, _encoder_failed
    if _encoder is not None or _encoder_failed:
        return _encoder
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - 离线/无网时降级
        logger.warning("tiktoken 不可用，token 计数降级为字符估算")
        _encoder_failed = True
    return _encoder


@functools.lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001
            pass
    # 经验值：中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return max(1, cjk + other // 4)


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算一组消息的 token。每条消息有固定开销，这里按 +4 计。"""
    total = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += count_tokens(str(block.get("text", "")))
        total += 4
    return total
