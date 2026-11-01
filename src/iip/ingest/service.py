"""Store incoming alerts and open incidents for new ones.

Idempotency is the core requirement. Alertmanager retries failed deliveries and
re-sends active alerts every ``repeat_interval``, so the same alert arrives many
times. Each delivery must leave the database in the same state as one delivery.

How it's done, per alert, inside one transaction:

1. ``INSERT ... ON CONFLICT (fingerprint, starts_at) DO UPDATE`` stores the alert or
   updates the existing row (for example firing -> resolved). Postgres makes this
   atomic, so two concurrent deliveries of the same alert can't create two rows.
2. ``RETURNING (xmax = 0) AS inserted`` tells us whether this call created the row.
   ``xmax`` is a Postgres system column that is 0 on a freshly inserted row and set
   when an existing row is updated. Only the delivery that actually inserted a
   firing alert opens an incident, so duplicates never open duplicate incidents.
3. When an alert resolves, its incident is resolved too (M1 has one incident per
   alert; M3 replaces this with correlation across alerts).
4. Resolved is final. A delayed retry of an old "firing" delivery can't reopen it.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import Boolean, Label, case, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from iip.db.models import Alert, Incident
from iip.ingest.alertmanager import AlertmanagerAlert, AlertmanagerPayload

logger = structlog.get_logger(__name__)

SOURCE_ALERTMANAGER = "alertmanager"


@dataclass
class IngestResult:
    received: int = 0
    new_alerts: int = 0
    updated_alerts: int = 0
    new_incident_ids: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    resolved_incident_ids: list[uuid.UUID] = field(default_factory=list[uuid.UUID])


async def ingest_alertmanager(session: AsyncSession, payload: AlertmanagerPayload) -> IngestResult:
    """Store every alert in the payload. The caller owns the transaction (commit)."""
    result = IngestResult(received=len(payload.alerts))
    for alert in payload.alerts:
        await _ingest_one(session, alert, result)
    logger.info(
        "alerts_ingested",
        received=result.received,
        new_alerts=result.new_alerts,
        updated_alerts=result.updated_alerts,
        new_incidents=len(result.new_incident_ids),
        resolved_incidents=len(result.resolved_incident_ids),
    )
    return result


async def _ingest_one(
    session: AsyncSession, alert: AlertmanagerAlert, result: IngestResult
) -> None:
    values = {
        "source": SOURCE_ALERTMANAGER,
        "fingerprint": alert.fingerprint,
        "status": alert.status,
        "alertname": alert.alertname,
        "service": alert.service,
        "severity": alert.severity,
        "labels": alert.labels,
        "annotations": alert.annotations,
        "starts_at": alert.starts_at,
        "ends_at": alert.ends_at,
        "raw": alert.raw(),
    }
    stmt = insert(Alert).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_alerts_fingerprint_starts_at",
        set_={
            # Deliveries can arrive out of order (a delayed retry of an old "firing"
            # after the "resolved"). Resolved is final for this occurrence: a new
            # firing of the same alert comes with a new startsAt, so a new row.
            "status": case(
                (Alert.status == "resolved", Alert.status),
                else_=stmt.excluded.status,
            ),
            "ends_at": case(
                (Alert.status == "resolved", Alert.ends_at),
                else_=stmt.excluded.ends_at,
            ),
            "annotations": stmt.excluded.annotations,
            "raw": stmt.excluded.raw,
            "received_at": datetime.now(UTC),
        },
    ).returning(Alert.id, Alert.incident_id, Alert.status, _inserted_flag())

    row = (await session.execute(stmt)).one()
    alert_id, incident_id, status, inserted = row

    if inserted:
        result.new_alerts += 1
    else:
        result.updated_alerts += 1

    if inserted and status == "firing":
        incident = Incident(
            title=alert.title,
            status="open",
            severity=alert.severity,
            services=[alert.service] if alert.service else [],
            started_at=alert.starts_at,
        )
        session.add(incident)
        await session.flush()  # assigns incident.id
        await session.execute(
            update(Alert).where(Alert.id == alert_id).values(incident_id=incident.id)
        )
        result.new_incident_ids.append(incident.id)
        logger.info("incident_opened", incident_id=str(incident.id), alertname=alert.alertname)

    elif status == "resolved" and incident_id is not None:
        resolved_at = alert.ends_at or datetime.now(UTC)
        resolved = await session.execute(
            update(Incident)
            .where(Incident.id == incident_id, Incident.status == "open")
            .values(status="resolved", resolved_at=resolved_at)
            .returning(Incident.id)
        )
        if resolved.scalar_one_or_none() is not None:
            result.resolved_incident_ids.append(incident_id)
            logger.info("incident_resolved", incident_id=str(incident_id))


def _inserted_flag() -> Label[bool]:
    """``xmax = 0`` is true only for a row this statement inserted (see module docs)."""
    return literal_column("(xmax = 0)", type_=Boolean).label("inserted")


async def get_incident(session: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
    return await session.scalar(select(Incident).where(Incident.id == incident_id))
