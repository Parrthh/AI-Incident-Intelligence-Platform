"""``python -m iip.evals``: run the offline root-cause eval and write a report.

With ``--baseline``, the run is compared with the stored baseline and the exit
code is 1 on a regression (the CI gate). ``--write-baseline`` stores this run's
scores as the new baseline.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from iip.evals.gate import DEFAULT_MAX_TOP3_DROP, baseline_from, check
from iip.evals.report import write_report
from iip.evals.runner import evaluate
from iip.evals.snapshot import Snapshot, load_snapshots
from iip.llm.base import LLMProvider
from iip.llm.fake import FakeProvider
from iip.llm.gemini import GeminiProvider
from iip.llm.ollama import OllamaProvider


def _provider(name: str, model: str) -> LLMProvider:
    if name == "fake":
        return FakeProvider(model="fake-model")
    if name == "ollama":
        url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        return OllamaProvider(base_url=url, model=model, timeout_seconds=600)
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("IIP_GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set")
    return GeminiProvider(api_key=key, model=model, timeout_seconds=60)


async def _run(args: argparse.Namespace, snapshots: list[Snapshot]) -> dict[str, Any]:
    provider = _provider(args.provider, args.model)
    try:
        return await evaluate(
            provider,
            snapshots,
            prompt_version=args.prompt_version,
            concurrency=args.concurrency,
            pause_seconds=args.pause,
        )
    finally:
        await provider.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m iip.evals")
    parser.add_argument("--datasets", default="evals/datasets")
    parser.add_argument("--provider", choices=["fake", "gemini", "ollama"], default="fake")
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--prompt-version", default="v1")
    parser.add_argument("--scenario", action="append", help="only these scenarios")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between calls")
    parser.add_argument("--out", default="evals/reports/latest")
    parser.add_argument("--baseline", help="compare with this baseline; exit 1 on a regression")
    parser.add_argument("--max-top3-drop", type=float, default=DEFAULT_MAX_TOP3_DROP)
    parser.add_argument("--write-baseline", help="write this run's scores as the new baseline")
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", "local")[:12])
    args = parser.parse_args()

    snapshots = load_snapshots(Path(args.datasets))
    if args.scenario:
        snapshots = [s for s in snapshots if s.scenario.name in args.scenario]
    if not snapshots:
        print(f"no snapshots under {args.datasets}", file=sys.stderr)
        return 2

    report = asyncio.run(_run(args, snapshots))
    json_path, md_path = write_report(report, Path(args.out))
    status = 0
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        gate = check(report, baseline, max_top3_drop=args.max_top3_drop)
        verdict = "passed" if gate.passed else "failed"
        lines = [
            "",
            f"### Regression gate: {verdict}",
            "",
            f"Baseline: {baseline['model']}, {baseline['prompt']}, "
            f"measured {baseline['measured_at']} at {baseline['commit']}.",
            "",
            *gate.lines,
            "",
        ]
        with md_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        status = 0 if gate.passed else 1
    if args.write_baseline:
        baseline_json = json.dumps(baseline_from(report, args.commit), indent=2) + "\n"
        Path(args.write_baseline).write_text(baseline_json, encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {json_path} and {md_path}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
