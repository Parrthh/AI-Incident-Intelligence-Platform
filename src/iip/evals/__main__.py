"""``python -m iip.evals``: run the offline root-cause eval and write a report."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from iip.evals.report import write_report
from iip.evals.runner import evaluate
from iip.evals.snapshot import load_snapshots
from iip.llm.base import LLMProvider
from iip.llm.fake import FakeProvider
from iip.llm.gemini import GeminiProvider


def _provider(name: str, model: str) -> LLMProvider:
    if name == "fake":
        return FakeProvider(model="fake-model")
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("IIP_GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set")
    return GeminiProvider(api_key=key, model=model, timeout_seconds=60)


async def _main(args: argparse.Namespace) -> int:
    snapshots = load_snapshots(Path(args.datasets))
    if args.scenario:
        snapshots = [s for s in snapshots if s.scenario.name in args.scenario]
    if not snapshots:
        print(f"no snapshots under {args.datasets}", file=sys.stderr)
        return 2
    provider = _provider(args.provider, args.model)
    try:
        report = await evaluate(
            provider,
            snapshots,
            prompt_version=args.prompt_version,
            concurrency=args.concurrency,
            pause_seconds=args.pause,
        )
    finally:
        await provider.aclose()
    json_path, md_path = write_report(report, Path(args.out))
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {json_path} and {md_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m iip.evals")
    parser.add_argument("--datasets", default="evals/datasets")
    parser.add_argument("--provider", choices=["fake", "gemini"], default="fake")
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--prompt-version", default="v1")
    parser.add_argument("--scenario", action="append", help="only these scenarios")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between calls")
    parser.add_argument("--out", default="evals/reports/latest")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
