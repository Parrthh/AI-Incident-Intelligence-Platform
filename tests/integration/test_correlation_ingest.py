"""Alerts on related services become one incident, with the reason on its timeline.

Uses the real payload Alertmanager sent in a chaos run (payment failing), plus
the checkout alert that followed it in the same run.
"""

import asyncio
import copy
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
from iip.db.models import Incident
from tests.fixtures.loader import load_alertmanager
from tests.integration.helpers import resolve_quiet_incidents_now

pytestmark = pytest.mark.integration

URL = "/v1/webhooks/alertmanager"
AUTH = {"Authorization": "Bearer t"}


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


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


def payment(status: str = "firing") -> dict[str, Any]:
    return load_alertmanager(f"captured_payment_failure_{status}")


def on_service(
    service: str,
    *,
    seconds_later: int = 60,
    status: str = "firing",
    severity: str = "critical",
) -> dict[str, Any]:
    """The payment alert, moved to another service and a bit later."""
    payload = copy.deepcopy(payment(status))
    alert = payload["alerts"][0]
    alert["labels"].update(service=service, service_name=service, severity=severity)
    alert["annotations"]["summary"] = f"{service} error rate above 20%"
    alert["fingerprint"] = f"fp-{service}"
    alert["startsAt"] = f"2026-11-01T05:{20 + seconds_later // 60:02d}:51.51Z"
    return payload


async def incident_count(db: AsyncEngine) -> int:
    async with db.connect() as conn:
        return await conn.scalar(select(func.count()).select_from(Incident)) or 0


async def test_failure_spreading_to_a_caller_is_one_incident(
    client: TestClient, db: AsyncEngine
) -> None:
    opened = client.post(URL, json=payment(), headers=AUTH).json()
    joined = client.post(URL, json=on_service("checkout"), headers=AUTH).json()

    [incident_id] = opened["new_incident_ids"]
    assert joined["new_incident_ids"] == []
    assert joined["attached_incident_ids"] == [incident_id]
    assert await incident_count(db) == 1

    detail = client.get(f"/v1/incidents/{incident_id}").json()
    assert detail["title"] == "payment error rate above 20%"
    assert detail["services"] == ["payment", "checkout"]
    assert len(detail["alerts"]) == 2
    kinds = [e["kind"] for e in detail["timeline"]]
    assert kinds == ["incident_opened", "alert_attached"]
    attached = detail["timeline"][1]
    assert attached["rule"] == "connected_service"
    assert attached["service"] == "checkout"
    assert "connected to payment in the service graph" in attached["reason"]

    page = client.get(f"/incidents/{incident_id}").text
    assert "Timeline" in page
    assert "added to this incident" in page
    assert "connected to payment in the service graph" in page


async def test_unrelated_service_opens_its_own_incident(
    client: TestClient, db: AsyncEngine
) -> None:
    client.post(URL, json=payment(), headers=AUTH)
    separate = client.post(URL, json=on_service("ad"), headers=AUTH).json()

    assert len(separate["new_incident_ids"]) == 1
    assert await incident_count(db) == 2


async def test_alerts_arriving_at_the_same_moment_still_group(
    app: FastAPI, db: AsyncEngine
) -> None:
    # Alertmanager sends one request per alert group, in parallel. Without the
    # correlation lock both requests would see "no incident yet" and open two.
    # Duplicates are mixed in too, as Alertmanager retries do.
    payloads = [payment(), on_service("checkout")] * 4
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as ac,
    ):
        responses = await asyncio.gather(*(ac.post(URL, json=p, headers=AUTH) for p in payloads))

    assert all(r.status_code == 202 for r in responses)
    assert sum(len(r.json()["new_incident_ids"]) for r in responses) == 1
    assert await incident_count(db) == 1


async def test_severity_rises_with_a_more_severe_alert(client: TestClient, db: AsyncEngine) -> None:
    first = on_service("checkout", seconds_later=0, severity="warning")
    incident_id = client.post(URL, json=first, headers=AUTH).json()["new_incident_ids"][0]
    client.post(URL, json=payment(), headers=AUTH)  # critical, on a connected service

    detail = client.get(f"/v1/incidents/{incident_id}").json()
    assert detail["severity"] == "critical"
    assert sorted(detail["services"]) == ["checkout", "payment"]


async def test_incident_resolves_only_when_all_alerts_have(
    client: TestClient, db: AsyncEngine
) -> None:
    incident_id = client.post(URL, json=payment(), headers=AUTH).json()["new_incident_ids"][0]
    client.post(URL, json=on_service("checkout"), headers=AUTH)

    client.post(URL, json=payment("resolved"), headers=AUTH)
    assert resolve_quiet_incidents_now(client) == []  # checkout is still firing

    client.post(URL, json=on_service("checkout", status="resolved"), headers=AUTH)
    [resolved] = resolve_quiet_incidents_now(client)

    assert str(resolved) == incident_id
    detail = client.get(f"/v1/incidents/{incident_id}").json()
    assert detail["status"] == "resolved"
    ends = [a["ends_at"] for a in detail["alerts"]]
    assert detail["resolved_at"] == max(ends)  # when the problem stopped
    kinds = [e["kind"] for e in detail["timeline"]]
    assert kinds.count("alert_resolved") == 2
    assert kinds[-1] == "incident_resolved"
    assert detail["timeline"][-1]["reason"].startswith("All 2 of its alerts resolved")
    assert resolve_quiet_incidents_now(client) == []  # resolving is done once


async def test_repeated_resolved_deliveries_record_one_event(client: TestClient) -> None:
    incident_id = client.post(URL, json=payment(), headers=AUTH).json()["new_incident_ids"][0]
    for _ in range(3):
        client.post(URL, json=payment("resolved"), headers=AUTH)

    detail = client.get(f"/v1/incidents/{incident_id}").json()
    assert [e["kind"] for e in detail["timeline"]].count("alert_resolved") == 1
