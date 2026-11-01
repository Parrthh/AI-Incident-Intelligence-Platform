"""Incident summaries: claim rules, idempotency and failure recording.

These call ``summarize_incident`` directly. The queue path that triggers it is
covered in ``test_job_pipeline.py``.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine
from iip.db.models import Analysis, OutboxMessage
from iip.llm.base import LLMError
from iip.llm.fake import FakeProvider
from iip.llm.summarize import summarize_incident
from tests.fixtures.loader import load_alertmanager
from tests.integration.helpers import (
    SlowProvider,
    analyses,
    create_incident,
    sessionmaker_for,
)

pytestmark = pytest.mark.integration

URL = "/v1/webhooks/alertmanager"
TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


def make_settings(database_url: str, **overrides: object) -> Settings:
    return Settings(
        env=Environment.TEST,
        database_url=SecretStr(database_url),
        webhook_token=SecretStr(TOKEN),
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **overrides,  # pyright: ignore[reportArgumentType]
    )


async def outbox_rows(db: AsyncEngine) -> list[OutboxMessage]:
    async with sessionmaker_for(db)() as session:
        return list((await session.scalars(select(OutboxMessage))).all())


async def test_webhook_writes_summary_job_to_outbox(
    migrated_database_url: str, db: AsyncEngine
) -> None:
    settings = make_settings(migrated_database_url, llm_provider="fake")
    with TestClient(create_app(settings)) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)
        client.post(
            URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH
        )  # duplicate

    incident_id = response.json()["new_incident_ids"][0]
    [row] = await outbox_rows(db)  # the duplicate delivery opened no incident, so no job
    assert row.type == "summarize"
    assert row.stream == "iip.jobs"
    assert row.payload == {"incident_id": incident_id}
    assert row.published_at is None  # no Redis configured in this test


async def test_no_llm_configured_means_no_jobs(migrated_database_url: str, db: AsyncEngine) -> None:
    with TestClient(create_app(make_settings(migrated_database_url))) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)

    assert response.status_code == 202
    assert await outbox_rows(db) == []
    assert await analyses(db) == []


async def test_summary_is_written(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = FakeProvider()

    result = await summarize_incident(sessionmaker_for(db), llm, incident_id, max_retries=0)

    assert result.outcome == "done"
    [row] = await analyses(db)
    assert row.id == result.analysis_id
    assert row.incident_id == incident_id
    assert row.status == "done"
    assert row.kind == "summary"
    assert row.summary == "Fake summary of the incident."
    assert row.provider == "fake"
    assert row.prompt_version == "summary_v0"
    assert row.attempts == 1
    assert row.completed_at is not None
    assert row.input_tokens is not None
    assert row.claimed_by is None  # claim released
    assert "HighErrorRate" in llm.calls[0]  # the prompt contained the alert data


async def test_finished_summary_is_reused(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = FakeProvider()
    first = await summarize_incident(sessionmaker_for(db), llm, incident_id)
    second = await summarize_incident(sessionmaker_for(db), llm, incident_id)

    assert second.outcome == "reused"
    assert second.analysis_id == first.analysis_id
    assert len(llm.calls) == 1
    assert len(await analyses(db)) == 1


async def test_permanent_llm_failure_is_recorded(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = FakeProvider(failures=[LLMError("bad api key", retryable=False)])

    result = await summarize_incident(sessionmaker_for(db), llm, incident_id, max_retries=2)

    assert result.outcome == "failed"
    assert result.retryable is False
    [row] = await analyses(db)
    assert row.status == "failed"
    assert row.error == "bad api key"
    assert row.summary is None
    assert row.attempts == 1  # permanent errors aren't retried
    assert row.claimed_by is None


async def test_retryable_failures_are_retried_within_one_run(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = FakeProvider(failures=[LLMError("rate limited", retryable=True)])

    result = await summarize_incident(sessionmaker_for(db), llm, incident_id, max_retries=1)

    assert result.outcome == "done"
    [row] = await analyses(db)
    assert row.attempts == 2


async def test_failed_row_is_retried_by_a_later_run(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = FakeProvider(failures=[LLMError("rate limited", retryable=True)])
    sm = sessionmaker_for(db)

    first = await summarize_incident(sm, llm, incident_id, max_retries=0, claim_token="job-1")
    assert (first.outcome, first.retryable) == ("failed", True)
    second = await summarize_incident(sm, llm, incident_id, max_retries=0, claim_token="job-2")

    assert second.outcome == "done"
    [row] = await analyses(db)  # same row, updated
    assert row.status == "done"
    assert row.error is None
    assert row.attempts == 2  # counted across runs


async def test_row_claimed_by_another_delivery_is_left_alone(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    sm = sessionmaker_for(db)
    await _leave_pending_row(db, incident_id, claimed_by="job-1")
    llm = FakeProvider()

    result = await summarize_incident(sm, llm, incident_id, claim_token="job-2")

    assert result.outcome == "busy"
    assert llm.calls == []


async def test_redelivery_takes_over_its_own_unfinished_row(db: AsyncEngine) -> None:
    # The worker died during the LLM call; the same job comes back.
    incident_id = await create_incident(db)
    await _leave_pending_row(db, incident_id, claimed_by="job-1")

    result = await summarize_incident(
        sessionmaker_for(db), FakeProvider(), incident_id, claim_token="job-1"
    )

    assert result.outcome == "done"
    [row] = await analyses(db)
    assert row.status == "done"


async def test_expired_claim_is_taken_over(db: AsyncEngine) -> None:
    # The holder died and its job was lost (e.g. dead-lettered): after the lease
    # runs out, any delivery may finish the work.
    incident_id = await create_incident(db)
    await _leave_pending_row(db, incident_id, claimed_by="job-1")
    async with db.begin() as conn:
        await conn.execute(
            update(Analysis).values(
                claimed_at=Analysis.claimed_at - literal(timedelta(seconds=700))
            )
        )

    result = await summarize_incident(
        sessionmaker_for(db), FakeProvider(), incident_id, claim_token="job-2", lease_seconds=600
    )

    assert result.outcome == "done"


async def test_concurrent_deliveries_call_the_llm_once(db: AsyncEngine) -> None:
    incident_id = await create_incident(db)
    llm = SlowProvider(delay=0.3)
    sm = sessionmaker_for(db)

    results = await asyncio.gather(
        *(summarize_incident(sm, llm, incident_id, claim_token=f"job-{n}") for n in range(5))
    )

    assert sorted(r.outcome for r in results) == ["busy"] * 4 + ["done"]
    assert len(llm.calls) == 1
    assert len(await analyses(db)) == 1


async def _leave_pending_row(db: AsyncEngine, incident_id: uuid.UUID, *, claimed_by: str) -> None:
    """Simulate a delivery that claimed the row and then died mid-call."""
    async with sessionmaker_for(db)() as session:
        session.add(
            Analysis(
                incident_id=incident_id,
                kind="summary",
                status="pending",
                provider="fake",
                model="fake-model",
                prompt_version="summary_v0",
                attempts=0,
                claimed_by=claimed_by,
                claimed_at=await session.scalar(select(func.now())),
            )
        )
        await session.commit()
