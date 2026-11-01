"""Write an eval report as Markdown (for people and PR comments) and JSON."""

import json
from pathlib import Path
from typing import Any


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:,.0f}{unit}"


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"## Root-cause eval: {report['model']}, prompt {report['prompt']}",
        "",
        f"{report['snapshots']} snapshots from chaos runs, scored against the known cause. "
        f"Run {report['run_at']}.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Top-1 (service and category) | {_pct(s['top1'])} |",
        f"| Top-3 (service and category) | {_pct(s['top3'])} |",
        f"| Service right, top-1 | {_pct(s['service_top1'])} |",
        f"| Service right, top-3 | {_pct(s['service_top3'])} |",
        f"| Citations pointing at real evidence | {_pct(s['citation_validity'])} |",
        f"| Hypotheses dropped for no valid citation | {s['hypotheses_dropped']} |",
        f"| Failed analyses (no usable answer) | {s['failed']} of {s['cases']} |",
        f"| Mean confidence when right / wrong | {_pct(s['confidence_when_right'])} / "
        f"{_pct(s['confidence_when_wrong'])} |",
        f"| Latency p50 / max | {_num(s['latency_ms_p50'], ' ms')} / "
        f"{_num(s['latency_ms_max'], ' ms')} |",
        f"| Tokens in / out (mean) | {_num(s['input_tokens_mean'])} / "
        f"{_num(s['output_tokens_mean'])} |",
        "",
        "| Scenario | Cases | Top-1 | Top-3 | Service top-1 |",
        "|---|---|---|---|---|",
    ]
    for name, row in s["per_scenario"].items():
        lines.append(
            f"| {name} | {row['cases']} | {_pct(row['top1'])} | {_pct(row['top3'])} | "
            f"{_pct(row['service_top1'])} |"
        )
    wrong = [c for c in report["cases"] if not c["top1"]]
    if wrong:
        lines += ["", "<details><summary>Misses</summary>", ""]
        for c in wrong:
            got = c["error"] or (f"{c['top'][0]} / {c['top'][1]}" if c["top"] else "no hypothesis")
            lines.append(f"- `{c['snapshot_id']}`: {got}")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path
