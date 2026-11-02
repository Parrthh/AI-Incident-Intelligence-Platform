"""Compare an eval report with the stored baseline; fail on a regression.

Runs in CI when the prompt, the analysis code or the eval changes. The model is
not deterministic, so small moves are noise: with about 30 snapshots one case
is 3 points. The gate fails only on a clear drop.
"""

from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_TOP3_DROP = 0.10
DEFAULT_MAX_CITATION_DROP = 0.05


@dataclass(frozen=True)
class GateResult:
    passed: bool
    lines: list[str]


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def check(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    max_top3_drop: float = DEFAULT_MAX_TOP3_DROP,
    max_citation_drop: float = DEFAULT_MAX_CITATION_DROP,
) -> GateResult:
    now = report["summary"]
    lines: list[str] = []
    passed = True
    for key, label, allowed in (
        ("top3", "Top-3", max_top3_drop),
        ("citation_validity", "Citation validity", max_citation_drop),
    ):
        before, after = baseline.get(key), now.get(key)
        if before is None or after is None:
            lines.append(f"- {label}: {_pct(before)} → {_pct(after)} (not compared)")
            continue
        drop = before - after
        ok = drop <= allowed
        passed = passed and ok
        mark = "ok" if ok else f"FAIL: dropped more than {allowed * 100:.0f} points"
        lines.append(f"- {label}: {_pct(before)} → {_pct(after)} ({mark})")
    lines.append(
        f"- Top-1: {_pct(baseline.get('top1'))} → {_pct(now.get('top1'))} (reported, not gated)"
    )
    return GateResult(passed, lines)


def baseline_from(report: dict[str, Any], commit: str) -> dict[str, Any]:
    s = report["summary"]
    return {
        "model": report["model"],
        "prompt": report["prompt"],
        "snapshots": report["snapshots"],
        "measured_at": report["run_at"],
        "commit": commit,
        "top1": s["top1"],
        "top3": s["top3"],
        "service_top1": s["service_top1"],
        "citation_validity": s["citation_validity"],
    }
