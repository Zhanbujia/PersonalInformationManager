"""Windows 事件循环适配的回归测试。

psycopg 的异步连接不能在 ProactorEventLoop 上跑，而 Windows 默认就是它。
这个模块保证入口在创建循环前把策略换成 Selector。
在 Linux 上它应该什么都不做——那里本来就对。
"""

from __future__ import annotations

import asyncio
import sys

from app.infra.winloop import ensure_selector_event_loop


def test_repeated_calls_are_noop() -> None:
    """幂等：调用两次不该重复设置策略，也不该抛。"""
    ensure_selector_event_loop()  # 第一次（conftest 里可能已经调过）
    assert ensure_selector_event_loop() is False


def test_windows_uses_selector_policy() -> None:
    ensure_selector_event_loop()
    if sys.platform == "win32":
        policy = asyncio.get_event_loop_policy()
        assert isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy), (
            "Windows 上必须切到 Selector 策略，否则 psycopg 异步连接直接抛 "
            "InterfaceError，而且是运行到连库那一刻才炸"
        )
    else:
        # 非 Windows 不该改动全局策略
        assert ensure_selector_event_loop() is False
