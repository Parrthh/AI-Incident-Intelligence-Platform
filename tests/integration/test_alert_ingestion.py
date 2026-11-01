import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine
from iip.db.models import Alert, Incident
from tests.fixtures.loader import load_alertmanager

pytestmark = pytest.mark.integration

URL = "/v1/webhooks/alertmanager"
TOKEN = "test-webhook-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app(migrated_database_url: str) -> FastAPI:
    settings = Settings(  # pyright: ignore[reportCallIssue]
        env=Environment.TEST,
        database_url=SecretStr(migrated_database_url),
        webhook_token=SecretStr(TOKEN),
        _env_file=None,
    )
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


async def count(db: AsyncEngine, model: type[Alert] | type[Incident]) -> int:
    async with db.connect() as conn:
        return (await conn.scalar(select(func.count()).select_from(model))) or 0


def post(client: TestClient, payload: dict[str, Any]) -> httpx.Response:
    return client.post(URL, json=payload, headers=AUTH)


async def test_firing_alert_is_stored_and_opens_an_incident(
    client: TestClient, db: AsyncEngine
) -> None:
    response = post(client, load_alertmanager("high_error_rate_firing"))

    assert response.status_code == 202
    body = response.json()
    assert body["received"] == 1
    assert body["new_alerts"] == 1
    assert len(body["new_incident_ids"]) == 1

    async with db.connect() as conn:
        alert = (await conn.execute(select(Alert))).one()
        incident = (await conn.execute(select(Incident))).one()
    assert alert.fingerprint == "a1b2c3d4e5f60718"
    assert alert.service == "checkout"
    assert alert.incident_id == incident.id
    assert alert.raw["fingerprint"] == "a1b2c3d4e5f60718"
    assert incident.title == "checkout error rate above 5% for 2 minutes"
    assert incident.status == "open"
    assert incident.services == ["checkout"]
    assert incident.severity == "critical"


async def test_redelivery_is_idempotent(client: TestClient, db: AsyncEngine) -> None:
    payload = load_alertmanager("high_error_rate_firing")

    first = post(client, payload)
    second = post(client, payload)
    third = post(client, payload)

    assert first.json()["new_alerts"] == 1
    assert second.json()["new_alerts"] == 0
    assert second.json()["updated_alerts"] == 1
    assert second.json()["new_incident_ids"] == []
    assert third.json()["new_incident_ids"] == []
    assert await count(db, Alert) == 1
    assert await count(db, Incident) == 1


async def test_resolved_alert_updates_row_and_resolves_incident(
    client: TestClient, db: AsyncEngine
) -> None:
    opened = post(client, load_alertmanager("high_error_rate_firing")).json()
    resolved = post(client, load_alertmanager("high_error_rate_resolved")).json()

    assert resolved["new_alerts"] == 0
    assert resolved["resolved_incident_ids"] == opened["new_incident_ids"]
    async with db.connect() as conn:
        alert = (await conn.execute(select(Alert))).one()
        incident = (await conn.execute(select(Incident))).one()
    assert alert.status == "resolved"
    assert alert.ends_at is not None
    assert incident.status == "resolved"
    assert incident.resolved_at == alert.ends_at
    assert await count(db, Alert) == 1


async def test_late_firing_retry_does_not_reopen_resolved_alert(
    client: TestClient, db: AsyncEngine
) -> None:
    post(client, load_alertmanager("high_error_rate_firing"))
    post(client, load_alertmanager("high_error_rate_resolved"))
    # A delayed retry of the original "firing" delivery arrives last.
    late = post(client, load_alertmanager("high_error_rate_firing")).json()

    assert late["new_incident_ids"] == []
    async with db.connect() as conn:
        alert = (await conn.execute(select(Alert))).one()
        incident = (await conn.execute(select(Incident))).one()
    assert alert.status == "resolved"
    assert alert.ends_at is not None
    assert incident.status == "resolved"


async def test_refiring_with_new_start_time_is_a_new_occurrence(
    client: TestClient, db: AsyncEngine
) -> None:
    post(client, load_alertmanager("high_error_rate_firing"))
    post(client, load_alertmanager("high_error_rate_resolved"))
    again = load_alertmanager("high_error_rate_firing")
    again["alerts"][0]["startsAt"] = "2026-11-01T06:00:00Z"

    response = post(client, again).json()

    assert response["new_alerts"] == 1
    assert len(response["new_incident_ids"]) == 1
    assert await count(db, Alert) == 2
    assert await count(db, Incident) == 2


async def test_grouped_delivery_stores_each_alert(client: TestClient, db: AsyncEngine) -> None:
    response = post(client, load_alertmanager("two_alerts_firing")).json()

    assert response["received"] == 2
    assert len(response["new_incident_ids"]) == 2
    assert await count(db, Alert) == 2


async def test_concurrent_duplicate_deliveries_create_one_incident(
    app: FastAPI, db: AsyncEngine
) -> None:
    # Alertmanager clusters (and retries) can deliver the same alert in parallel.
    # The unique constraint plus ON CONFLICT must hold under real concurrency.
    payload = load_alertmanager("high_error_rate_firing")
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as ac,
    ):
        responses = await asyncio.gather(
            *(ac.post(URL, json=payload, headers=AUTH) for _ in range(10))
        )

    assert all(r.status_code == 202 for r in responses)
    assert sum(len(r.json()["new_incident_ids"]) for r in responses) == 1
    assert await count(db, Alert) == 1
    assert await count(db, Incident) == 1


def test_invalid_payload_is_rejected_with_422(client: TestClient) -> None:
    payload = load_alertmanager("high_error_rate_firing")
    del payload["alerts"][0]["fingerprint"]

    response = post(client, payload)

    assert response.status_code == 422
