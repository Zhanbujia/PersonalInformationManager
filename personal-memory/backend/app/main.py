"""FastAPI 装配。

启动时做四件必须做的事（任何一件失败都不该带着问题上线）：

1. 连数据库，开连接池；
2. **验证 RLS 真的生效**——检查当前连接角色是不是超级用户。
   这是整套多租户隔离的地基，超级用户会绕过所有策略，且不报错；
3. 检查扩展（vector / pg_trgm）是否装上；
4. 如果配了模型密钥，探一次 embedding 维度，防止写库时才炸。

第 2 条值得强调：`force row level security` 管得住表 owner，**管不住超级用户**。
用超级用户连库跑应用，RLS 等于没配，而且完全静默。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import ask, auth, digests, items, library, search, stats, todos
from app.config import settings
from app.errors import AppError
from app.infra.db import Database, fetch_one
from app.infra.llm import Embeddings, Models
from app.infra.obs import setup_logging

logger = logging.getLogger(__name__)


async def _startup_checks(db: Database) -> None:
    async with db.system_tx() as conn:
        role = await fetch_one(
            conn,
            "select current_user as name, (select rolsuper from pg_roles where rolname = current_user) as is_super",
        )
        exts = await fetch_one(
            conn,
            "select count(*) as n from pg_extension where extname in ('vector','pg_trgm','pgcrypto')",
        )

    if role and role["is_super"]:
        msg = (
            f"数据库当前连接角色 `{role['name']}` 是超级用户，行级安全策略会被完全绕过。"
            "请改用普通角色（如 pim_app）连接；生产环境将拒绝启动。"
        )
        if settings.env == "prod":
            raise RuntimeError(msg)
        logger.warning(msg)

    if not exts or exts["n"] < 3:
        logger.warning(
            "缺少数据库扩展（vector / pg_trgm / pgcrypto 需全部就位），"
            "检索与去重可能不可用。请检查 deploy/init/01_roles.sql。"
        )

    # 没单独配 worker 连接时，读 jobs 会落到应用角色上。
    # 而 jobs 对应用角色只开了 insert 和 id 列，于是任务轮询接口会 500。
    # 这是个很容易漏的配置：服务能起来、记录能写，只有去查任务进度才炸。
    if not settings.worker_database_url:
        logger.warning(
            "未配置 WORKER_DATABASE_URL，后台任务连接退回应用角色。"
            "jobs 表对应用角色只有 insert 与 id 列权限，任务进度接口会 permission denied。"
            "请把 WORKER_DATABASE_URL 指向 pim_worker 角色。"
        )

    if settings.llm_provider == "mock" or settings.llm_api_key or settings.embedding_api_key:
        try:
            emb = Embeddings()
            dim = await emb.probe_dim()
            logger.info("embedding 维度自检通过 dim=%s model=%s", dim, settings.embedding_model)
        except Exception:  # noqa: BLE001 - 模型不可用不该阻塞启动
            logger.exception("embedding 自检失败：向量化将降级为纯关键词检索")
    else:
        logger.warning("未配置模型密钥（LLM_API_KEY / EMBEDDING_API_KEY），分析功能不可用")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.env, settings.debug)
    structlog.contextvars.bind_contextvars(app=settings.app_name, env=settings.env)

    db = Database(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        timeout=settings.db_pool_timeout,
        name="app",
    )
    worker_db = Database(
        settings.effective_worker_database_url,
        min_size=1,
        max_size=5,
        timeout=settings.db_pool_timeout,
        name="worker",
    )
    await db.open()
    await worker_db.open()

    app.state.db = db
    app.state.worker_db = worker_db
    app.state.models = Models()
    app.state.embeddings = Embeddings()

    await _startup_checks(db)
    logger.info("服务已启动 env=%s", settings.env)
    try:
        yield
    finally:
        await db.close()
        await worker_db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    prefix = settings.api_prefix
    app.include_router(auth.router, prefix=prefix)
    app.include_router(items.router, prefix=prefix)
    app.include_router(library.router, prefix=prefix)
    app.include_router(search.router, prefix=prefix)
    app.include_router(ask.router, prefix=prefix)
    app.include_router(todos.router, prefix=prefix)
    app.include_router(digests.router, prefix=prefix)
    app.include_router(stats.router, prefix=prefix)

    @app.get(f"{prefix}/health")
    async def health(request: Request) -> dict:
        ok = await request.app.state.db.healthy()
        return {"status": "ok" if ok else "degraded", "db": ok, "version": "0.1.0"}

    return app


app = create_app()
