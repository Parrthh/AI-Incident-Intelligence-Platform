import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iip import __version__
from iip.api import health
from iip.api.health import ReadinessChecks


def test_healthz_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_is_ready_when_all_checks_pass(app: FastAPI, client: TestClient) -> None:
    async def ok() -> None:
        return None

    readiness: ReadinessChecks = app.state.readiness
    readiness.register("db", ok)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"db": {"ok": True, "error": None}}}


def test_readyz_is_ready_with_no_checks(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_returns_503_when_a_check_fails(app: FastAPI, client: TestClient) -> None:
    async def ok() -> None:
        return None

    async def broken() -> None:
        raise ConnectionRefusedError("postgres://user:secret@db:5432 refused")

    readiness: ReadinessChecks = app.state.readiness
    readiness.register("redis", ok)
    readiness.register("db", broken)

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["redis"]["ok"] is True
    assert body["checks"]["db"] == {"ok": False, "error": "ConnectionRefusedError"}
    # The exception message may contain credentials; it must not reach the response.
    assert "secret" not in response.text


def test_readyz_treats_slow_check_as_not_ready(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)

    async def slow() -> None:
        await asyncio.sleep(1)

    readiness: ReadinessChecks = app.state.readiness
    readiness.register("db", slow)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["db"] == {"ok": False, "error": "timeout"}


def test_version_reports_package_version_and_git_sha(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert response.json() == {"version": __version__, "git_sha": "test-sha"}
