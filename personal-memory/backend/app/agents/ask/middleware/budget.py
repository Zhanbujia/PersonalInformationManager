"""拦截点 ②：预算守卫（轮次 / token / 打转）。

三个终止条件必须同时存在，因为它们防的是三件不同的事：

| 条件 | 防什么 |
|---|---|
| 轮次上限 | 模型一直"再查一次" |
| token 上限 | 某一轮检索结果异常大，把整轮预算吃光 |
| 打转检测 | 同一工具同样参数反复调用（模型有时会陷进去） |

结论是"提前结束"，不是"报错"：把已经找到的线索交给用户，比抛一个 504 更有用。
"""

from __future__ import annotations

from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage

from app.agents.ask.context import AskContext


def _closing(text: str) -> AIMessage:
    return AIMessage(content=text)


@before_model(can_jump_to=["end"])
async def budget_guard(state, runtime):
    return await budget_guard_impl(state, runtime)


async def budget_guard_impl(state, runtime):
    """真正的判断逻辑。

    单独抽出来是为了能直接单测——装饰器返回的是 `AgentMiddleware` 对象，
    不能当函数调用，如果测试去调 `budget_guard(...)` 会得到一个
    "对象不可调用"的错误，然后你会以为是自己写错了。
    """
    ctx: AskContext = runtime.context

    if ctx.rounds >= ctx.max_rounds:
        ctx.truncated = True
        ctx.truncate_reason = "max_rounds"
        return {
            "jump_to": "end",
            "messages": [
                _closing(
                    "（这轮已经查了比较多轮，先把我目前找到的线索给你。"
                    "如果还需要更细的内容，可以换个问法继续问。）"
                )
            ],
        }

    if ctx.tokens_in + ctx.tokens_out >= ctx.turn_tokens:
        ctx.truncated = True
        ctx.truncate_reason = "turn_tokens"
        return {
            "jump_to": "end",
            "messages": [_closing("（这轮已达用量上限，先给出目前找到的内容。）")],
        }

    if ctx.tool_calls >= ctx.max_tool_calls:
        ctx.truncated = True
        ctx.truncate_reason = "max_tool_calls"
        return {
            "jump_to": "end",
            "messages": [_closing("（工具调用次数已达上限，先给出目前的结论。）")],
        }

    if len(ctx.fingerprint_ring) >= 2 and ctx.fingerprint_ring[-1] == ctx.fingerprint_ring[-2]:
        ctx.truncated = True
        ctx.truncate_reason = "loop_detected"
        return {
            "jump_to": "end",
            "messages": [
                _closing("（检测到重复检索，说明这条路走不通了。以下是我已经找到的内容。）")
            ],
        }

    return None
