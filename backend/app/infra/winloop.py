"""Windows 事件循环适配。

**问题**：Windows 上 asyncio 默认用 `ProactorEventLoop`，而 psycopg 的
异步连接明确不支持它——一连库就抛：

    psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'
    to run in async mode.

Linux / macOS 默认就是 Selector 系，所以这个坑只在 Windows 出现，
在 CI 上（Linux）永远发现不了。

**为什么单独抽一个模块、由入口显式调用**：
事件循环的策略只对**之后创建的循环**生效。如果放在 `app` 包被 import 的
位置，uvicorn 那时已经建好循环了，改了也没用。所以调用点必须早于循环创建：
`run_api.py`、`workers/worker.py` 的 main、`tests/conftest.py`。
"""

from __future__ import annotations

import asyncio
import sys

# uvicorn 0.36+ 不再读 asyncio 的策略，而是用 loop_factory 显式建循环，
# 并且在 win32 上**硬编码**返回 ProactorEventLoop。所以光设置策略没用，
# 必须把工厂本身换掉。
# uvicorn 只接受 "module:attr" 形式的字符串，解析后直接当作工厂调用（不传参），
# 因此这里给的是事件循环的类本身，而不是一个包装函数。
UVICORN_LOOP_FACTORY = "asyncio:SelectorEventLoop"


def ensure_selector_event_loop() -> bool:
    """在 Windows 上把事件循环策略切换为 Selector。返回是否真的改过。

    幂等：重复调用只会在第一次生效。非 Windows 平台直接返回 False。
    """
    if sys.platform != "win32":
        return False

    policy = asyncio.get_event_loop_policy()
    # getattr 是为了在类型检查器眼里也能跑：Windows 专属类在非 Windows 上不存在
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:  # pragma: no cover - 只可能在非 Windows 发生
        return False
    if isinstance(policy, selector_policy):
        return False

    asyncio.set_event_loop_policy(selector_policy())
    return True
