from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from iip.ingest.alertmanager import MAX_ALERTS_PER_PAYLOAD, AlertmanagerPayload
from tests.fixtures.loader import load_alertmanager


def test_parses_firing_alert() -> None:
    payload = AlertmanagerPayload.model_validate(load_alertmanager("high_error_rate_firing"))

    alert = payload.alerts[0]
    assert alert.status == "firing"
    assert alert.fingerprint == "a1b2c3d4e5f60718"
    assert alert.alertname == "HighErrorRate"
    assert alert.service == "checkout"
    assert alert.severity == "critical"
    assert alert.starts_at == datetime(2026, 11, 1, 4, 10, tzinfo=UTC)
    assert alert.title == "checkout error rate above 5% for 2 minutes"


def test_zero_ends_at_means_not_ended() -> None:
    payload = AlertmanagerPayload.model_validate(load_alertmanager("high_error_rate_firing"))

    assert payload.alerts[0].ends_at is None


def test_resolved_alert_has_end_time() -> None:
    payload = AlertmanagerPayload.model_validate(load_alertmanager("high_error_rate_resolved"))

    assert payload.alerts[0].status == "resolved"
    assert payload.alerts[0].ends_at == datetime(2026, 11, 1, 4, 25, tzinfo=UTC)


def test_service_falls_back_to_job_label_and_title_to_alertname() -> None:
    payload = AlertmanagerPayload.model_validate(load_alertmanager("two_alerts_firing"))

    service_down = payload.alerts[1]
    assert service_down.service == "recommendation"  # from the "job" label
    assert service_down.title == "ServiceDown on recommendation"


def test_unknown_fields_are_kept_not_rejected() -> None:
    data = load_alertmanager("high_error_rate_firing")
    data["someFutureField"] = {"x": 1}
    data["alerts"][0]["anotherNewField"] = "y"

    payload = AlertmanagerPayload.model_validate(data)

    assert payload.alerts[0].raw()["anotherNewField"] == "y"


def test_naive_timestamps_are_treated_as_utc() -> None:
    data = load_alertmanager("high_error_rate_firing")
    data["alerts"][0]["startsAt"] = "2026-11-01T04:10:00"

    payload = AlertmanagerPayload.model_validate(data)

    assert payload.alerts[0].starts_at.tzinfo is not None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["alerts"][0].pop("fingerprint"),
        lambda d: d["alerts"][0].update(status="exploded"),
        lambda d: d["alerts"][0].update(startsAt="yesterday"),
        lambda d: d["alerts"][0].update(fingerprint=""),
        lambda d: d.pop("alerts"),
    ],
)
def test_rejects_payloads_missing_what_we_depend_on(mutate) -> None:  # type: ignore[no-untyped-def]
    data = load_alertmanager("high_error_rate_firing")
    mutate(data)

    with pytest.raises(ValidationError):
        AlertmanagerPayload.model_validate(data)


def test_rejects_oversized_payload() -> None:
    data = load_alertmanager("high_error_rate_firing")
    data["alerts"] = data["alerts"] * (MAX_ALERTS_PER_PAYLOAD + 1)

    with pytest.raises(ValidationError):
        AlertmanagerPayload.model_validate(data)
