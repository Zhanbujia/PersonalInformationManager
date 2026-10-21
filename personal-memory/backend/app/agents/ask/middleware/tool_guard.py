"""拦截点 ④：工具授权与失败计数。

**"工具失败 3 次就停用"为什么必须在这里做，而不是"从工具列表里删掉它"？**

工具清单住在稳定前缀里。把某个工具从清单里删掉，前缀就变了，整段 prompt 缓存作废；
而如果保留 schema 只在尾部说"别用了"，模型很可能还是去调。

正确的做法是**让前缀一动不动**：工具照常披露（模型仍看得到它），
但真正调用时在执行层被拒绝，并回一条明确的错误让它换策略。
这正是"注册 ≠ 披露 ≠ 授权"三层分离的价值——能在授权层收紧，就不必动披露层。

被拒绝时返回的是一条 `ToolMessage(status="error")`，不是抛异常：
抛异常会让整个 Agent 崩掉，而模型的正常反应应该是"换个办法"。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage

from app.agents.ask.context import AskContext

logger = logging.getLogger(__name__)


def _fingerprint(name: str, args: Any) -> str:
    """工具调用指纹：工具名 + 参数的稳定序列化。

    必须稳定：dict 的 key 顺序不同但内容一样，要算同一个指纹，
    否则 `{"a":1,"b":2}` 和 `{"b":2,"a":1}` 会被当成两次不同调用，打转检测失效。
    """
    try:
        body = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        body = str(args)
    return f"{name}:{body}"


@wrap_tool_call
async def tool_guard(request, handler):
    ctx: AskContext = request.runtime.context
    tool_call = request.tool_call
    name = tool_call.get("name", "")
    call_id = tool_call.get("id", "")

    # ① 授权层：已停用的工具直接拒绝
    if ctx.is_disabled(name):
        f = ctx.tool_failures[name]
        return ToolMessage(
            content=(
                f"工具 {name} 本次会话已停用（连续失败 {f.count} 次，最后一次错误：{f.last_error}）。"
                "请换一个工具，或者基于已有信息如实说明缺少哪些证据。"
            ),
            tool_call_id=call_id,
            status="error",
        )

    # ② 打转指纹：记下来，由 budget_guard 判断是否要打断
    ctx.fingerprint_ring.append(_fingerprint(name, tool_call.get("args", {})))
    if len(ctx.fingerprint_ring) > 8:
        ctx.fingerprint_ring = ctx.fingerprint_ring[-8:]

    try:
        result = await handler(request)
    except Exception as exc:  # noqa: BLE001
        # 失败计数：连续三次就把这个工具关掉，避免模型一直撞同一堵墙
        count = ctx.note_tool_failure(name, f"{type(exc).__name__}: {exc}")
        logger.warning("工具失败 name=%s count=%s err=%s", name, count, exc)
        return ToolMessage(
            content=f"工具 {name} 执行失败（第 {count} 次）：{type(exc).__name__}: {exc}",
            tool_call_id=call_id,
            status="error",
        )

    # 成功一次就把失败计数清零——"连续"失败才算数，不是累计失败
    if isinstance(result, ToolMessage) and result.status == "error":
        count = ctx.note_tool_failure(name, str(result.content))
        logger.info("工具返回错误 name=%s count=%s", name, count)
    else:
        ctx.reset_tool_failures(name)
    return result
