"""Write an LLM summary for an incident and store it as an ``analyses`` row.

Flow:
1. Load the incident and its alerts.
2. Build the prompt from the alert data and redact secrets out of it.
3. Record a ``pending`` analysis first, so a run that crashes midway is visible
   instead of silently missing.
4. Call the provider with retries; store ``done`` with the summary, tokens,
   latency and attempt count, or ``failed`` with the error.

Idempotent: if a finished summary already exists for this incident and prompt
version, it's reused and the LLM isn't called again. Retried deliveries and
repeated triggers therefore don't spend quota.

M1 runs this in FastAPI ``BackgroundTasks`` after the webhook responds. That is
known tech debt: if the process stops before the task finishes, the summary is
lost (the pending row stays behind as evidence). M2 moves it onto a durable queue.
"""

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
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
) -> uuid.UUID | None:
    """Summarize one incident. Returns the analysis id, or None if the incident is gone.

    Never raises for LLM failures: they're recorded on the analysis row. This runs
    in the background, where an exception would only end up in a log line.
    """
    prompt = load_prompt(*SUMMARY_PROMPT)
    log = logger.bind(incident_id=str(incident_id), prompt=prompt.id, model=provider.model)

    async with sessionmaker() as session:
        incident = await session.scalar(
            select(Incident)
            .where(Incident.id == incident_id)
            .options(selectinload(Incident.alerts))
        )
        if incident is None:
            log.warning("summary_skipped_incident_missing")
            return None

        existing = await _existing_summary(session, incident_id, prompt.version)
        if existing is not None:
            log.info("summary_reused", analysis_id=str(existing))
            return existing

        user_prompt, redactions = build_prompt(prompt, incident)
        analysis = Analysis(
            incident_id=incident_id,
            kind=KIND_SUMMARY,
            status="pending",
            provider=provider.name,
            model=provider.model,
            prompt_version=prompt.id,
            redactions=redactions,
            attempts=0,
        )
        session.add(analysis)
        await session.commit()
        analysis_id = analysis.id

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
            return None
        row.attempts = attempts
        row.completed_at = datetime.now(UTC)
        if response is not None:
            row.status = "done"
            row.summary = response.text
            row.model = response.model
            row.input_tokens = response.input_tokens
            row.output_tokens = response.output_tokens
            row.latency_ms = response.latency_ms
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
    else:
        log.warning(
            "summary_failed",
            analysis_id=str(analysis_id),
            attempts=attempts,
            error=str(error),
            retryable=error.retryable if error else None,
        )
    return analysis_id


async def _existing_summary(
    session: AsyncSession, incident_id: uuid.UUID, prompt_version: str
) -> uuid.UUID | None:
    return await session.scalar(
        select(Analysis.id)
        .where(
            Analysis.incident_id == incident_id,
            Analysis.kind == KIND_SUMMARY,
            Analysis.status == "done",
            Analysis.prompt_version == f"{SUMMARY_PROMPT[0]}_{prompt_version}",
        )
        .limit(1)
    )
