import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from iip.api.app import create_app
from iip.config import Environment, Settings
from tests.fixtures.loader import load_alertmanager
from tests.integration.helpers import resolve_quiet_incidents_now, run_summary_jobs

pytestmark = pytest.mark.integration

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr(migrated_database_url),
        webhook_token=SecretStr(TOKEN),
        llm_provider="fake",
        git_sha="abc123def4567890",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def send(client: TestClient, payload: dict[str, Any]) -> list[str]:
    response = client.post("/v1/webhooks/alertmanager", json=payload, headers=AUTH)
    assert response.status_code == 202
    run_summary_jobs(client)
    return response.json()["new_incident_ids"]


def alert_at(minute: int, fingerprint: str, title: str = "x") -> dict[str, Any]:
    payload = load_alertmanager("high_error_rate_firing")
    alert = payload["alerts"][0]
    alert["fingerprint"] = fingerprint
    alert["labels"]["service"] = f"svc-{fingerprint}"  # unrelated services: one incident each
    alert["startsAt"] = f"2026-11-01T04:{minute:02d}:00Z"
    alert["annotations"]["summary"] = title
    return payload


def test_api_lists_newest_first_with_summaries(client: TestClient) -> None:
    send(client, alert_at(1, "f1", "first"))
    send(client, alert_at(2, "f2", "second"))

    body = client.get("/v1/incidents").json()

    assert [i["title"] for i in body["items"]] == ["second", "first"]
    assert body["items"][0]["summary"]["status"] == "done"
    assert body["items"][0]["summary"]["text"] == "Fake summary of the incident."
    assert body["next_cursor"] is None


def test_api_paginates_with_cursor_without_gaps_or_repeats(client: TestClient) -> None:
    for minute in range(7):
        send(client, alert_at(minute, f"p{minute}", f"t{minute}"))

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/v1/incidents", params=params).json()
        seen += [i["title"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert pages == 3
    assert seen == [f"t{m}" for m in range(6, -1, -1)]


def test_api_filters_by_status_and_service(client: TestClient) -> None:
    send(client, load_alertmanager("high_error_rate_firing"))
    send(client, load_alertmanager("high_error_rate_resolved"))
    resolve_quiet_incidents_now(client)
    send(client, load_alertmanager("two_alerts_firing"))

    open_titles = {i["title"] for i in client.get("/v1/incidents?status=open").json()["items"]}
    resolved = client.get("/v1/incidents?status=resolved").json()["items"]
    cart = client.get("/v1/incidents?service=cart").json()["items"]

    assert len(open_titles) == 2
    assert [i["services"] for i in resolved] == [["checkout"]]
    assert [i["services"] for i in cart] == [["cart"]]


def test_api_detail_includes_alerts(client: TestClient) -> None:
    incident_id = send(client, load_alertmanager("high_error_rate_firing"))[0]

    body = client.get(f"/v1/incidents/{incident_id}").json()

    assert body["id"] == incident_id
    assert body["alerts"][0]["alertname"] == "HighErrorRate"
    assert body["alerts"][0]["labels"]["service"] == "checkout"
    assert body["summary"]["prompt_version"] == "summary_v1"


def test_api_errors(client: TestClient) -> None:
    assert client.get("/v1/incidents/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/v1/incidents/not-a-uuid").status_code == 422
    assert client.get("/v1/incidents?cursor=garbage").status_code == 400
    assert client.get("/v1/incidents?status=exploded").status_code == 422
    assert client.get("/v1/incidents?limit=1000").status_code == 422


def test_ui_list_and_detail_render(client: TestClient) -> None:
    incident_id = send(client, load_alertmanager("high_error_rate_firing"))[0]

    listing = client.get("/incidents")
    detail = client.get(f"/incidents/{incident_id}")

    assert listing.status_code == 200
    assert "checkout error rate above 5% for 2 minutes" in listing.text
    assert f'href="/incidents/{incident_id}"' in listing.text
    assert detail.status_code == 200
    assert "Fake summary of the incident." in detail.text
    assert "HighErrorRate" in detail.text
    assert "service=checkout" in detail.text
    assert "abc123def456" in detail.text  # build info in footer
    # Plain-language context for visitors who aren't engineers.
    assert "Where these come from" in listing.text
    assert "AI summary" in listing.text
    assert "from the alerts as they first fired" in detail.text


def test_ui_empty_state(client: TestClient) -> None:
    response = client.get("/incidents")

    assert response.status_code == 200
    assert "No incidents yet" in response.text


def test_ui_escapes_alert_text(client: TestClient) -> None:
    # Anyone who can send alerts controls this text. It must never run as HTML.
    evil = "<script>alert('xss')</script>"
    payload = alert_at(5, "xss", evil)
    payload["alerts"][0]["labels"]["service"] = '"><img src=x onerror=alert(1)>'
    incident_id = send(client, payload)[0]

    for url in ("/incidents", f"/incidents/{incident_id}"):
        html = client.get(url).text
        assert "<script>alert('xss')</script>" not in html
        assert "<img src=x" not in html
        assert "&lt;script&gt;" in html


def test_ui_unknown_incident_is_404(client: TestClient) -> None:
    assert client.get("/incidents/00000000-0000-0000-0000-000000000000").status_code == 404


def test_json_body_is_parsed_not_rendered(client: TestClient) -> None:
    # Sanity: the API returns JSON, not HTML, for the same data.
    send(client, load_alertmanager("high_error_rate_firing"))
    response = client.get("/v1/incidents")
    assert response.headers["content-type"].startswith("application/json")
    json.loads(response.text)
