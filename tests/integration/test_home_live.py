from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.ingest.alertmanager import AlertmanagerPayload
from tests.fixtures.loader import load_alertmanager

pytestmark = pytest.mark.integration

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    settings = Settings(
        env=Environment.TEST,
        database_url=SecretStr(migrated_database_url),
        webhook_token=SecretStr("t"),
        llm_provider="fake",
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_home_shows_latest_live_incident_and_real_counts(client: TestClient) -> None:
    empty = client.get("/").text
    assert "Example from a chaos test run" in empty

    response = client.post(
        "/v1/webhooks/alertmanager",
        json=load_alertmanager("captured_payment_failure_firing"),
        headers=AUTH,
    )
    incident_id = response.json()["new_incident_ids"][0]

    html = client.get("/").text
    assert "Latest incident on this deployment" in html
    assert "Fake summary of the incident." in html
    assert f'href="/incidents/{incident_id}"' in html
    assert "<dd>1</dd>" in html  # one incident, one alert, one summary


def test_captured_payloads_round_trip(client: TestClient) -> None:
    # Real payloads from Alertmanager 0.28 in the chaos run: firing, then resolved.
    firing = AlertmanagerPayload.model_validate(
        load_alertmanager("captured_payment_failure_firing")
    )
    assert firing.alerts[0].service == "payment"

    opened = client.post(
        "/v1/webhooks/alertmanager",
        json=load_alertmanager("captured_payment_failure_firing"),
        headers=AUTH,
    ).json()
    resolved = client.post(
        "/v1/webhooks/alertmanager",
        json=load_alertmanager("captured_payment_failure_resolved"),
        headers=AUTH,
    ).json()

    assert resolved["resolved_incident_ids"] == opened["new_incident_ids"]
    detail = client.get(f"/v1/incidents/{opened['new_incident_ids'][0]}").json()
    assert detail["status"] == "resolved"
    assert detail["title"] == "payment error rate above 20%"
