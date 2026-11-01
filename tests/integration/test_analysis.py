"""Root-cause analysis end to end on a real database, with a fake model."""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from iip.analysis.analyzer import analyze_incident
from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine
from iip.db.models import Analysis, Deploy, Incident
from iip.enrichment.runner import Enricher
from iip.enrichment.sources.database import AlertSource, DeploySource
from iip.llm.base import LLMError
from iip.llm.fake import FakeProvider
from iip.queue.jobs import JOB_ANALYZE
from iip.queue.streams import Message
from iip.worker.jobs import build_handlers
from tests.fixtures.loader import load_alertmanager
from tests.integration.helpers import create_incident, sessionmaker_for

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


async def enriched_incident(db: AsyncEngine, *, deploy: bool = True) -> uuid.UUID:
    incident_id = await create_incident(db)  # checkout
    sm = sessionmaker_for(db)
    if deploy:
        async with sm() as session:
            incident = await session.get(Incident, incident_id)
            assert incident is not None
            session.add(
                Deploy(
                    service="checkout",
                    version="v2.4.0",
                    deployed_at=incident.started_at - timedelta(minutes=3),
                )
            )
            await session.commit()
    await Enricher(sm, [AlertSource(sm), DeploySource(sm)]).enrich(incident_id)
    return incident_id


async def analyses(db: AsyncEngine) -> list[Analysis]:
    async with sessionmaker_for(db)() as session:
        return list(
            (
                await session.scalars(
                    select(Analysis)
                    .where(Analysis.kind == "analysis")
                    .order_by(Analysis.created_at)
                )
            ).all()
        )


async def test_analysis_is_stored_with_validated_citations(db: AsyncEngine) -> None:
    incident_id = await enriched_incident(db)
    llm = FakeProvider()

    outcome = await analyze_incident(sessionmaker_for(db), llm, incident_id, max_retries=0)

    assert outcome.outcome == "done"
    [row] = await analyses(db)
    assert row.status == "done"
    assert row.prompt_version == "analyze_v1"
    assert row.input_hash
    assert row.result is not None
    [hypothesis] = row.result["hypotheses"]
    assert hypothesis["root_cause_service"] == "checkout"
    # The deploy ranks first in the prompt, so the fake model cites it first.
    assert hypothesis["evidence_ids"] == ["ev_deploy_1", "ev_alert_1"]
    assert row.result["citations"]["validity"] == 1.0
    assert row.result["context"]["included"] == ["ev_deploy_1", "ev_alert_1"]
    prompt = llm.calls[0]
    assert "[ev_deploy_1] deploy" in prompt
    assert "checkout deployed v2.4.0, 3 min before the incident started" in prompt


async def test_same_evidence_is_not_analyzed_twice_new_evidence_is(db: AsyncEngine) -> None:
    incident_id = await enriched_incident(db, deploy=False)
    sm = sessionmaker_for(db)
    llm = FakeProvider()

    first = await analyze_incident(sm, llm, incident_id)
    again = await analyze_incident(sm, llm, incident_id)
    assert (first.outcome, again.outcome) == ("done", "reused")
    assert len(llm.calls) == 1

    async with sm() as session:  # a deploy is reported, evidence is collected again
        incident = await session.get(Incident, incident_id)
        assert incident is not None
        session.add(Deploy(service="checkout", version="v2.4.1", deployed_at=incident.started_at))
        await session.commit()
    await Enricher(sm, [AlertSource(sm), DeploySource(sm)]).enrich(incident_id)
    third = await analyze_incident(sm, llm, incident_id)

    assert third.outcome == "done"
    assert len(llm.calls) == 2
    rows = await analyses(db)
    assert len(rows) == 2
    assert rows[0].input_hash != rows[1].input_hash


