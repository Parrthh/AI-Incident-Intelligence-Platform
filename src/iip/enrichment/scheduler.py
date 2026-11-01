"""Turn incidents whose debounce has passed into ``enrich`` jobs.

Ingestion sets ``incidents.enrich_after`` (and pushes it back while related
alerts keep arriving). This sweep finds incidents past that time, clears the
marker and writes an ``enrich`` job to the outbox, in one transaction, so a job
is queued exactly when the marker is cleared.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iip.db.models import Incident
from iip.queue.jobs import JOB_ENRICH
from iip.queue.outbox import enqueue

BATCH = 50


async def schedule_due_enrichments(
    session: AsyncSession, stream: str, now: datetime | None = None
) -> list[uuid.UUID]:
    """Queue enrichment for every incident that is due. The caller commits."""
    now = now or datetime.now(UTC)
    due = (
        await session.scalars(
            select(Incident)
            .where(Incident.enrich_after.is_not(None), Incident.enrich_after <= now)
            .order_by(Incident.enrich_after)
            .limit(BATCH)
            # Several workers may sweep at once: each incident goes to one of them.
            .with_for_update(skip_locked=True)
        )
    ).all()
    for incident in due:
        incident.enrich_after = None
        enqueue(session, stream, JOB_ENRICH, {"incident_id": str(incident.id)})
    return [i.id for i in due]
