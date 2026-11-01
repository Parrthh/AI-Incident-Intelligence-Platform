import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from iip.analysis.citations import validate_citations
from iip.analysis.context import rank, render_evidence
from iip.analysis.schema import Category, InvalidAnalysisError, parse_analysis
from iip.db.models import Evidence
from iip.llm.fake import FakeProvider

T0 = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)


def reply(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "Payment is failing; checkout fails because it calls payment.",
        "timeline": [
            {"at": "14:00:00", "description": "errors start", "evidence_ids": ["ev_trace_1"]}
        ],
        "hypotheses": [
            {
                "rank": 1,
                "root_cause_service": "payment",
                "category": "code_bug",
                "explanation": "Invalid token errors originate in payment.",
                "confidence": 0.8,
                "evidence_ids": ["ev_trace_1", "ev_log_1"],
                "next_steps": ["Check the token validation change."],
            }
        ],
        "missing_information": [],
    }
    base.update(overrides)
    return base


def test_parse_accepts_json_and_code_fences() -> None:
    plain = parse_analysis(json.dumps(reply()))
    fenced = parse_analysis("```json\n" + json.dumps(reply()) + "\n```")
    assert plain == fenced
    assert plain.hypotheses[0].category is Category.CODE_BUG


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("The root cause is payment.", "reply is not JSON"),
        (json.dumps({"hypotheses": []}), "wrong shape: summary"),
        (
            json.dumps(reply(hypotheses=[{**reply()["hypotheses"][0], "category": "aliens"}])),
            "wrong shape: hypotheses.0.category",
        ),
        (
            json.dumps(reply(hypotheses=[{**reply()["hypotheses"][0], "confidence": 1.7}])),
            "wrong shape: hypotheses.0.confidence",
        ),
    ],
)
def test_parse_rejects_bad_replies(text: str, message: str) -> None:
    with pytest.raises(InvalidAnalysisError, match=message):
        parse_analysis(text)


def test_invented_citations_are_removed_and_ungrounded_hypotheses_dropped() -> None:
    result = parse_analysis(
        json.dumps(
            reply(
                hypotheses=[
                    {**reply()["hypotheses"][0], "rank": 1, "evidence_ids": ["ev_made_up_9"]},
                    {
                        **reply()["hypotheses"][0],
                        "rank": 2,
                        "root_cause_service": "checkout",
                        "evidence_ids": ["ev_trace_1", "ev_trace_1", "ev_log_77"],
                    },
                ]
            )
        )
    )

    cleaned, report = validate_citations(result, {"ev_trace_1", "ev_log_1"})

    assert [(h.rank, h.root_cause_service, h.evidence_ids) for h in cleaned.hypotheses] == [
        (1, "checkout", ["ev_trace_1"])  # re-ranked, duplicate citation collapsed
    ]
    assert report.hypotheses_dropped == 1
    assert report.cited == 5  # 1 + 3 in hypotheses, 1 in the timeline
    assert report.invalid == 2
    assert report.invalid_ids == ["ev_log_77", "ev_made_up_9"]
    assert report.validity == pytest.approx(3 / 5)


def ev(
    id_: str, kind: str, minutes: float, service: str, count: int = 0, summary: str = ""
) -> Evidence:
    return Evidence(
        incident_id=uuid.UUID(int=1),
        id=id_,
        kind=kind,
        source="t",
        summary=summary or f"{kind} on {service}",
        payload={"service": service, "count": count},
        observed_at=T0 + timedelta(minutes=minutes),
        dedupe_key=id_,
    )


def test_ranking_puts_own_services_deploys_and_close_items_first() -> None:
    items = [
        ev("ev_metric_1", "metric", 0, "payment"),
        ev("ev_trace_9", "trace", 0, "load-generator", count=99),
        ev("ev_trace_2", "trace", 3, "payment", count=5),
        ev("ev_trace_1", "trace", 1, "payment", count=5),
        ev("ev_deploy_1", "deploy", -4, "payment"),
        ev("ev_log_1", "log", 1, "payment", count=50),
        ev("ev_alert_1", "alert", 0, "payment"),
    ]
    ordered = [e.id for e in rank(items, {"payment"}, T0)]
    assert ordered == [
        "ev_deploy_1",
        "ev_trace_1",
        "ev_trace_2",
        "ev_log_1",
        "ev_metric_1",
        "ev_alert_1",
        "ev_trace_9",  # another service's evidence comes last, however big
    ]


def test_budget_drops_the_least_useful_items_and_records_them() -> None:
    items = [ev(f"ev_log_{n}", "log", n, "payment", summary="x" * 400) for n in range(1, 6)]

    rendered = render_evidence(items, {"payment"}, T0, budget_tokens=250)

    assert rendered.included == ["ev_log_1", "ev_log_2"]
    assert rendered.dropped == ["ev_log_3", "ev_log_4", "ev_log_5"]
    assert rendered.tokens_estimate <= 250
    assert rendered.text.startswith("[ev_log_1] log, 14:01:00: ")
    # Same input, same fingerprint; another prompt version, another fingerprint.
    again = render_evidence(items, {"payment"}, T0, budget_tokens=250)
    assert again.fingerprint("analyze_v1") == rendered.fingerprint("analyze_v1")
    assert rendered.fingerprint("analyze_v2") != rendered.fingerprint("analyze_v1")


async def test_fake_provider_answers_json_mode_with_a_valid_grounded_analysis() -> None:
    prompt = "Services: payment, checkout\n[ev_trace_1] trace\n[ev_log_1] log\n[ev_trace_1] again"
    response = await FakeProvider().generate(prompt, json_mode=True)
    result = parse_analysis(response.text)
    assert result.hypotheses[0].root_cause_service == "payment"
    assert result.hypotheses[0].evidence_ids == ["ev_trace_1", "ev_log_1"]
