"""API 启动入口。

为什么不能直接 `uvicorn app.main:app`：
uvicorn 0.36 起改用 loop_factory 建事件循环，并且在 Windows 上**硬编码**
返回 ProactorEventLoop；而 psycopg 的异步连接只支持 Selector 系，于是启动时
连接池第一次拿连接就抛 InterfaceError，服务起不来。

光调用 asyncio.set_event_loop_policy 也没用——uvicorn 不再走策略。
必须把 loop 工厂显式换成 asyncio.SelectorEventLoop。

用法：
    python run_api.py            # 默认 127.0.0.1:8000，开启热重载
    API_RELOAD=0 python run_api.py
    API_PORT=8080 python run_api.py
"""

from __future__ import annotations

import os
import sys

import uvicorn

from app.infra.winloop import UVICORN_LOOP_FACTORY, ensure_selector_event_loop

# 双保险：uvicorn 那条路走 loop 参数，其它直接 asyncio.run 的代码走策略。
# 两者都必须早于任何事件循环创建。
ensure_selector_event_loop()


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD", "1") == "1",
        # Windows 必须用 SelectorEventLoop，否则 psycopg 异步连接直接拒绝
        loop=UVICORN_LOOP_FACTORY if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
