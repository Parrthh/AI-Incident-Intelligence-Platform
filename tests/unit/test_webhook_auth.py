from fastapi.testclient import TestClient

from iip.api.app import create_app
from iip.config import Environment, Settings
from tests.fixtures.loader import load_alertmanager

URL = "/v1/webhooks/alertmanager"


def make_client(token: str | None) -> TestClient:
    settings = Settings(env=Environment.TEST, webhook_token=token, _env_file=None)  # pyright: ignore[reportCallIssue]
    return TestClient(create_app(settings))


def test_webhook_disabled_without_configured_token() -> None:
    with make_client(None) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"))

    assert response.status_code == 503


def test_missing_token_is_rejected() -> None:
    with make_client("s3cret") as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_wrong_token_is_rejected() -> None:
    with make_client("s3cret") as client:
        response = client.post(
            URL,
            json=load_alertmanager("high_error_rate_firing"),
            headers={"Authorization": "Bearer nope"},
        )

    assert response.status_code == 401


def test_wrong_scheme_is_rejected() -> None:
    with make_client("s3cret") as client:
        response = client.post(
            URL,
            json=load_alertmanager("high_error_rate_firing"),
            headers={"Authorization": "Basic s3cret"},
        )

    assert response.status_code == 401


def test_any_configured_token_is_accepted_for_rotation() -> None:
    # No database configured in this test, so a valid token gets past auth and
    # then fails with 503 at the session dependency. That proves auth passed.
    with make_client("old-token, new-token") as client:
        for token in ("old-token", "new-token"):
            response = client.post(
                URL,
                json=load_alertmanager("high_error_rate_firing"),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 503
            assert response.json()["detail"] == "database not configured"
