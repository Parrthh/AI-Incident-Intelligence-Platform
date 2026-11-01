import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine
from iip.db.models import Analysis
from iip.llm.base import LLMError
from iip.llm.fake import FakeProvider
from iip.llm.summarize import summarize_incident
from tests.fixtures.loader import load_alertmanager

pytestmark = pytest.mark.integration

URL = "/v1/webhooks/alertmanager"
TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(database_url: str) -> FastAPI:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr(database_url),
        webhook_token=SecretStr(TOKEN),
        llm_provider="fake",
        llm_max_retries=0,
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    return create_app(settings)


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


async def analyses(db: AsyncEngine) -> list[Analysis]:
    async with async_sessionmaker(db)() as session:
        return list((await session.scalars(select(Analysis))).all())


async def test_new_incident_gets_a_summary(migrated_database_url: str, db: AsyncEngine) -> None:
    app = make_app(migrated_database_url)
    with TestClient(app) as client:
        # TestClient runs background tasks before returning the response.
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)
        llm: FakeProvider = app.state.llm

    incident_id = response.json()["new_incident_ids"][0]
    rows = await analyses(db)
    assert len(rows) == 1
    row = rows[0]
    assert str(row.incident_id) == incident_id
    assert row.status == "done"
    assert row.kind == "summary"
    assert row.summary == "Fake summary of the incident."
    assert row.provider == "fake"
    assert row.prompt_version == "summary_v0"
    assert row.attempts == 1
    assert row.completed_at is not None
    assert row.input_tokens is not None
    # The prompt sent to the model contained the alert data.
    assert "HighErrorRate" in llm.calls[0]


async def test_redelivery_does_not_summarize_again(
    migrated_database_url: str, db: AsyncEngine
) -> None:
    app = make_app(migrated_database_url)
    payload = load_alertmanager("high_error_rate_firing")
    with TestClient(app) as client:
        client.post(URL, json=payload, headers=AUTH)
        client.post(URL, json=payload, headers=AUTH)
        llm: FakeProvider = app.state.llm

    assert len(llm.calls) == 1
    assert len(await analyses(db)) == 1


async def test_llm_failure_is_recorded_not_raised(
    migrated_database_url: str, db: AsyncEngine
) -> None:
    app = make_app(migrated_database_url)
    with TestClient(app) as client:
        app.state.llm = FakeProvider(failures=[LLMError("bad api key", retryable=False)])
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)

    # The webhook itself still succeeded: summaries never block ingestion.
    assert response.status_code == 202
    row = (await analyses(db))[0]
    assert row.status == "failed"
    assert row.error == "bad api key"
    assert row.summary is None
    assert row.attempts == 1


async def test_retryable_failures_are_retried(migrated_database_url: str, db: AsyncEngine) -> None:
    app = make_app(migrated_database_url)
    with TestClient(app) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)
    incident_id = response.json()["new_incident_ids"][0]

    # Fresh summary run for a second prompt attempt, with one transient failure.
    async with db.begin() as conn:
        await conn.execute(delete(Analysis))
    flaky = FakeProvider(failures=[LLMError("rate limited", retryable=True)])
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(db, expire_on_commit=False)
    await summarize_incident(sessionmaker, flaky, uuid.UUID(incident_id), max_retries=1)

    row = (await analyses(db))[0]
    assert row.status == "done"
    assert row.attempts == 2


async def test_no_llm_configured_means_no_analyses(
    migrated_database_url: str, db: AsyncEngine
) -> None:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr(migrated_database_url),
        webhook_token=SecretStr(TOKEN),
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)

    assert response.status_code == 202
    assert await analyses(db) == []
