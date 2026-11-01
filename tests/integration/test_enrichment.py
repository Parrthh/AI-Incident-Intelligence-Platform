"""Evidence collection: debounce, sources, partial failure, stable IDs, deploys."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine
from iip.db.models import Deploy, Evidence, Incident, IncidentEvent, OutboxMessage
from iip.enrichment.base import EnrichmentContext, EvidenceItem, EvidenceKind
from iip.enrichment.runner import Enricher
from iip.enrichment.scheduler import schedule_due_enrichments
from iip.enrichment.sources.database import AlertSource, DeploySource
from tests.fixtures.loader import load_alertmanager
from tests.integration.helpers import create_incident, sessionmaker_for

pytestmark = pytest.mark.integration

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def app(migrated_database_url: str) -> FastAPI:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr(migrated_database_url),
        webhook_token=SecretStr("t"),
        service_graph="builtin:otel-demo",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


class BrokenSource:
    name = "logs"

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        raise ConnectionError("connection refused")


class HangingSource:
    name = "traces"

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        await asyncio.sleep(30)
        return []


class FixedSource:
    name = "metrics"

    def __init__(self, items: list[EvidenceItem]) -> None:
        self.items = items

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        return self.items


async def add_deploy(db: AsyncEngine, service: str, minutes_before: float, version: str) -> None:
    async with sessionmaker_for(db)() as session:
        incident = (await session.scalars(select(Incident))).first()
        assert incident is not None
        session.add(
            Deploy(
                service=service,
                version=version,
                deployed_at=incident.started_at - timedelta(minutes=minutes_before),
            )
        )
        await session.commit()


async def evidence_rows(db: AsyncEngine) -> list[Evidence]:
    async with sessionmaker_for(db)() as session:
        return list((await session.scalars(select(Evidence).order_by(Evidence.id))).all())


async def events(db: AsyncEngine, kind: str) -> list[IncidentEvent]:
    async with sessionmaker_for(db)() as session:
        return list(
            (await session.scalars(select(IncidentEvent).where(IncidentEvent.kind == kind))).all()
        )


async def test_partial_failure_keeps_other_evidence_and_says_what_is_missing(
    db: AsyncEngine,
) -> None:
    incident_id = await create_incident(db)  # checkout
    await add_deploy(db, "checkout", 5, "v2.4.0")
    await add_deploy(db, "checkout", 60, "v2.3.0")  # outside the 15-minute lookback
    await add_deploy(db, "cart", 2, "v9")  # another service
    sm = sessionmaker_for(db)
    enricher = Enricher(
        sm,
        [AlertSource(sm), DeploySource(sm), BrokenSource(), HangingSource()],
        source_timeout=0.5,
    )

    results = await enricher.enrich(incident_id)

    assert results is not None
    by_source = {r.source: r for r in results}
    assert by_source["logs"].error == "ConnectionError: connection refused"
    assert by_source["traces"].error == "timeout after 0.5 s"
    rows = await evidence_rows(db)
    assert [(r.id, r.kind) for r in rows] == [("ev_alert_1", "alert"), ("ev_deploy_1", "deploy")]
    assert rows[1].summary == "checkout deployed v2.4.0, 5 min before the incident started"
    missing = await events(db, "evidence_unavailable")
    assert sorted(e.payload["reason"] for e in missing) == [
        "logs unavailable: ConnectionError: connection refused",
        "traces unavailable: timeout after 0.5 s",
    ]
    collected = await events(db, "evidence_collected")
    assert sorted(e.payload["source"] for e in collected) == ["alerts", "deploys"]


async def test_collecting_again_keeps_ids_and_adds_only_new_items(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    await add_deploy(db, "checkout", 5, "v2.4.0")
    sm = sessionmaker_for(db)
    enricher = Enricher(sm, [AlertSource(sm), DeploySource(sm)])

    await enricher.enrich(incident_id)
    await add_deploy(db, "checkout", 1, "v2.4.1")  # a hotfix arrives
    await enricher.enrich(incident_id)

    rows = await evidence_rows(db)
    assert [r.id for r in rows] == ["ev_alert_1", "ev_deploy_1", "ev_deploy_2"]
    assert "v2.4.0" in rows[1].summary  # the first deploy kept its ID
    assert "v2.4.1" in rows[2].summary


async def test_concurrent_runs_never_hand_out_the_same_id(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    now = datetime.now(UTC)
    items = [
        EvidenceItem(EvidenceKind.METRIC, f"metric {n}", now, dedupe_key=f"m{n}") for n in range(5)
    ]
    sm = sessionmaker_for(db)
    enricher = Enricher(sm, [FixedSource(items)])

    await asyncio.gather(*(enricher.enrich(incident_id) for _ in range(4)))

    rows = await evidence_rows(db)
    assert sorted(r.id for r in rows) == [f"ev_metric_{n}" for n in range(1, 6)]


async def test_a_failing_source_is_skipped_quickly_after_repeated_failures(
    db: AsyncEngine,
) -> None:
    incident_id = await create_incident(db)
    sm = sessionmaker_for(db)
    enricher = Enricher(sm, [BrokenSource()])

    for _ in range(3):
        await enricher.enrich(incident_id)
    results = await enricher.enrich(incident_id)

    assert results is not None
    assert results[0].error is not None
    assert results[0].error.startswith("skipped: failed 3 times in a row")


async def test_debounce_waits_for_related_alerts_then_schedules_once(
    client: TestClient, db: AsyncEngine
) -> None:
    payment = load_alertmanager("captured_payment_failure_firing")
    incident_id = client.post("/v1/webhooks/alertmanager", json=payment, headers=AUTH).json()[
        "new_incident_ids"
    ][0]
    async with sessionmaker_for(db)() as session:
        incident = await session.get(Incident, uuid.UUID(incident_id))
        assert incident is not None
        assert incident.enrich_after is not None
        first_deadline = incident.enrich_after
        assert first_deadline > datetime.now(UTC)  # not yet: more alerts may follow

        assert await schedule_due_enrichments(session, "s") == []  # not due
        due = await schedule_due_enrichments(session, "s", now=first_deadline)
        await session.commit()
    assert due == [uuid.UUID(incident_id)]

    async with sessionmaker_for(db)() as session:
        jobs = (await session.scalars(select(OutboxMessage))).all()
        assert [(j.type, j.payload) for j in jobs if j.stream == "s"] == [
            ("enrich", {"incident_id": incident_id})
        ]
        incident = await session.get(Incident, uuid.UUID(incident_id))
        assert incident is not None
        assert incident.enrich_after is None
        # Scheduled once: a second sweep finds nothing.
        assert await schedule_due_enrichments(session, "s", now=first_deadline) == []


async def test_a_new_alert_pushes_collection_back_but_not_past_the_max_wait(
    client: TestClient, db: AsyncEngine
) -> None:
    payment = load_alertmanager("captured_payment_failure_firing")
    incident_id = uuid.UUID(
        client.post("/v1/webhooks/alertmanager", json=payment, headers=AUTH).json()[
            "new_incident_ids"
        ][0]
    )
    # Pretend the incident opened 170 s ago: the max wait (180 s) is close.
    async with db.begin() as conn:
        await conn.execute(
            update(Incident).values(created_at=datetime.now(UTC) - timedelta(seconds=170))
        )

    checkout = load_alertmanager("captured_payment_failure_firing")
    checkout["alerts"][0]["labels"].update(service="checkout", service_name="checkout")
    checkout["alerts"][0]["fingerprint"] = "fp-checkout"
    client.post("/v1/webhooks/alertmanager", json=checkout, headers=AUTH)

    async with sessionmaker_for(db)() as session:
        incident = await session.get(Incident, incident_id)
        assert incident is not None
        assert incident.enrich_after is not None
        cap = incident.created_at + timedelta(seconds=180)
        assert incident.enrich_after <= cap + timedelta(seconds=1)
        assert incident.enrich_after < datetime.now(UTC) + timedelta(seconds=30)


async def test_report_a_deploy(client: TestClient, db: AsyncEngine) -> None:
    payment = load_alertmanager("captured_payment_failure_firing")
    client.post("/v1/webhooks/alertmanager", json=payment, headers=AUTH)
    async with db.begin() as conn:
        await conn.execute(update(Incident).values(enrich_after=None))  # already collected

    body = {"service": "payment", "version": "v1.4.2", "commit_sha": "abc123"}
    assert client.post("/v1/deploys", json=body).status_code == 401
    response = client.post("/v1/deploys", json=body, headers=AUTH)

    assert response.status_code == 201
    out = response.json()
    assert out["service"] == "payment"
    assert out["incidents_to_recheck"] == 1  # a deploy during the incident: collect again
    async with sessionmaker_for(db)() as session:
        incident = (await session.scalars(select(Incident))).one()
        assert incident.enrich_after is not None
    assert client.post("/v1/deploys", json={"service": ""}, headers=AUTH).status_code == 422


async def test_incident_page_and_api_show_evidence(client: TestClient, db: AsyncEngine) -> None:
    payment = load_alertmanager("captured_payment_failure_firing")
    incident_id = client.post("/v1/webhooks/alertmanager", json=payment, headers=AUTH).json()[
        "new_incident_ids"
    ][0]
    await add_deploy(db, "payment", 3, "v1.4.2")
    sm = sessionmaker_for(db)
    await Enricher(sm, [AlertSource(sm), DeploySource(sm), BrokenSource()]).enrich(
        uuid.UUID(incident_id)
    )

    detail = client.get(f"/v1/incidents/{incident_id}").json()
    assert [e["id"] for e in detail["evidence"]] == ["ev_deploy_1", "ev_alert_1"]
    assert detail["enriched_at"] is not None
    assert any(t["kind"] == "evidence_unavailable" for t in detail["timeline"])

    page = client.get(f"/incidents/{incident_id}").text
    assert "Evidence (2)" in page
    assert 'id="ev_deploy_1"' in page
    assert "payment deployed v1.4.2, 3 min before the incident started" in page
    assert "No evidence from logs" in page
