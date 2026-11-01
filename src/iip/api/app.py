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
from datetime import timedelta

import structlog
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from iip import __version__
from iip.api.health import ReadinessChecks
from iip.api.middleware import RequestContextMiddleware
from iip.api.routes import incidents, metrics, system, webhooks
from iip.config import Settings, get_settings
from iip.core.graph import load_graph
from iip.db.engine import create_engine
from iip.ingest.service import CorrelationConfig
from iip.llm.factory import build_provider
from iip.queue.outbox import OutboxRelay
from iip.queue.redis import create_redis
from iip.web import home, pages
from iip.worker.runner import WorkerDeps, create_consumer_redis, run_worker

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    readiness = ReadinessChecks()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        logger.info(
            "api_starting",
            version=__version__,
            git_sha=settings.commit_sha,
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
        # expire_on_commit=False: objects stay readable after commit (e.g. to build
        # the response) without another round trip to the database.
        app.state.sessionmaker = (
            async_sessionmaker(engine, expire_on_commit=False) if engine is not None else None
        )

        redis = None
        if settings.redis_url is not None:
            redis = create_redis(settings.redis_url.get_secret_value())
            client = redis

            async def check_redis() -> None:
                await client.ping()  # pyright: ignore[reportUnknownMemberType]

            readiness.register("redis", check_redis)
        app.state.redis = redis

        llm = build_provider(settings)
        app.state.llm = llm
        if llm is not None:
            logger.info("llm_configured", provider=llm.name, model=llm.model)

        background_stop = asyncio.Event()
        background: list[asyncio.Task[None]] = []

        # The outbox relay publishes jobs the webhook committed (ADR-0008). It runs
        # in the API process because that is where the jobs are written.
        relay: OutboxRelay | None = None
        if app.state.sessionmaker is not None and redis is not None:
            relay = OutboxRelay(
                app.state.sessionmaker, redis, sweep_seconds=settings.outbox_sweep_seconds
            )
            background.append(asyncio.create_task(relay.run(background_stop), name="iip-outbox"))
        app.state.outbox_relay = relay

        worker_task: asyncio.Task[None] | None = None
        consumer_redis = None
        if settings.run_worker_in_api:
            deps = None
            if app.state.sessionmaker is not None and settings.redis_url is not None:
                consumer_redis = create_consumer_redis(settings.redis_url.get_secret_value())
                deps = WorkerDeps(app.state.sessionmaker, consumer_redis, llm, settings)
            worker_task = asyncio.create_task(run_worker(background_stop, deps), name="iip-worker")
            background.append(worker_task)
            logger.info("worker_running_in_api_process", consuming=deps is not None)
        app.state.worker_task = worker_task

        try:
            yield
        finally:
            background_stop.set()
            for task in background:
                # Give in-flight work a bounded time to finish, then cancel.
                try:
                    await asyncio.wait_for(task, timeout=15)
                except TimeoutError:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            if consumer_redis is not None:
                await consumer_redis.aclose()
            if llm is not None:
                await llm.aclose()
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
    # Loaded once at startup: a broken graph file fails fast instead of on the
    # first alert.
    app.state.correlation = CorrelationConfig(
        graph=load_graph(settings.service_graph),
        window=timedelta(seconds=settings.correlation_window_seconds),
    )
    app.state.readiness = readiness

    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    app.include_router(metrics.router)
    app.include_router(home.router)
    app.include_router(webhooks.router)
    app.include_router(incidents.router)
    app.include_router(pages.router)
    return app
