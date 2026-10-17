"""日志。

开发环境输出人类可读的行；生产环境输出 JSON，方便采集。
所有记录都带 `trace` 字段（一次请求/一个任务一个 id），用于把一条链路的日志串起来。
"""

from __future__ import annotations

import logging
import sys
import uuid

import structlog

_configured = False


def setup_logging(env: str = "dev", debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if env == "prod":
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "app"):
    return structlog.get_logger(name)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]
