import json
import logging

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iip.observability.logging import configure_logging


def test_generates_request_id_when_missing(client: TestClient) -> None:
    response = client.get("/version")

    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32  # uuid4 hex
    assert all(c in "0123456789abcdef" for c in request_id)


def test_reuses_valid_incoming_request_id(client: TestClient) -> None:
    response = client.get("/version", headers={"X-Request-ID": "lb-trace:abc-123"})

    assert response.headers["x-request-id"] == "lb-trace:abc-123"


@pytest.mark.parametrize(
    "bad_id",
    [
        "x" * 129,  # too long
        "has spaces",
        "semi;colon",
        "",
    ],
)
def test_replaces_invalid_incoming_request_id(client: TestClient, bad_id: str) -> None:
    response = client.get("/version", headers={"X-Request-ID": bad_id})

    assert response.headers["x-request-id"] != bad_id
    assert len(response.headers["x-request-id"]) == 32


def test_each_request_gets_a_different_id(client: TestClient) -> None:
    first = client.get("/version").headers["x-request-id"]
    second = client.get("/version").headers["x-request-id"]

    assert first != second


def test_request_id_is_on_the_access_log_line(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(level="INFO", json=True)
    try:
        with TestClient(app) as client:
            response = client.get("/version", headers={"X-Request-ID": "req-42"})
        assert response.status_code == 200

        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        access = [line for line in lines if line.get("event") == "request"]
        assert len(access) == 1
        assert access[0]["request_id"] == "req-42"
        assert access[0]["path"] == "/version"
        assert access[0]["status"] == 200
        assert access[0]["method"] == "GET"
        assert isinstance(access[0]["duration_ms"], float | int)
        assert access[0]["level"] == "info"
        assert "timestamp" in access[0]
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()


def test_health_checks_are_not_access_logged(
    app: FastAPI, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(level="INFO", json=True)
    try:
        with TestClient(app) as client:
            client.get("/healthz")
            client.get("/readyz")

        out = capsys.readouterr().out
        assert '"path": "/healthz"' not in out
        assert '"path": "/readyz"' not in out
    finally:
        structlog.reset_defaults()
        logging.getLogger().handlers.clear()
