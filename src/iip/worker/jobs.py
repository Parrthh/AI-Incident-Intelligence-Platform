"""Job handlers: turn a queue message into work, and work results into queue outcomes.

A handler returns to acknowledge the message, raises ``PermanentError`` to
dead-letter it, or raises anything else to have it retried later (ADR-0002).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.analysis.analyzer import DEFAULT_BUDGET_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS, analyze_incident
from iip.enrichment.runner import Enricher
from iip.llm.base import LLMProvider
from iip.llm.summarize import summarize_incident
from iip.queue.jobs import JOB_ANALYZE, JOB_ENRICH, JOB_SUMMARIZE
from iip.queue.streams import Message, PermanentError
from iip.worker.consumer import Handler

logger = structlog.get_logger(__name__)


PublishJob = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class AnalysisOptions:
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


class RetryLaterError(Exception):
    """The job failed in a way that may succeed on a later delivery."""


def _incident_id(message: Message) -> uuid.UUID:
    try:
        return uuid.UUID(str(message.payload["incident_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentError(f"bad {message.type} payload: {exc}") from exc


def build_handlers(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LLMProvider | None,
    *,
    llm_max_retries: int,
    enricher: Enricher | None = None,
    publish_job: PublishJob | None = None,
    analysis: AnalysisOptions | None = None,
) -> dict[str, Handler]:
    analysis = analysis or AnalysisOptions()

    async def summarize(message: Message) -> None:
        incident_id = _incident_id(message)
        if llm is None:
            logger.info("summary_skipped_no_llm", incident_id=str(incident_id))
            return
        result = await summarize_incident(
            sessionmaker,
            llm,
            incident_id,
            max_retries=llm_max_retries,
            # The stream message ID identifies this job across redeliveries, so a
            # redelivery after a crash may take over its own unfinished row.
            claim_token=message.id,
        )
        if result.outcome == "failed":
            if result.retryable:
                raise RetryLaterError(result.error)
            raise PermanentError(result.error or "summary failed")

    async def enrich(message: Message) -> None:
        incident_id = _incident_id(message)
        if enricher is None:
            logger.info("enrichment_skipped_not_configured", incident_id=str(incident_id))
            return
        # Source failures are recorded on the timeline, not raised; only a
        # database error gets here, and the queue retries it.
        if await enricher.enrich(incident_id) is None:
            logger.info("enrichment_skipped_incident_missing", incident_id=str(incident_id))
            return
        # New evidence: analyze. If this publish fails, the enrich job is retried,
        # which is harmless (collection and analysis are both idempotent).
        if llm is not None and publish_job is not None:
            await publish_job(JOB_ANALYZE, {"incident_id": str(incident_id)})

    async def analyze(message: Message) -> None:
        incident_id = _incident_id(message)
        if llm is None:
            logger.info("analysis_skipped_no_llm", incident_id=str(incident_id))
            return
        result = await analyze_incident(
            sessionmaker,
            llm,
            incident_id,
            max_retries=llm_max_retries,
            claim_token=message.id,
            budget_tokens=analysis.budget_tokens,
            max_output_tokens=analysis.max_output_tokens,
        )
        if result.outcome == "failed":
            if result.retryable:
                raise RetryLaterError(result.error)
            raise PermanentError(result.error or "analysis failed")

    return {JOB_SUMMARIZE: summarize, JOB_ENRICH: enrich, JOB_ANALYZE: analyze}
