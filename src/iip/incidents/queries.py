"""Read-side queries for incidents, shared by the JSON API and the web UI.

Pagination uses a cursor (keyset), not page numbers. The list is ordered by
(started_at DESC, id DESC); the cursor encodes the last row's pair, and the next
page asks for rows strictly "after" it. Unlike OFFSET, this stays fast on large
tables (no scanning and discarding skipped rows) and doesn't skip or repeat rows
when new incidents arrive while someone is paging.
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, any_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from iip.db.models import Alert, Analysis, Incident

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class InvalidCursorError(ValueError):
    pass


def encode_cursor(started_at: datetime, incident_id: uuid.UUID) -> str:
    raw = f"{started_at.isoformat()}|{incident_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        started, ident = raw.split("|", 1)
        return datetime.fromisoformat(started), uuid.UUID(ident)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError("invalid cursor") from exc


@dataclass(frozen=True)
class IncidentPage:
    items: list[tuple[Incident, Analysis | None]]
    next_cursor: str | None


async def list_incidents(
    session: AsyncSession,
    *,
    status: str | None = None,
    service: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> IncidentPage:
    limit = max(1, min(limit, MAX_PAGE_SIZE))
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status == status)
    if service:
        # SQL: :service = ANY(services)
        stmt = stmt.where(literal(service) == any_(Incident.services))
    if cursor:
        after_started, after_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                Incident.started_at < after_started,
                and_(Incident.started_at == after_started, Incident.id < after_id),
            )
        )
    # Fetch one extra row to know whether another page exists.
    stmt = stmt.order_by(Incident.started_at.desc(), Incident.id.desc()).limit(limit + 1)
    incidents = list((await session.scalars(stmt)).all())

    has_more = len(incidents) > limit
    incidents = incidents[:limit]
    summaries = await latest_summaries(session, [i.id for i in incidents])
    next_cursor = (
        encode_cursor(incidents[-1].started_at, incidents[-1].id)
        if has_more and incidents
        else None
    )
    return IncidentPage(
        items=[(i, summaries.get(i.id)) for i in incidents], next_cursor=next_cursor
    )


async def latest_summaries(
    session: AsyncSession, incident_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Analysis]:
    """Newest analysis per incident, in one query (DISTINCT ON), not one per row."""
    if not incident_ids:
        return {}
    stmt = (
        select(Analysis)
        .where(Analysis.incident_id.in_(incident_ids), Analysis.kind == "summary")
        .order_by(Analysis.incident_id, Analysis.created_at.desc())
        .distinct(Analysis.incident_id)
    )
    return {a.incident_id: a for a in (await session.scalars(stmt)).all()}


async def get_incident_detail(
    session: AsyncSession, incident_id: uuid.UUID
) -> tuple[Incident, list[Alert], Analysis | None] | None:
    incident = await session.scalar(
        select(Incident).where(Incident.id == incident_id).options(selectinload(Incident.alerts))
    )
    if incident is None:
        return None
    alerts = sorted(incident.alerts, key=lambda a: a.starts_at)
    summary = (await latest_summaries(session, [incident_id])).get(incident_id)
    return incident, alerts, summary
