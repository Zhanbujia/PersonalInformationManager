"""后台 worker：轮询数据库队列执行任务。

为什么用数据库轮询而不是 Redis / Celery：任务量很小（每天几十条），
多一个中间件就多一个故障点和一套运维。`for update skip locked` 已经能保证
多进程不抢同一条，扩展性上限足够覆盖这个产品很久。

**一个必须遵守的规矩**：worker 每处理一条任务，都要从 `payload` 里取出
`userId` 再用它开事务。不这么做的话，`force row level security` 会让所有
写操作**静默命中 0 行**，而任务状态还标成成功——比不配 RLS 更危险。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from uuid import UUID

from app.agents.digest.generate import DigestContext, generate_digest
from app.agents.ingest.extract import Extractor
from app.agents.ingest.pipeline import IngestContext, run_ingest
from app.config import settings
from app.infra.db import Database
from app.infra.llm import Embeddings, Models
from app.infra.obs import setup_logging
from app.infra.winloop import ensure_selector_event_loop
from app.repo import infra as infra_repo

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, kinds: list[str], name: str | None = None) -> None:
        self.kinds = kinds
        self.name = name or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.db = Database(
            settings.effective_worker_database_url,
            min_size=1,
            max_size=settings.worker_concurrency + 2,
            name="worker",
        )
        self.models = Models()
        self.embeddings = Embeddings()
        self._stopping = asyncio.Event()
        self._inflight = 0

    # ------------------------------------------------------------------ 生命周期

    async def run(self) -> None:
        setup_logging(settings.env, settings.debug)
        await self.db.open()
        self._install_signal_handlers()
        logger.info("worker 启动 name=%s kinds=%s", self.name, self.kinds)

        sem = asyncio.Semaphore(settings.worker_concurrency)
        try:
            while not self._stopping.is_set():
                async with sem:
                    job = await self._claim()
                    if job is None:
                        await asyncio.sleep(settings.worker_poll_interval)
                        continue
                    # 不 await：允许并发处理，但受 semaphore 限流
                    asyncio.create_task(self._run_job(job))
        finally:
            await self.db.close()
            logger.info("worker 退出 name=%s", self.name)

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stopping.set)
            except NotImplementedError:  # Windows 不支持，靠 KeyboardInterrupt
                pass

    # ------------------------------------------------------------------ 队列

    async def _claim(self) -> dict | None:
        async with self.db.system_tx() as conn:
            return await infra_repo.claim_job(conn, self.name, self.kinds)

    async def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        payload = job["payload"] or {}
        kind = job["kind"]
        try:
            result: dict | None = None
            if kind == "ingest":
                result = await self._handle_ingest(payload)
            elif kind == "digest":
                result = await self._handle_digest(payload)
            else:
                logger.warning("未知任务类型 %s，直接标记完成", kind)

            async with self.db.system_tx() as conn:
                await infra_repo.finish_job(conn, job_id, result=result)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要落库，不能吞
            logger.exception("任务失败 job=%s kind=%s", job_id, kind)
            delay = min(30 * (2 ** max(0, job["attempts"] - 1)), 600)
            async with self.db.system_tx() as conn:
                await infra_repo.retry_job(conn, job_id, f"{type(exc).__name__}: {exc}", delay)

    # ------------------------------------------------------------------ 具体任务

    async def _handle_ingest(self, payload: dict) -> dict:
        user_id = UUID(payload["userId"])
        item_id = UUID(payload["itemId"])
        ctx = IngestContext(
            db=self.db, extractor=Extractor(self.models.extract_llm), embeddings=self.embeddings
        )
        stats = await run_ingest(ctx, user_id=user_id, item_id=item_id)
        return {"itemId": str(item_id), "userId": str(user_id), **stats}

    async def _handle_digest(self, payload: dict) -> dict:
        from datetime import date

        user_id = UUID(payload["userId"])
        ctx = DigestContext(db=self.db, strong_llm=self.models.strong)
        digest_id = await generate_digest(
            ctx,
            user_id=user_id,
            kind=payload["kind"],
            period_start=date.fromisoformat(payload["periodStart"]),
            period_end=date.fromisoformat(payload["periodEnd"]),
        )
        return {"digestId": str(digest_id), "userId": str(user_id)}


def main(kinds: list[str]) -> None:
    logging.basicConfig(level=logging.INFO)
    # 必须在 asyncio.run 之前：Windows 默认的 ProactorEventLoop 跑不了 psycopg 异步连接
    ensure_selector_event_loop()
    asyncio.run(Worker(kinds).run())


if __name__ == "__main__":
    main(["ingest", "digest"])
