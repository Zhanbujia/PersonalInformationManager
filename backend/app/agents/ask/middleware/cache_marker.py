"""拦截点 ③：prompt 缓存标记。

这里有一个"不报错但很贵"的坑，值得单独说明：

`SystemMessage(content="一大段文字")` 这种最顺手的写法，**没法携带缓存标记**。
供应商（Anthropic 系）要求把指令写成 content block 并显式标 `cache_control`，
否则缓存命中率恒为 0 —— 请求照常成功，账单悄悄翻倍。

所以这里做一件事：**只在显式打开开关时**，把系统消息转成带 `cache_control`
的 content block。默认关闭，因为 OpenAI 兼容网关可能不认识这个字段，
硬塞会被拒绝。开不开取决于你用哪家模型，这是部署决策，不是代码决策。

另外：**这里绝对不改模型、不改工具列表**。两者都在稳定前缀里，
改它们等于让整段缓存作废——省下的钱还不够一次重算。
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import SystemMessage

from app.config import settings

logger = logging.getLogger(__name__)


def _with_cache_control(message: SystemMessage) -> SystemMessage:
    content = message.content
    if isinstance(content, list):
        # 已经是 block 形式：给最后一个 text block 打标记
        blocks = [
            dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content
        ]
        for block in reversed(blocks):
            if block.get("type") == "text":
                block["cache_control"] = {"type": "ephemeral"}
                break
        return SystemMessage(content=blocks)

    return SystemMessage(
        content=[
            {
                "type": "text",
                "text": str(content),
                "cache_control": {"type": "ephemeral"},
            }
        ]
    )


@wrap_model_call
async def cache_marker(request, handler):
    if settings.prompt_cache_enabled and request.system_message is not None:
        try:
            request = request.override(system_message=_with_cache_control(request.system_message))
        except Exception:  # noqa: BLE001 - 标记失败不该让问答失败
            logger.warning("缓存标记失败，按原样发送", exc_info=True)
    return await handler(request)
