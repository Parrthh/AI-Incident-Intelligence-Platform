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

from sqlalchemy import and_, any_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from iip.db.models import Alert, Analysis, Evidence, Incident, IncidentEvent

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


async def incident_timeline(session: AsyncSession, incident_id: uuid.UUID) -> list[IncidentEvent]:
    """Timeline events, oldest first. Ties keep insertion order."""
    return list(
        (
            await session.scalars(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident_id)
                .order_by(IncidentEvent.at, IncidentEvent.created_at)
            )
        ).all()
    )


@dataclass(frozen=True)
class AnalysisView:
    latest_done: Analysis | None
    in_progress: bool  # a newer analysis is being written (new evidence arrived)
    latest_failed: Analysis | None  # newest run, if it failed


async def incident_analysis(session: AsyncSession, incident_id: uuid.UUID) -> AnalysisView:
    rows = list(
        (
            await session.scalars(
                select(Analysis)
                .where(Analysis.incident_id == incident_id, Analysis.kind == "analysis")
                .order_by(Analysis.created_at.desc())
                .limit(10)
            )
        ).all()
    )
    done = next((r for r in rows if r.status == "done"), None)
    newest = rows[0] if rows else None
    return AnalysisView(
        latest_done=done,
        in_progress=newest is not None and newest.status == "pending",
        latest_failed=newest if newest is not None and newest.status == "failed" else None,
    )


async def incident_evidence(session: AsyncSession, incident_id: uuid.UUID) -> list[Evidence]:
    """Evidence items, oldest observation first."""
    return list(
        (
            await session.scalars(
                select(Evidence)
                .where(Evidence.incident_id == incident_id)
                .order_by(Evidence.observed_at, Evidence.id)
            )
        ).all()
    )


@dataclass(frozen=True)
class SiteStats:
    incidents: int
    alerts: int
    summaries: int
    median_summary_ms: int | None


async def site_stats(session: AsyncSession) -> SiteStats:
    """Counts shown on the home page. All real, straight from this database."""
    incidents = await session.scalar(select(func.count()).select_from(Incident)) or 0
    alerts = await session.scalar(select(func.count()).select_from(Alert)) or 0
    summaries = (
        await session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.status == "done")
        )
        or 0
    )
    median = await session.scalar(
        select(func.percentile_cont(0.5).within_group(Analysis.latency_ms)).where(
            Analysis.status == "done", Analysis.latency_ms.is_not(None)
        )
    )
    return SiteStats(
        incidents=incidents,
        alerts=alerts,
        summaries=summaries,
        median_summary_ms=round(median) if median is not None else None,
    )


async def latest_summarized(session: AsyncSession) -> tuple[Incident, Alert, Analysis] | None:
    """Newest incident that has a finished summary, with its first alert."""
    row = (
        await session.execute(
            select(Incident, Analysis)
            .join(Analysis, Analysis.incident_id == Incident.id)
            .where(Analysis.status == "done", Analysis.kind == "summary")
            .order_by(Incident.started_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    incident, analysis = row
    alert = await session.scalar(
        select(Alert).where(Alert.incident_id == incident.id).order_by(Alert.starts_at).limit(1)
    )
    if alert is None:
        return None
    return incident, alert, analysis
