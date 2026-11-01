"""Store incoming alerts and group them into incidents.

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
   firing alert groups it, so duplicates never open duplicate incidents.
3. A new firing alert is **correlated** (``core/correlation.py``): it joins an open
   incident on the same or a connected service, or opens a new one. The decision
   and its reason go on the incident's timeline.
4. Resolved is final for an alert occurrence. A delayed retry of an old "firing"
   can't reopen it. Incidents themselves resolve later, once all their alerts
   have resolved and a quiet period has passed (``incidents/resolver.py``).

Correlation reads open incidents and then writes, so two alerts arriving at the
same moment (Alertmanager sends one request per alert group, in parallel) could
both decide "no incident yet" and open two. Before correlating, the transaction
takes Postgres advisory locks named after each firing alert's service, the
services connected to it, and its fingerprint. Two alerts that could affect each
other's decision always share a lock name, so they take turns; unrelated alerts
don't wait for each other. All locks are taken in one sorted batch, so two
transactions can't deadlock, and they are released at commit.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import (
    Boolean,
    ColumnElement,
    Label,
    case,
    exists,
    literal_column,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from iip.core.correlation import AlertFacts, OpenIncident, correlate
from iip.core.graph import EMPTY_GRAPH, ServiceGraph
from iip.core.lifecycle import Status
from iip.db.models import Alert, Incident, IncidentEvent
from iip.ingest.alertmanager import AlertmanagerAlert, AlertmanagerPayload

logger = structlog.get_logger(__name__)

SOURCE_ALERTMANAGER = "alertmanager"


@dataclass(frozen=True)
class CorrelationConfig:
    graph: ServiceGraph = EMPTY_GRAPH
    window: timedelta = timedelta(minutes=10)


DEFAULT_CORRELATION = CorrelationConfig()

# Arbitrary constant naming the correlation locks (migrations use 7392001).
CORRELATION_LOCK_CLASS = 7392002

SEVERITY_RANK = {"critical": 3, "error": 2, "warning": 1, "info": 0}


@dataclass
class IngestResult:
    received: int = 0
    new_alerts: int = 0
    updated_alerts: int = 0
    new_incident_ids: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    attached_incident_ids: list[uuid.UUID] = field(default_factory=list[uuid.UUID])
    # Kept for API compatibility. Incidents now resolve after a quiet period, in
    # the background, so ingestion itself never resolves one.
    resolved_incident_ids: list[uuid.UUID] = field(default_factory=list[uuid.UUID])


async def ingest_alertmanager(
    session: AsyncSession,
    payload: AlertmanagerPayload,
    correlation: CorrelationConfig = DEFAULT_CORRELATION,
) -> IngestResult:
    """Store every alert in the payload. The caller owns the transaction (commit)."""
    result = IngestResult(received=len(payload.alerts))
    await _lock_for_correlation(session, payload, correlation)
    for alert in payload.alerts:
        await _ingest_one(session, alert, result, correlation)
    logger.info(
        "alerts_ingested",
        received=result.received,
        new_alerts=result.new_alerts,
        updated_alerts=result.updated_alerts,
        new_incidents=len(result.new_incident_ids),
        attached=len(result.attached_incident_ids),
    )
    return result


async def _ingest_one(
    session: AsyncSession,
    alert: AlertmanagerAlert,
    result: IngestResult,
    correlation: CorrelationConfig,
) -> None:
    previous_status = await session.scalar(
        select(Alert.status).where(
            Alert.fingerprint == alert.fingerprint, Alert.starts_at == alert.starts_at
        )
    )
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
        await _correlate(session, alert, alert_id, result, correlation)
    elif status == "resolved" and previous_status == "firing" and incident_id is not None:
        session.add(
            IncidentEvent(
                incident_id=incident_id,
                at=alert.ends_at or datetime.now(UTC),
                kind="alert_resolved",
                payload={
                    "alert_id": str(alert_id),
                    "alertname": alert.alertname,
                    "service": alert.service,
                },
            )
        )


async def _correlate(
    session: AsyncSession,
    alert: AlertmanagerAlert,
    alert_id: uuid.UUID,
    result: IngestResult,
    correlation: CorrelationConfig,
) -> None:
    facts = AlertFacts(
        fingerprint=alert.fingerprint,
        alertname=alert.alertname,
        service=alert.service,
        starts_at=alert.starts_at,
    )
    candidates = await _candidate_incidents(session, facts, correlation)
    decision = correlate(facts, candidates, correlation.graph, correlation.window)
    alert_event = {
        "alert_id": str(alert_id),
        "alertname": alert.alertname,
        "service": alert.service,
        "rule": decision.rule.value,
        "reason": decision.reason,
    }

    if decision.incident_id is None:
        incident = Incident(
            title=alert.title,
            status=Status.OPEN.value,
            severity=alert.severity,
            services=[alert.service] if alert.service else [],
            started_at=alert.starts_at,
        )
        session.add(incident)
        await session.flush()  # assigns incident.id
        incident_id = incident.id
        result.new_incident_ids.append(incident_id)
        session.add(
            IncidentEvent(
                incident_id=incident_id,
                at=alert.starts_at,
                kind="incident_opened",
                payload=alert_event,
            )
        )
        logger.info("incident_opened", incident_id=str(incident_id), alertname=alert.alertname)
    else:
        incident_id = decision.incident_id
        await _merge_into(session, incident_id, alert)
        if incident_id not in result.attached_incident_ids:
            result.attached_incident_ids.append(incident_id)
        session.add(
            IncidentEvent(
                incident_id=incident_id,
                at=alert.starts_at,
                kind="alert_attached",
                payload=alert_event,
            )
        )
        logger.info(
            "alert_attached",
            incident_id=str(incident_id),
            alertname=alert.alertname,
            rule=decision.rule.value,
        )

    await session.execute(update(Alert).where(Alert.id == alert_id).values(incident_id=incident_id))


async def _lock_for_correlation(
    session: AsyncSession, payload: AlertmanagerPayload, correlation: CorrelationConfig
) -> None:
    names: set[str] = set()
    for alert in payload.alerts:
        if alert.status != "firing":
            continue
        names.add(f"fp:{alert.fingerprint}")
        if alert.service:
            names.add(f"svc:{alert.service}")
            names.update(f"svc:{n}" for n in correlation.graph.neighbors(alert.service))
    if not names:
        return
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(:cls, h) "
            "FROM (SELECT DISTINCT hashtext(n) AS h FROM unnest(CAST(:names AS text[])) AS n "
            "ORDER BY h) AS keys"
        ),
        {"cls": CORRELATION_LOCK_CLASS, "names": sorted(names)},
    )


async def _candidate_incidents(
    session: AsyncSession, alert: AlertFacts, correlation: CorrelationConfig
) -> list[OpenIncident]:
    """Open incidents that could match: same fingerprint, or a nearby service in the window.

    Filtering in SQL keeps this fast when many incidents are open; the decision
    itself is made by ``correlate`` in plain Python.
    """
    same_fingerprint = exists().where(
        Alert.incident_id == Incident.id, Alert.fingerprint == alert.fingerprint
    )
    conditions: list[ColumnElement[bool]] = [same_fingerprint]
    if alert.service is not None:
        nearby = sorted({alert.service, *correlation.graph.neighbors(alert.service)})
        conditions.append(
            Incident.services.overlap(nearby)
            & Incident.started_at.between(
                alert.starts_at - correlation.window, alert.starts_at + correlation.window
            )
        )
    rows = (
        await session.execute(
            select(Incident.id, Incident.started_at, Incident.services, same_fingerprint)
            .where(Incident.status != Status.RESOLVED.value, or_(*conditions))
            .order_by(Incident.started_at.desc())
            .limit(50)
        )
    ).all()
    return [
        OpenIncident(
            id=incident_id,
            started_at=started_at,
            services=frozenset(services),
            fingerprints=frozenset({alert.fingerprint}) if has_fingerprint else frozenset(),
        )
        for incident_id, started_at, services, has_fingerprint in rows
    ]


async def _merge_into(
    session: AsyncSession, incident_id: uuid.UUID, alert: AlertmanagerAlert
) -> None:
    """Add the alert's service to the incident and raise its severity if needed."""
    incident = await session.get(Incident, incident_id, with_for_update=True)
    if incident is None:  # pragma: no cover - locked above, can't vanish
        return
    if alert.service and alert.service not in incident.services:
        incident.services = [*incident.services, alert.service]
    if SEVERITY_RANK.get(alert.severity or "", -1) > SEVERITY_RANK.get(incident.severity or "", -1):
        incident.severity = alert.severity


def _inserted_flag() -> Label[bool]:
    """``xmax = 0`` is true only for a row this statement inserted (see module docs)."""
    return literal_column("(xmax = 0)", type_=Boolean).label("inserted")


async def get_incident(session: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
    return await session.scalar(select(Incident).where(Incident.id == incident_id))
