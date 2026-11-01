"""The offline eval: snapshot loading, scoring and the report, with a fake model."""

import json
from pathlib import Path

from iip.evals.report import to_markdown, write_report
from iip.evals.runner import evaluate, evaluate_one
from iip.evals.scoring import summarize
from iip.evals.snapshot import Scenario, load_snapshots
from iip.llm.fake import FakeProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots"


def analysis(service: str, category: str, *more: tuple[str, str], cite: str = "ev_trace_1") -> str:
    pairs = [(service, category), *more]
    return json.dumps(
        {
            "summary": "s",
            "hypotheses": [
                {
                    "rank": n,
                    "root_cause_service": svc,
                    "category": cat,
                    "explanation": "e",
                    "confidence": 0.9 - n / 10,
                    "evidence_ids": [cite],
                }
                for n, (svc, cat) in enumerate(pairs, start=1)
            ],
        }
    )


def test_scenario_accepts_truth_and_alternatives() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "x",
            "root_cause_service": "payment",
            "category": "service_down",
            "also_correct": [{"service": "checkout", "category": "config_error"}],
        }
    )
    assert scenario.accepts("payment", "service_down")
    assert scenario.accepts("checkout", "config_error")
    assert not scenario.accepts("checkout", "service_down")
    assert scenario.accepts_service("checkout")


async def test_scores_top1_top3_and_service_only() -> None:
    [snapshot] = [s for s in load_snapshots(FIXTURES) if s.scenario.name == "payment_failure"]

    exact = await evaluate_one(FakeProvider(json_reply=analysis("payment", "code_bug")), snapshot)
    second = await evaluate_one(
        FakeProvider(json_reply=analysis("checkout", "code_bug", ("payment", "code_bug"))), snapshot
    )
    service_only = await evaluate_one(
        FakeProvider(json_reply=analysis("payment", "config_error")), snapshot
    )

    assert (exact.top1, exact.top3, exact.service_top1) == (True, True, True)
    assert (second.top1, second.top3, second.service_top1) == (False, True, False)
    assert (service_only.top1, service_only.top3, service_only.service_top1) == (False, False, True)
    assert exact.top_confidence == 0.8
    assert exact.evidence_included == 3


async def test_invented_citations_count_against_validity() -> None:
    [snapshot] = [s for s in load_snapshots(FIXTURES) if s.scenario.name == "payment_failure"]
    reply = analysis("payment", "code_bug", cite="ev_trace_99")

    case = await evaluate_one(FakeProvider(json_reply=reply), snapshot)

    assert case.ok
    assert case.top is None  # the only hypothesis cited nothing real, so it was dropped
    assert not case.top1
    assert (case.cited, case.invalid_citations, case.hypotheses_dropped) == (1, 1, 1)


async def test_a_reply_that_never_parses_is_a_failed_case() -> None:
    [snapshot] = [s for s in load_snapshots(FIXTURES) if s.scenario.name == "payment_failure"]

    case = await evaluate_one(FakeProvider(json_reply="I think it's payment."), snapshot)

    assert not case.ok
    assert case.error is not None
    assert case.error.startswith("reply is not JSON")


async def test_full_run_summary_and_report(tmp_path: Path) -> None:
    snapshots = load_snapshots(FIXTURES)
    # The default fake answer: the first listed service, category "unknown".
    report = await evaluate(FakeProvider(), snapshots)

    s = report["summary"]
    assert s["cases"] == 2
    assert s["top1"] == 0.0  # category "unknown" is never right
    assert s["service_top1"] == 1.0  # payment for one, checkout (accepted) for the other
    assert s["citation_validity"] == 1.0
    assert set(s["per_scenario"]) == {"payment_failure", "payment_unreachable"}

    json_path, md_path = write_report(report, tmp_path)
    assert json.loads(json_path.read_text())["summary"]["cases"] == 2
    markdown = md_path.read_text()
    assert "| Top-1 (service and category) | 0% |" in markdown
    assert "| payment_unreachable | 1 | 0% | 0% | 100% |" in markdown
    assert "Misses" in markdown


def test_summary_of_nothing_is_all_unknown() -> None:
    s = summarize([])
    assert (s.cases, s.top1, s.citation_validity) == (0, None, None)
    assert to_markdown(
        {
            "model": "m",
            "prompt": "p",
            "snapshots": 0,
            "run_at": "now",
            "summary": s.__dict__,
            "cases": [],
        }
    ).startswith("## Root-cause eval: m, prompt p")
