from datetime import UTC, datetime

import pytest

from iip.db.models import Alert, Incident
from iip.llm.prompting import load_prompt
from iip.llm.summarize import SUMMARY_PROMPT, build_prompt


def make_incident(annotations: dict[str, str] | None = None) -> Incident:
    started = datetime(2026, 11, 1, 4, 10, tzinfo=UTC)
    alert = Alert(
        alertname="HighErrorRate",
        status="firing",
        labels={"alertname": "HighErrorRate", "service": "checkout"},
        annotations=annotations or {"summary": "checkout errors"},
        starts_at=started,
    )
    return Incident(
        title="checkout errors",
        status="open",
        services=["checkout"],
        started_at=started,
        alerts=[alert],
    )


def test_summary_prompt_has_system_and_user_sections() -> None:
    prompt = load_prompt(*SUMMARY_PROMPT)

    assert prompt.id == "summary_v0"
    assert "on-call" in prompt.system
    assert "$alerts" in prompt.user.template


def test_build_prompt_includes_alert_data() -> None:
    text, redactions = build_prompt(load_prompt(*SUMMARY_PROMPT), make_incident())

    assert "HighErrorRate [firing]" in text
    assert "service=checkout" in text
    assert "2026-11-01T04:10:00+00:00" in text
    assert redactions == 0


def test_build_prompt_redacts_secrets_in_annotations() -> None:
    incident = make_incident({"runbook": "see https://bot:tok3n@wiki.internal/runbook"})

    text, redactions = build_prompt(load_prompt(*SUMMARY_PROMPT), incident)

    assert "tok3n" not in text
    assert redactions == 1


def test_unknown_prompt_version_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("summary", "v999")
