"""Retry with exponential backoff and full jitter.

Waits grow exponentially (1s, 2s, 4s...) so a struggling provider gets breathing
room. "Full jitter" picks a random wait between 0 and that cap: if many callers
fail at the same moment, their retries spread out instead of arriving together
and causing the next failure (the "thundering herd").
See the AWS Builders' Library article "Timeouts, retries and backoff with jitter".

Only retryable errors are retried (see ``LLMError``).
"""

import asyncio
import random
from collections.abc import Awaitable, Callable

import structlog

from iip.llm.base import LLMError

logger = structlog.get_logger(__name__)


async def with_retries[T](
    call: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    attempt = 0
    while True:
        try:
            return await call()
        except LLMError as exc:
            if not exc.retryable or attempt >= max_retries:
                raise
            cap = min(max_delay, base_delay * (2**attempt))
            delay = random.uniform(0, cap)  # noqa: S311 - jitter, not security
            attempt += 1
            logger.warning(
                "llm_retry",
                attempt=attempt,
                max_retries=max_retries,
                delay_s=round(delay, 2),
                error=str(exc),
            )
            await sleep(delay)
