import uuid
from datetime import UTC, datetime, timedelta

from iip.db.models import Evidence, IncidentEvent
from iip.web.timeline import build_timeline

T0 = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
INCIDENT = uuid.UUID(int=1)


def event(kind: str, minutes: float, **payload: object) -> IncidentEvent:
    return IncidentEvent(
        incident_id=INCIDENT, at=T0 + timedelta(minutes=minutes), kind=kind, payload=payload
    )


def evidence(id_: str, kind: str, minutes: float, summary: str) -> Evidence:
    return Evidence(
        incident_id=INCIDENT,
        id=id_,
        kind=kind,
        source="test",
        summary=summary,
        payload={},
        observed_at=T0 + timedelta(minutes=minutes),
        dedupe_key=id_,
    )


def test_events_and_timed_evidence_are_merged_in_order() -> None:
    rows = build_timeline(
        [
            event("incident_opened", 0, alertname="HighErrorRate", service="payment", reason="r1"),
            event("alert_attached", 1, alertname="HighErrorRate", service="checkout", reason="r2"),
            event("evidence_unavailable", 3, source="logs", reason="logs unavailable: timeout"),
            event("incident_resolved", 9, reason="r3"),
        ],
        [
            evidence("ev_deploy_1", "deploy", -2, "payment deployed v2, 2 min before"),
            evidence("ev_alert_1", "alert", 0, "alerts are already on the timeline"),
        ],
    )

    assert [(r.css, r.title) for r in rows] == [
        ("deploy", "payment deployed v2, 2 min before"),
        ("opened", "Incident opened by HighErrorRate on payment"),
        ("attached", "HighErrorRate on checkout added to this incident"),
        ("missing", "No evidence from logs"),
        ("resolved", "Incident resolved"),
    ]
    assert rows[0].evidence_id == "ev_deploy_1"
    assert rows[3].detail == "logs unavailable: timeout"
