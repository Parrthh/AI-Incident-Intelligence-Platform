import uuid

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from pydantic import SecretStr

from iip.api.app import create_app
from iip.config import Environment, Settings


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_endpoint_serves_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for name in (
        "iip_http_request_duration_seconds",
        "iip_jobs_total",
        "iip_queue_pending",
        "iip_outbox_lag_seconds",
    ):
        assert name in response.text


def test_requests_are_labelled_by_route_template_not_raw_path(client: TestClient) -> None:
    labels = {"method": "GET", "route": "/incidents/{incident_id}", "status": "503"}
    before = sample("iip_http_request_duration_seconds_count", **labels)

    for _ in range(3):  # three different IDs, one label set
        client.get(f"/incidents/{uuid.uuid4()}")

    assert sample("iip_http_request_duration_seconds_count", **labels) == before + 3


def test_unknown_methods_and_paths_do_not_create_new_labels(client: TestClient) -> None:
    before = sample(
        "iip_http_request_duration_seconds_count", method="GET", route="unmatched", status="404"
    )
    client.get("/no/such/page/1")
    client.get("/no/such/page/2")
    after = sample(
        "iip_http_request_duration_seconds_count", method="GET", route="unmatched", status="404"
    )
    assert after == before + 2


def test_metrics_token_is_enforced_when_set() -> None:
    settings = Settings(
        env=Environment.TEST,
        metrics_token=SecretStr("scrape-me"),
        _env_file=None,  # pyright: ignore[reportCallIssue]
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/metrics").status_code == 401
        bad = {"Authorization": "Bearer wrong"}
        assert client.get("/metrics", headers=bad).status_code == 401
        good = {"Authorization": "Bearer scrape-me"}
        assert client.get("/metrics", headers=good).status_code == 200
