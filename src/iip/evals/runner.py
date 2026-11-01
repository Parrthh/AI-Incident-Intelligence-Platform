"""Run the analysis on every snapshot and score it."""

import asyncio
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import structlog

from iip.analysis.analyzer import ANALYZE_PROMPT, DEFAULT_BUDGET_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS
from iip.analysis.citations import validate_citations
from iip.analysis.prompt import build_analysis_prompt
from iip.analysis.schema import InvalidAnalysisError, parse_analysis
from iip.evals.scoring import CaseResult, failed_case, score_case, summarize
from iip.evals.snapshot import Snapshot
from iip.llm.base import LLMError, LLMProvider
from iip.llm.prompting import load_prompt
from iip.llm.retry import with_retries

logger = structlog.get_logger(__name__)


async def evaluate_one(
    provider: LLMProvider,
    snapshot: Snapshot,
    *,
    prompt_version: str = ANALYZE_PROMPT[1],
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_retries: int = 3,
) -> CaseResult:
    prompt = load_prompt(ANALYZE_PROMPT[0], prompt_version)
    evidence = snapshot.orm_evidence()
    built = build_analysis_prompt(
        prompt,
        title=snapshot.incident.title,
        status=snapshot.incident.status,
        started_at=snapshot.incident.started_at,
        services=snapshot.incident.services,
        alerts=snapshot.orm_alerts(),
        evidence=evidence,
        missing=snapshot.unavailable,
        budget_tokens=budget_tokens,
    )
    last_error = "no answer"
    for _ in range(2):  # same policy as production: one more try on a bad reply
        try:
            response = await with_retries(
                lambda: provider.generate(
                    built.text,
                    system=prompt.system,
                    max_output_tokens=max_output_tokens,
                    json_mode=True,
                ),
                max_retries=max_retries,
            )
        except LLMError as exc:
            return failed_case(snapshot, f"LLM error: {exc}")
        try:
            result = parse_analysis(response.text)
        except InvalidAnalysisError as exc:
            last_error = str(exc)
            continue
        cleaned, report = validate_citations(result, {e.id for e in evidence})
        return score_case(
            snapshot,
            cleaned,
            report,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            included=len(built.rendered.included),
            dropped=len(built.rendered.dropped),
        )
    return failed_case(snapshot, last_error)


async def evaluate(
    provider: LLMProvider,
    snapshots: list[Snapshot],
    *,
    prompt_version: str = ANALYZE_PROMPT[1],
    concurrency: int = 2,
    pause_seconds: float = 0.0,
) -> dict[str, Any]:
    """Score every snapshot. Low concurrency and an optional pause keep free-tier
    rate limits happy."""
    slots = asyncio.Semaphore(concurrency)

    async def one(snapshot: Snapshot) -> CaseResult:
        async with slots:
            result = await evaluate_one(provider, snapshot, prompt_version=prompt_version)
            if pause_seconds:
                await asyncio.sleep(pause_seconds)
            logger.info("eval_case", snapshot=snapshot.id, top1=result.top1, top=result.top)
            return result

    started = time.perf_counter()
    cases = await asyncio.gather(*(one(s) for s in snapshots))
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": provider.name,
        "model": provider.model,
        "prompt": f"{ANALYZE_PROMPT[0]}_{prompt_version}",
        "snapshots": len(snapshots),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "summary": asdict(summarize(list(cases))),
        "cases": [asdict(c) for c in cases],
    }
