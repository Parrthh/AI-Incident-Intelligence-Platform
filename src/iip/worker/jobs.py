"""Job handlers: turn a queue message into work, and work results into queue outcomes.

A handler returns to acknowledge the message, raises ``PermanentError`` to
dead-letter it, or raises anything else to have it retried later (ADR-0002).
"""

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.llm.base import LLMProvider
from iip.llm.summarize import summarize_incident
from iip.queue.jobs import JOB_SUMMARIZE
from iip.queue.streams import Message, PermanentError
from iip.worker.consumer import Handler

logger = structlog.get_logger(__name__)


class RetryLaterError(Exception):
    """The job failed in a way that may succeed on a later delivery."""


def build_handlers(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LLMProvider | None,
    *,
    llm_max_retries: int,
) -> dict[str, Handler]:
    async def summarize(message: Message) -> None:
        try:
            incident_id = uuid.UUID(str(message.payload["incident_id"]))
        except (KeyError, ValueError) as exc:
            raise PermanentError(f"bad summarize payload: {exc}") from exc
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

    return {JOB_SUMMARIZE: summarize}
