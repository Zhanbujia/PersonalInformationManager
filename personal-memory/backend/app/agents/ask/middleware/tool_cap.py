"""拦截点：工具调用硬上限。

为什么要单独做一层"去工具"而不是只靠 budget_guard 的 jump_to：

有些模型（尤其 DeepSeek 这类会并行/反复调工具的）一轮里能打出好几个
tool_calls，导致 langgraph 的 step 数（model 节点 + tool 节点）远比
"轮数"涨得快——`recursion_limit` 会先于 `max_tool_calls` 触顶，
于是还没等 budget_guard 把 `tool_calls` 数到上限，就先抛
`GraphRecursionError` 了。

这里在模型调用前直接把工具摘掉：一旦 `tool_calls` 达到上限，
模型**不再有能力**发起新工具调用，只能给出最终答案。这是"物理"上的
终止保证，不依赖模型自觉。

不改系统指令、不改工具清单（前缀稳定），只在这一次请求里换一个
没有工具的模型实例。
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import wrap_model_call

from app.agents.ask.context import AskContext

logger = logging.getLogger(__name__)


@wrap_model_call
async def tool_cap(request, handler):
    ctx: AskContext = request.runtime.context

    if ctx.tool_calls >= ctx.max_tool_calls:
        ctx.truncated = True
        ctx.truncate_reason = ctx.truncate_reason or "tool_cap"
        try:
            # 强制"不许再调用工具"，但**保留工具 schema**（前缀稳定）。
            # 比 bind_tools([]) 可靠：多数实现里把工具换成空列表并不会真正撤销
            # 已绑定的工具，模型照样能继续调。
            request = request.override(model=request.model.bind(tool_choice="none"))
        except Exception:  # noqa: BLE001
            try:
                request = request.override(model=request.model.bind_tools([]))
            except Exception:  # noqa: BLE001 - 兜底失败就退回原模型
                logger.warning("tool_cap: 去工具失败，退回原模型", exc_info=True)

    return await handler(request)
