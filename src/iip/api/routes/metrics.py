"""``GET /metrics`` for Prometheus.

Backlog gauges (queue length, pending jobs, dead letters, outbox lag) are read
here, at scrape time, with a short timeout each. A slow or failed read leaves
the previous value and counts a collection error, so a Redis or database blip
never makes the scrape itself fail.
"""

import asyncio
import hmac
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select

from iip.config import Settings
from iip.db.models import OutboxMessage
from iip.observability.metrics import (
    OUTBOX_LAG,
    OUTBOX_UNPUBLISHED,
    QUEUE_DEAD_LETTERS,
    QUEUE_LENGTH,
    QUEUE_PENDING,
    SCRAPE_ERRORS,
)
from iip.queue import admin

logger = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

GAUGE_TIMEOUT_SECONDS = 2.0


def _authorized(request: Request, settings: Settings) -> bool:
    if settings.metrics_token is None:
        return True
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    expected = settings.metrics_token.get_secret_value()
    return scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.encode())


async def _queue_gauges(request: Request, settings: Settings) -> None:
    redis = request.app.state.redis
    if redis is None:
        return
    stats = await admin.queue_stats(redis, settings.jobs_stream)
    QUEUE_LENGTH.set(stats.stream_length)
    QUEUE_PENDING.set(stats.pending)
    QUEUE_DEAD_LETTERS.set(stats.dead_letters)


async def _outbox_gauges(request: Request) -> None:
    sessionmaker = request.app.state.sessionmaker
    if sessionmaker is None:
        return
    async with sessionmaker() as session:
        count, oldest = (
            await session.execute(
                select(func.count(), func.min(OutboxMessage.created_at)).where(
                    OutboxMessage.published_at.is_(None)
                )
            )
        ).one()
    OUTBOX_UNPUBLISHED.set(count)
    OUTBOX_LAG.set(0 if oldest is None else (datetime.now(UTC) - oldest).total_seconds())


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if not _authorized(request, settings):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
    for source, read in (
        ("redis", _queue_gauges(request, settings)),
        ("database", _outbox_gauges(request)),
    ):
        try:
            await asyncio.wait_for(read, timeout=GAUGE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - a scrape must not fail on a blip
            SCRAPE_ERRORS.labels(source=source).inc()
            logger.warning("metrics_gauge_failed", source=source, error=type(exc).__name__)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
