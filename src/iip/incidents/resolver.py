"""Resolve incidents whose alerts have all resolved and stayed quiet (M3).

Called by the worker's sweeper (``incidents/sweeper.py``). An incident resolves when every alert in
it is resolved and the last one ended at least the quiet period ago. Its
``resolved_at`` is when that last alert ended, which is when the problem
actually stopped; the quiet period only confirms it.

If the process was asleep (the free demo sleeps when idle), the first sweep
after waking catches up, with the same ``resolved_at``.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from iip.core.lifecycle import ACTIVE, Status, ready_to_resolve
from iip.db.models import Alert, Incident, IncidentEvent

logger = structlog.get_logger(__name__)


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
