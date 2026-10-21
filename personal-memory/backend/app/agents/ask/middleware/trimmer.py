"""拦截点 ②：上下文水位。

这里做的是一个**有意的克制**：运行中**不重写历史**，只在水位超标时收尾。

为什么不重写：摘要式压缩（把前几轮压成一段话）会让消息序列发生变化，
而 prompt 缓存是按前缀字节匹配的——一改历史，整段前缀作废。
一次压缩赔掉的重算成本，往往比省下来的 token 还多。

那水位真的会超吗？不会，因为历史在**进入 Agent 之前**就裁剪好了：
只取最近 4 轮（`recent_history`），检索片段每条截断到 500 字，
单轮上限 5 次工具调用。也就是说正常情况下的上下文是有界的。

这一层是兜底：万一某次检索返回异常大，或者用户粘了一段超长文本进来，
它会在调用模型之前喊停，而不是等供应商报 400。

结论：**压缩在会话边界做（入参裁剪），运行中只做熔断。**
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage

from app.agents.ask.context import AskContext
from app.config import settings
from app.infra.tokens import count_messages_tokens

logger = logging.getLogger(__name__)


@before_model(can_jump_to=["end"])
async def context_trimmer(state, runtime):
    ctx: AskContext = runtime.context

    payload = []
    for m in state["messages"]:
        content = m.content if isinstance(m.content, str) else str(m.content)
        payload.append({"content": content})

    estimated = count_messages_tokens(payload)
    watermark = settings.compact_trigger_tokens

    if estimated <= watermark:
        return None

    logger.warning(
        "上下文水位超限 estimated=%s watermark=%s item_owner=%s", estimated, watermark, ctx.user_id
    )
    ctx.truncated = True
    ctx.truncate_reason = "context_watermark"
    return {
        "jump_to": "end",
        "messages": [
            AIMessage(
                content=(
                    "（这轮检索到的材料太多，先把最相关的部分整理给你。"
                    "如果把问题问得再具体一点，我可以查得更准。）"
                )
            )
        ],
    }
