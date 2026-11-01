"""Resolve incidents whose alerts have all resolved and stayed quiet (M3).

Runs every ``interval`` in the worker. An incident resolves when every alert in
it is resolved and the last one ended at least the quiet period ago. Its
``resolved_at`` is when that last alert ended, which is when the problem
actually stopped; the quiet period only confirms it.

If the process was asleep (the free demo sleeps when idle), the first sweep
after waking catches up, with the same ``resolved_at``.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.core.lifecycle import ACTIVE, Status, ready_to_resolve
from iip.db.models import Alert, Incident, IncidentEvent

logger = structlog.get_logger(__name__)

DEFAULT_INTERVAL_SECONDS = 30.0


async def resolve_quiet_incidents(
    session: AsyncSession, quiet: timedelta, now: datetime | None = None
) -> list[uuid.UUID]:
    """Resolve every incident that is ready. The caller commits."""
    now = now or datetime.now(UTC)
    last_ended = func.max(func.coalesce(Alert.ends_at, Alert.received_at))
    rows = (
        await session.execute(
            select(
                Incident.id,
                func.array_agg(Alert.status),
                last_ended,
                func.count(Alert.id),
            )
            .join(Alert, Alert.incident_id == Incident.id)
            .where(Incident.status.in_([s.value for s in ACTIVE]))
            .group_by(Incident.id)
            .having(func.bool_and(Alert.status == "resolved"))
        )
    ).all()

    resolved: list[uuid.UUID] = []
    for incident_id, statuses, last, count in rows:
        if not ready_to_resolve(statuses, last, now, quiet):
            continue
        changed = await session.execute(
            update(Incident)
            .where(Incident.id == incident_id, Incident.status.in_([s.value for s in ACTIVE]))
            .values(status=Status.RESOLVED.value, resolved_at=last)
            .returning(Incident.id)
        )
        if changed.scalar_one_or_none() is None:  # resolved by someone else meanwhile
            continue
        minutes = round(quiet.total_seconds() / 60, 1)
        quiet_text = f"{minutes:g} minute" + ("" if minutes == 1 else "s")
        alerts_text = "its alert" if count == 1 else f"all {count} of its alerts"
        session.add(
            IncidentEvent(
                incident_id=incident_id,
                at=now,
                kind="incident_resolved",
                payload={
                    "reason": f"{alerts_text.capitalize()} resolved and nothing fired again "
                    f"for {quiet_text}.",
                    "problem_ended_at": last.isoformat(),
                },
            )
        )
        resolved.append(incident_id)
        logger.info("incident_resolved", incident_id=str(incident_id), alerts=count)
    return resolved


async def run_resolver(
    sessionmaker: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    quiet: timedelta,
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    logger.info("resolver_started", quiet_seconds=quiet.total_seconds(), interval=interval)
    try:
        while not stop.is_set():
            try:
                async with sessionmaker() as session:
                    await resolve_quiet_incidents(session, quiet)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 - keep sweeping through database blips
                logger.warning(
                    "resolver_failed", error_type=type(exc).__name__, error=str(exc)[:300]
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
    finally:
        logger.info("resolver_stopped")
