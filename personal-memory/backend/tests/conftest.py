"""pytest 全局夹具。

这里唯一要做的事，是在任何异步测试跑起来之前把 Windows 的事件循环换成
Selector——psycopg 的异步连接不支持 Windows 默认的 ProactorEventLoop。

必须放在 conftest 里：pytest-asyncio 在收集测试之后、执行之前创建循环，
conftest 的 import 时机早于它。
"""

from __future__ import annotations

from app.infra.winloop import ensure_selector_event_loop

# 模块级执行，早于任何测试函数和事件循环创建
ensure_selector_event_loop()
