"""Run root-cause analysis for one incident and store the result (M4).

Flow, like summaries (``llm/summarize.py``):
1. Load the incident, its alerts, its evidence and the "unavailable" notes.
2. Render the evidence into the prompt within a token budget; redact secrets.
3. The rendered input gets a fingerprint (``input_hash``). The same input is
   never analyzed twice; new evidence means a new analysis row.
4. Claim the row, call the model in JSON mode with no database connection held,
   parse and validate the reply, drop invented citations (ADR-0003), store.

A reply that isn't valid JSON of the right shape is asked for once more, then
recorded as failed. That's a model problem, not a network one, so the job isn't
retried again and again.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from iip.analysis.citations import validate_citations
from iip.analysis.prompt import build_analysis_prompt
from iip.analysis.schema import AnalysisResult, InvalidAnalysisError, parse_analysis
from iip.db.models import Analysis, Evidence, Incident, IncidentEvent
from iip.llm.base import LLMError, LLMProvider, LLMResponse
from iip.llm.prompting import load_prompt
from iip.llm.retry import with_retries
from iip.llm.summarize import DEFAULT_LEASE_SECONDS, claim_analysis_row
from iip.observability.metrics import ANALYSIS_CITATIONS, LLM_REQUEST_DURATION, LLM_TOKENS

logger = structlog.get_logger(__name__)

ANALYZE_PROMPT = ("analyze", "v1")
KIND_ANALYSIS = "analysis"
DEFAULT_BUDGET_TOKENS = 6000
DEFAULT_MAX_OUTPUT_TOKENS = 2048
PARSE_ATTEMPTS = 2

Outcome = Literal["done", "reused", "busy", "failed", "missing", "no_evidence"]


@dataclass(frozen=True)
class AnalysisOutcome:
    outcome: Outcome
    analysis_id: uuid.UUID | None = None
    error: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class _Input:
    prompt: str
    redactions: int
    input_hash: str
    known_ids: set[str]
    context: dict[str, Any]


async def _build_input(session: AsyncSession, incident: Incident, budget: int) -> _Input | None:
    prompt = load_prompt(*ANALYZE_PROMPT)
    evidence = list(
        (await session.scalars(select(Evidence).where(Evidence.incident_id == incident.id))).all()
    )
    if not evidence:
        return None
    missing_events = (
        await session.scalars(
            select(IncidentEvent).where(
                IncidentEvent.incident_id == incident.id,
                IncidentEvent.kind == "evidence_unavailable",
            )
        )
    ).all()
    # Only the latest note per source, and only if that source hasn't since worked.
    collected_after: dict[str, datetime] = {}
    for event in (
        await session.scalars(
            select(IncidentEvent).where(
                IncidentEvent.incident_id == incident.id,
                IncidentEvent.kind == "evidence_collected",
            )
        )
    ).all():
        source = str(event.payload.get("source"))
        collected_after[source] = max(collected_after.get(source, event.at), event.at)
    missing: dict[str, str] = {}
    for event in sorted(missing_events, key=lambda e: e.at):
        source = str(event.payload.get("source"))
        if collected_after.get(source, datetime.min.replace(tzinfo=UTC)) < event.at:
            missing[source] = str(event.payload.get("reason"))
    built = build_analysis_prompt(
        prompt,
        title=incident.title,
        status=incident.status,
        started_at=incident.started_at,
        services=incident.services,
        alerts=list(incident.alerts),
        evidence=evidence,
        missing=missing,
        budget_tokens=budget,
    )
    rendered = built.rendered
    return _Input(
        prompt=built.text,
        redactions=built.redactions,
        input_hash=rendered.fingerprint(prompt.id),
        known_ids={e.id for e in evidence},
        context={
            "included": rendered.included,
            "dropped": rendered.dropped,
            "tokens_estimate": rendered.tokens_estimate,
            "budget_tokens": budget,
            "unavailable": sorted(missing),
        },
    )


async def analyze_incident(
    sessionmaker: async_sessionmaker[AsyncSession],
    provider: LLMProvider,
    incident_id: uuid.UUID,
    *,
    max_retries: int = 2,
    claim_token: str | None = None,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> AnalysisOutcome:
    prompt = load_prompt(*ANALYZE_PROMPT)
    token = claim_token or uuid.uuid4().hex
    log = logger.bind(incident_id=str(incident_id), prompt=prompt.id, model=provider.model)

    async with sessionmaker() as session:
        incident = await session.scalar(
            select(Incident)
            .where(Incident.id == incident_id)
            .options(selectinload(Incident.alerts))
        )
        if incident is None:
            return AnalysisOutcome("missing")
        built = await _build_input(session, incident, budget_tokens)
        if built is None:
            log.info("analysis_skipped_no_evidence")
            return AnalysisOutcome("no_evidence")
        existing = await session.scalar(
            select(Analysis.id).where(
                Analysis.incident_id == incident_id,
                Analysis.kind == KIND_ANALYSIS,
                Analysis.prompt_version == prompt.id,
                Analysis.input_hash == built.input_hash,
                Analysis.status == "done",
            )
        )
        if existing is not None:
            return AnalysisOutcome("reused", existing)
        claimed = await claim_analysis_row(
            session,
            incident_id,
            provider,
            prompt.id,
            built.redactions,
            token,
            lease_seconds,
            kind=KIND_ANALYSIS,
            input_hash=built.input_hash,
        )
        await session.commit()
        if claimed is None:
            return AnalysisOutcome("busy")
        analysis_id, previous_attempts = claimed

    attempts = 0
    last_response: LLMResponse | None = None

    async def call() -> LLMResponse:
        nonlocal attempts
        attempts += 1
        return await provider.generate(
            built.prompt, system=prompt.system, max_output_tokens=max_output_tokens, json_mode=True
        )

    result: AnalysisResult | None = None
    error: str | None = None
    retryable = False
    for _ in range(PARSE_ATTEMPTS):
        try:
            last_response = await with_retries(call, max_retries=max_retries)
        except LLMError as exc:
            error, retryable = str(exc), exc.retryable
            break
        try:
            result = parse_analysis(last_response.text)
            break
        except InvalidAnalysisError as exc:
            error, retryable = str(exc), False
            log.warning("analysis_invalid_reply", error=str(exc))

    stored: dict[str, Any] | None = None
    if result is not None and last_response is not None:
        cleaned, report = validate_citations(result, built.known_ids)
        ANALYSIS_CITATIONS.labels(outcome="valid").inc(report.cited - report.invalid)
        ANALYSIS_CITATIONS.labels(outcome="invalid").inc(report.invalid)
        stored = {
            **cleaned.model_dump(mode="json"),
            "citations": {
                "cited": report.cited,
                "invalid": report.invalid,
                "invalid_ids": report.invalid_ids,
                "hypotheses_dropped": report.hypotheses_dropped,
                "validity": report.validity,
            },
            "context": built.context,
        }

    async with sessionmaker() as session:
        row = await session.get(Analysis, analysis_id)
        if row is None:
            return AnalysisOutcome("missing")
        row.attempts = previous_attempts + attempts
        row.completed_at = datetime.now(UTC)
        row.claimed_by = None
        row.claimed_at = None
        if stored is not None and last_response is not None:
            row.status = "done"
            row.result = stored
            row.summary = str(stored["summary"])
            row.model = last_response.model
            row.input_tokens = last_response.input_tokens
            row.output_tokens = last_response.output_tokens
            row.latency_ms = last_response.latency_ms
            row.error = None
        else:
            row.status = "failed"
            row.error = (error or "unknown error")[:1000]
        await session.commit()

    if stored is not None and last_response is not None:
        LLM_REQUEST_DURATION.labels(provider.name, last_response.model).observe(
            last_response.latency_ms / 1000
        )
        for direction, count in (
            ("input", last_response.input_tokens),
            ("output", last_response.output_tokens),
        ):
            if count is not None:
                LLM_TOKENS.labels(provider.name, last_response.model, direction).inc(count)
        log.info(
            "analysis_done",
            analysis_id=str(analysis_id),
            hypotheses=len(stored["hypotheses"]),
            citation_validity=stored["citations"]["validity"],
            latency_ms=last_response.latency_ms,
            evidence_included=len(built.context["included"]),
            evidence_dropped=len(built.context["dropped"]),
        )
        return AnalysisOutcome("done", analysis_id)
    log.warning("analysis_failed", analysis_id=str(analysis_id), error=error, retryable=retryable)
    return AnalysisOutcome("failed", analysis_id, error=error, retryable=retryable)
