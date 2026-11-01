"""Write an LLM summary for an incident and store it as an ``analyses`` row.

Runs as a background job (worker, ADR-0002). Flow:
1. Load the incident and its alerts. Stop if a finished summary already exists.
2. Build the prompt from the alert data and redact secrets out of it.
3. **Claim** the incident's analysis row: insert it, or take over an existing
   unfinished one, marking it ``pending`` and ``claimed_by`` this job delivery.
4. Call the provider with retries, with no database connection held.
5. Store ``done`` with the summary, tokens, latency and attempts, or ``failed``
   with the error, and release the claim.

Idempotency. Delivery is at least once, so the same job can arrive twice, and
two different jobs can exist for one incident (an outbox row published twice).
There is one analyses row per incident, kind and prompt version (a unique
constraint), and a delivery may only work on it if:
- it isn't ``done`` (a finished summary is reused, the LLM isn't called again), and
- nobody else holds the claim: it's unclaimed, claimed by this same delivery
  (a redelivery after a crash), or the claim is older than ``lease_seconds``
  (the holder died and its job is gone).
Otherwise the outcome is ``busy`` and the job just finishes: the holder's own
job is still pending in Redis and is redelivered if that holder dies.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from iip.db.models import Alert, Analysis, Incident
from iip.llm.base import LLMError, LLMProvider, LLMResponse
from iip.llm.prompting import Prompt, load_prompt
from iip.llm.redact import redact
from iip.llm.retry import with_retries

logger = structlog.get_logger(__name__)

SUMMARY_PROMPT = ("summary", "v0")
KIND_SUMMARY = "summary"
MAX_ALERTS_IN_PROMPT = 20
# How long a claim protects a row. Far longer than one run takes (about 95 s at
# most), so it only matters when a worker died and its job was lost.
DEFAULT_LEASE_SECONDS = 600

Outcome = Literal["done", "reused", "busy", "failed", "missing"]


@dataclass(frozen=True)
class SummaryResult:
    outcome: Outcome
    analysis_id: uuid.UUID | None = None
    error: str | None = None
    retryable: bool = False


def format_alerts(alerts: list[Alert]) -> str:
    lines: list[str] = []
    for alert in sorted(alerts, key=lambda a: a.starts_at)[:MAX_ALERTS_IN_PROMPT]:
        labels = ", ".join(f"{k}={v}" for k, v in sorted(alert.labels.items()))
        notes = "; ".join(f"{k}: {v}" for k, v in sorted(alert.annotations.items()))
        lines.append(
            f"- {alert.alertname} [{alert.status}] since {alert.starts_at.isoformat()}"
            f"\n  labels: {labels}" + (f"\n  annotations: {notes}" if notes else "")
        )
    if len(alerts) > MAX_ALERTS_IN_PROMPT:
        lines.append(f"- ... and {len(alerts) - MAX_ALERTS_IN_PROMPT} more alerts")
    return "\n".join(lines)


def build_prompt(prompt: Prompt, incident: Incident) -> tuple[str, int]:
    """Return the rendered user prompt (redacted) and how many items were redacted."""
    rendered = prompt.render(
        title=incident.title,
        status=incident.status,
        started_at=incident.started_at.isoformat(),
        services=", ".join(incident.services) or "unknown",
        alerts=format_alerts(list(incident.alerts)),
    )
    result = redact(rendered)
    return result.text, result.total


async def summarize_incident(
    sessionmaker: async_sessionmaker[AsyncSession],
    provider: LLMProvider,
    incident_id: uuid.UUID,
    *,
    max_retries: int = 2,
    claim_token: str | None = None,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
) -> SummaryResult:
    """Summarize one incident. Never raises for LLM failures: they're recorded on
    the row and returned, and the caller decides whether to retry the job."""
    prompt = load_prompt(*SUMMARY_PROMPT)
    token = claim_token or uuid.uuid4().hex
    log = logger.bind(incident_id=str(incident_id), prompt=prompt.id, model=provider.model)

    async with sessionmaker() as session:
        incident = await session.scalar(
            select(Incident)
            .where(Incident.id == incident_id)
            .options(selectinload(Incident.alerts))
        )
        if incident is None:
            log.warning("summary_skipped_incident_missing")
            return SummaryResult("missing")

        existing = await _done_summary(session, incident_id, prompt.id)
        if existing is not None:
            log.info("summary_reused", analysis_id=str(existing))
            return SummaryResult("reused", existing)

        user_prompt, redactions = build_prompt(prompt, incident)
        claimed = await _claim(
            session, incident_id, provider, prompt.id, redactions, token, lease_seconds
        )
        await session.commit()
        if claimed is None:
            done = await _done_summary(session, incident_id, prompt.id)
            if done is not None:  # finished by another delivery just now
                return SummaryResult("reused", done)
            log.info("summary_busy", claim=token)
            return SummaryResult("busy")
        analysis_id, previous_attempts = claimed

    attempts = 0

    async def call() -> LLMResponse:
        nonlocal attempts
        attempts += 1
        return await provider.generate(user_prompt, system=prompt.system, max_output_tokens=1024)

    # The LLM call happens with no database session open. It can take tens of
    # seconds with retries; holding a pooled connection that long would starve
    # API requests on a small pool.
    response: LLMResponse | None = None
    error: LLMError | None = None
    try:
        response = await with_retries(call, max_retries=max_retries)
    except LLMError as exc:
        error = exc

    async with sessionmaker() as session:
        row = await session.get(Analysis, analysis_id)
        if row is None:  # deleted meanwhile (incident removed)
            log.warning("summary_skipped_analysis_missing", analysis_id=str(analysis_id))
            return SummaryResult("missing")
        row.attempts = previous_attempts + attempts
        row.completed_at = datetime.now(UTC)
        row.claimed_by = None
        row.claimed_at = None
        if response is not None:
            row.status = "done"
            row.summary = response.text
            row.model = response.model
            row.input_tokens = response.input_tokens
            row.output_tokens = response.output_tokens
            row.latency_ms = response.latency_ms
            row.error = None
        else:
            row.status = "failed"
            row.error = str(error)[:1000]
        await session.commit()

    if response is not None:
        log.info(
            "summary_done",
            analysis_id=str(analysis_id),
            attempts=attempts,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            redactions=redactions,
        )
        return SummaryResult("done", analysis_id)
    retryable = error.retryable if error else False
    log.warning(
        "summary_failed",
        analysis_id=str(analysis_id),
        attempts=attempts,
        error=str(error),
        retryable=retryable,
    )
    return SummaryResult("failed", analysis_id, error=str(error), retryable=retryable)


async def _claim(
    session: AsyncSession,
    incident_id: uuid.UUID,
    provider: LLMProvider,
    prompt_id: str,
    redactions: int,
    token: str,
    lease_seconds: float,
) -> tuple[uuid.UUID, int] | None:
    """Insert or take over the analysis row in one atomic statement.

    Returns (id, attempts so far), or None if the row is done or claimed by
    someone else. ``ON CONFLICT ... DO UPDATE ... WHERE`` makes the check and the
    takeover a single step, so two deliveries racing can't both win.
    """
    stmt = insert(Analysis).values(
        incident_id=incident_id,
        kind=KIND_SUMMARY,
        status="pending",
        provider=provider.name,
        model=provider.model,
        prompt_version=prompt_id,
        redactions=redactions,
        attempts=0,
        claimed_by=token,
        claimed_at=func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_analyses_incident_kind_prompt",
        set_={
            "status": "pending",
            "provider": stmt.excluded.provider,
            "model": stmt.excluded.model,
            "redactions": stmt.excluded.redactions,
            "claimed_by": token,
            "claimed_at": func.now(),
        },
        where=(Analysis.status != "done")
        & or_(
            Analysis.claimed_by.is_(None),
            Analysis.claimed_by == token,
            Analysis.claimed_at < func.now() - literal(timedelta(seconds=lease_seconds)),
        ),
    ).returning(Analysis.id, Analysis.attempts)
    row = (await session.execute(stmt)).one_or_none()
    return None if row is None else (row[0], row[1])


async def _done_summary(
    session: AsyncSession, incident_id: uuid.UUID, prompt_id: str
) -> uuid.UUID | None:
    return await session.scalar(
        select(Analysis.id).where(
            Analysis.incident_id == incident_id,
            Analysis.kind == KIND_SUMMARY,
            Analysis.status == "done",
            Analysis.prompt_version == prompt_id,
        )
    )