async def test_invented_citations_are_removed(db: AsyncEngine) -> None:
    incident_id = await enriched_incident(db)
    reply = {
        "summary": "Checkout broke after a deploy.",
        "hypotheses": [
            {
                "rank": 1,
                "root_cause_service": "checkout",
                "category": "bad_deploy",
                "explanation": "Deploy 3 minutes before.",
                "confidence": 0.7,
                "evidence_ids": ["ev_deploy_1", "ev_deploy_7"],
            },
            {
                "rank": 2,
                "root_cause_service": "payment",
                "category": "dependency_failure",
                "explanation": "Made up.",
                "confidence": 0.2,
                "evidence_ids": ["ev_trace_3"],
            },
        ],
    }
    llm = FakeProvider(json_reply=json.dumps(reply))

    await analyze_incident(sessionmaker_for(db), llm, incident_id)

    [row] = await analyses(db)
    assert row.result is not None
    assert [h["root_cause_service"] for h in row.result["hypotheses"]] == ["checkout"]
    assert row.result["hypotheses"][0]["evidence_ids"] == ["ev_deploy_1"]
    assert row.result["citations"] == {
        "cited": 3,
        "invalid": 2,
        "invalid_ids": ["ev_deploy_7", "ev_trace_3"],
        "hypotheses_dropped": 1,
        "validity": pytest.approx(1 / 3),
    }


async def test_a_bad_reply_is_asked_for_once_more_then_fails(db: AsyncEngine) -> None:
    incident_id = await enriched_incident(db)

    outcome = await analyze_incident(
        sessionmaker_for(db), FakeProvider(json_reply="Sure! The cause is checkout."), incident_id
    )

    assert outcome.outcome == "failed"
    assert outcome.retryable is False
    [row] = await analyses(db)
    assert row.status == "failed"
    assert row.attempts == 2
    assert row.error is not None
    assert row.error.startswith("reply is not JSON")


async def test_provider_outage_is_retryable(db: AsyncEngine) -> None:
    incident_id = await enriched_incident(db)
    llm = FakeProvider(failures=[LLMError("503", retryable=True)])

    outcome = await analyze_incident(sessionmaker_for(db), llm, incident_id, max_retries=0)

    assert (outcome.outcome, outcome.retryable) == ("failed", True)


async def test_no_evidence_means_no_analysis(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = FakeProvider()

    outcome = await analyze_incident(sessionmaker_for(db), llm, incident_id)

    assert outcome.outcome == "no_evidence"
    assert llm.calls == []


async def test_enrich_job_queues_analysis(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    sm = sessionmaker_for(db)
    published: list[tuple[str, dict[str, object]]] = []

    async def publish_job(job_type: str, payload: dict[str, object]) -> None:
        published.append((job_type, payload))

    handlers = build_handlers(
        sm,
        FakeProvider(),
        llm_max_retries=0,
        enricher=Enricher(sm, [AlertSource(sm)]),
        publish_job=publish_job,
    )
    message = Message(
        id="1-0", type="enrich", payload={"incident_id": str(incident_id)}, created_at=0
    )
    await handlers["enrich"](message)

    assert published == [(JOB_ANALYZE, {"incident_id": str(incident_id)})]
    analyze = Message(
        id="2-0", type=JOB_ANALYZE, payload={"incident_id": str(incident_id)}, created_at=0
    )
    await handlers[JOB_ANALYZE](analyze)
    [row] = await analyses(db)
    assert row.status == "done"


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr(migrated_database_url),
        webhook_token=SecretStr("t"),
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    app: FastAPI = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


async def test_page_and_api_show_the_analysis(client: TestClient, db: AsyncEngine) -> None:
    payload = load_alertmanager("high_error_rate_firing")
    incident_id = client.post(
        "/v1/webhooks/alertmanager", json=payload, headers={"Authorization": "Bearer t"}
    ).json()["new_incident_ids"][0]
    sm = sessionmaker_for(db)
    await Enricher(sm, [AlertSource(sm)]).enrich(uuid.UUID(incident_id))
    assert client.get(f"/v1/incidents/{incident_id}").json()["analysis"] is None
    page = client.get(f"/incidents/{incident_id}").text
    assert "Not analyzed yet" in page

    await analyze_incident(sm, FakeProvider(), uuid.UUID(incident_id))

    detail = client.get(f"/v1/incidents/{incident_id}").json()
    analysis = detail["analysis"]
    assert analysis["status"] == "done"
    assert analysis["result"]["hypotheses"][0]["root_cause_service"] == "checkout"
    page = client.get(f"/incidents/{incident_id}").text
    assert "Likely root cause" in page
    assert 'href="#ev_alert_1"' in page
    assert "Citations checked: 1 of 1 pointed at real evidence" in page
