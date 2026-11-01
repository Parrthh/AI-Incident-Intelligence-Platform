from typing import Any

from iip.evals.gate import baseline_from, check

BASELINE = {
    "model": "m",
    "prompt": "analyze_v1",
    "measured_at": "t",
    "commit": "c",
    "top1": 0.5,
    "top3": 0.8,
    "citation_validity": 1.0,
}


def report(top3: float | None, validity: float | None, top1: float = 0.5) -> dict[str, Any]:
    return {
        "model": "m",
        "prompt": "analyze_v1",
        "snapshots": 30,
        "run_at": "now",
        "summary": {
            "top1": top1,
            "top3": top3,
            "service_top1": 0.7,
            "citation_validity": validity,
        },
    }


def test_small_moves_pass() -> None:
    result = check(report(0.72, 0.97), BASELINE)
    assert result.passed
    assert result.lines[0] == "- Top-3: 80% → 72% (ok)"


def test_a_clear_top3_drop_fails() -> None:
    result = check(report(0.65, 1.0), BASELINE)
    assert not result.passed
    assert "FAIL" in result.lines[0]


def test_more_invented_citations_fail() -> None:
    assert not check(report(0.8, 0.9), BASELINE).passed


def test_missing_numbers_are_not_compared() -> None:
    result = check(report(None, None), BASELINE)
    assert result.passed
    assert "not compared" in result.lines[0]


def test_baseline_from_report() -> None:
    assert baseline_from(report(0.8, 1.0), "abc") == {
        "model": "m",
        "prompt": "analyze_v1",
        "snapshots": 30,
        "measured_at": "now",
        "commit": "abc",
        "top1": 0.5,
        "top3": 0.8,
        "service_top1": 0.7,
        "citation_validity": 1.0,
    }
