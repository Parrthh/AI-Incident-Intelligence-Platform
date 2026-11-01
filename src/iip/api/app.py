"""FastAPI application factory.

``create_app`` builds a fresh app from explicit settings. Tests call it with their
own settings; production calls it once through ``iip api``. Avoiding a
module-level ``app = FastAPI()`` keeps tests isolated from each other and from
the real environment.

Startup opens the Postgres pool and Redis client and registers them as readiness
checks; shutdown closes them. If a URL isn't configured (allowed outside
production), that dependency is simply absent.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import structlog
from fastapi import FastAPI
from sqlalchemy import text

from iip import __version__
from iip.api.health import ReadinessChecks
from iip.api.middleware import RequestContextMiddleware
from iip.api.routes import system
from iip.config import Settings, get_settings
from iip.db.engine import create_engine
from iip.queue.redis import create_redis
from iip.worker.runner import run_worker

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    readiness = ReadinessChecks()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        logger.info(
            "api_starting",
            version=__version__,
            git_sha=settings.git_sha,
            env=settings.env.value,
        )

        engine = None
        if settings.database_url is not None:
            engine = create_engine(
                settings.database_url.get_secret_value(), pool_size=settings.db_pool_size
            )
            db = engine

            async def check_db() -> None:
                async with db.connect() as conn:
                    await conn.execute(text("SELECT 1"))

            readiness.register("database", check_db)
        app.state.db_engine = engine

        redis = None
        if settings.redis_url is not None:
            redis = create_redis(settings.redis_url.get_secret_value())
            client = redis

            async def check_redis() -> None:
                await client.ping()  # pyright: ignore[reportUnknownMemberType]

            readiness.register("redis", check_redis)
        app.state.redis = redis

        worker_stop = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if settings.run_worker_in_api:
            worker_task = asyncio.create_task(run_worker(worker_stop), name="iip-worker")
            logger.info("worker_running_in_api_process")
        app.state.worker_task = worker_task

        try:
            yield
        finally:
            if worker_task is not None:
                worker_stop.set()
                # Give in-flight work a bounded time to finish, then cancel.
                try:
                    await asyncio.wait_for(worker_task, timeout=15)
                except TimeoutError:
                    worker_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker_task
            if redis is not None:
                await redis.aclose()
            if engine is not None:
                await engine.dispose()
            logger.info("api_stopped")

    app = FastAPI(
        title="AI Incident Intelligence Platform",
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are handy locally but expose the full API surface on a
        # public demo; keep them off in production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.readiness = readiness

    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    return app
