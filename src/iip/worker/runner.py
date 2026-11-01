"""Worker entrypoints.

The worker consumes the jobs stream (ADR-0002) and runs handlers from
``jobs.py``. It runs either as its own process (``iip worker``, for
self-hosters) or as a task inside the API process (the free Render demo, which
has no background workers, ADR-0004).

Without a database and Redis there is nothing to consume, so the worker only
logs a heartbeat. That keeps local runs and unit tests simple.

The loop must stop promptly when asked. ``stop`` is an ``asyncio.Event`` set on
shutdown (SIGTERM from Docker or Render).
"""

import asyncio
import os
import signal
import socket
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.config import Settings, get_settings
from iip.db.engine import create_engine
from iip.llm.base import LLMProvider
from iip.llm.factory import build_provider
from iip.queue.jobs import CONSUMER_GROUP
from iip.queue.redis import create_redis
from iip.queue.streams import StreamQueue
from iip.worker.consumer import DEFAULT_RECLAIM_INTERVAL_SECONDS, run_consumer
from iip.worker.jobs import build_handlers

logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 60.0
BLOCK_MS = 30_000
# Longer than the blocking read, so the client doesn't give up before Redis answers.
CONSUMER_SOCKET_TIMEOUT_SECONDS = BLOCK_MS / 1000 + 10


@dataclass(frozen=True)
class WorkerDeps:
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis  # a client made with CONSUMER_SOCKET_TIMEOUT_SECONDS
    llm: LLMProvider | None
    settings: Settings


def consumer_name() -> str:
    """Unique per process, readable in ``XINFO CONSUMERS`` output."""
    return f"{socket.gethostname()}-{os.getpid()}"


def create_consumer_redis(url: str) -> Redis:
    return create_redis(url, socket_timeout=CONSUMER_SOCKET_TIMEOUT_SECONDS)


async def run_worker(
    stop: asyncio.Event,
    deps: WorkerDeps | None = None,
    *,
    heartbeat_seconds: float | None = None,
) -> None:
    if deps is None:
        await _heartbeat(stop, heartbeat_seconds or HEARTBEAT_INTERVAL_SECONDS)
        return
    queue = StreamQueue(
        redis=deps.redis,
        stream=deps.settings.jobs_stream,
        group=CONSUMER_GROUP,
        consumer=consumer_name(),
        block_ms=BLOCK_MS,
        base_backoff_s=deps.settings.queue_retry_base_seconds,
    )
    handlers = build_handlers(
        deps.sessionmaker, deps.llm, llm_max_retries=deps.settings.llm_max_retries
    )
    # Sweep for abandoned jobs at least as often as the shortest retry wait.
    reclaim = min(DEFAULT_RECLAIM_INTERVAL_SECONDS, deps.settings.queue_retry_base_seconds)
    await run_consumer(queue, handlers, stop, reclaim_interval_seconds=reclaim)


async def _heartbeat(stop: asyncio.Event, interval: float) -> None:
    logger.info("worker_started", mode="heartbeat")
    beats = 0
    try:
        while not stop.is_set():
            beats += 1
            logger.info("worker_heartbeat", beats=beats)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
    finally:
        logger.info("worker_stopped", beats=beats)


async def run_worker_until_signalled(settings: Settings | None = None) -> None:
    """Entrypoint for the standalone ``iip worker`` process."""
    settings = settings or get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    if settings.database_url is None or settings.redis_url is None:
        logger.warning("worker_missing_dependencies", database=bool(settings.database_url))
        await run_worker(stop)
        return

    engine = create_engine(
        settings.database_url.get_secret_value(), pool_size=settings.db_pool_size
    )
    redis = create_consumer_redis(settings.redis_url.get_secret_value())
    llm = build_provider(settings)
    try:
        deps = WorkerDeps(async_sessionmaker(engine, expire_on_commit=False), redis, llm, settings)
        await run_worker(stop, deps)
    finally:
        if llm is not None:
            await llm.aclose()
        await redis.aclose()
        await engine.dispose()
