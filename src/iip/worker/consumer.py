"""Consumer loop: read messages from a stream queue and run handlers on them.

Per message, the outcome decides what happens next:
- handler returns: the message is acknowledged (done).
- handler raises ``PermanentError``: the message is dead-lettered right away.
- handler raises anything else: the message is left pending. ``claim_due``
  picks it up again after the backoff, until ``max_deliveries`` is reached.
- no handler for the message type: dead-lettered (retrying can't help).

Shutdown: the blocking read can wait up to ``block_ms``. The loop races it
against the ``stop`` event, so a SIGTERM stops the worker within milliseconds.
A read cancelled that way loses nothing: anything Redis delivered to this
consumer in the meantime stays pending and is reclaimed later.
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping

import structlog

from iip.observability.metrics import JOB_DURATION, JOBS
from iip.queue.streams import Message, PermanentError, StreamQueue

logger = structlog.get_logger(__name__)

Handler = Callable[[Message], Awaitable[None]]

DEFAULT_RECLAIM_INTERVAL_SECONDS = 30.0
# Pause after an unexpected Redis error, so an outage doesn't become a hot loop
# that burns the command budget.
ERROR_PAUSE_SECONDS = 5.0


async def handle_message(
    queue: StreamQueue, handlers: Mapping[str, Handler], message: Message
) -> str:
    """Run one message's handler and settle it. Returns the outcome name."""
    started = time.perf_counter()
    outcome = await _handle(queue, handlers, message)
    JOBS.labels(type=message.type, outcome=outcome).inc()
    if outcome != "dead_lettered" or message.type in handlers:
        JOB_DURATION.labels(type=message.type).observe(time.perf_counter() - started)
    return outcome


async def _handle(queue: StreamQueue, handlers: Mapping[str, Handler], message: Message) -> str:
    log = logger.bind(
        stream=queue.stream, message_id=message.id, type=message.type, deliveries=message.deliveries
    )
    handler = handlers.get(message.type)
    if handler is None:
        await queue.dead_letter(message, f"no handler for message type {message.type!r}")
        return "dead_lettered"
    started = time.perf_counter()
    try:
        await handler(message)
    except PermanentError as exc:
        await queue.dead_letter(message, f"{type(exc).__name__}: {exc}")
        return "dead_lettered"
    except Exception as exc:  # noqa: BLE001 - any other failure is retried
        log.warning("message_failed", error_type=type(exc).__name__, error=str(exc)[:300])
        return "retry"
    await queue.ack(message)
    log.info("message_done", duration_ms=round((time.perf_counter() - started) * 1000))
    return "done"


async def _wait_or_stop[T](stop: asyncio.Event, awaitable: Awaitable[T]) -> T | None:
    """Await ``awaitable`` unless ``stop`` is set first; then cancel it and return None."""
    work = asyncio.ensure_future(awaitable)
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({work, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopper.cancel()
    if work.done():
        return work.result()
    work.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await work
    return None


async def _handle_batch(
    queue: StreamQueue,
    handlers: Mapping[str, Handler],
    messages: list[Message],
    concurrency: int,
) -> None:
    """Handle up to ``concurrency`` messages at once.

    Jobs spend most of their time waiting on the LLM, so running a few side by
    side multiplies throughput without more processes. Each message is still
    acknowledged on its own, so one failure doesn't affect the others.
    """
    if concurrency <= 1:
        for message in messages:
            await handle_message(queue, handlers, message)
        return
    slots = asyncio.Semaphore(concurrency)

    async def one(message: Message) -> None:
        async with slots:
            await handle_message(queue, handlers, message)

    results = await asyncio.gather(*(one(m) for m in messages), return_exceptions=True)
    for message, result in zip(messages, results, strict=True):
        if isinstance(result, Exception):  # a Redis error while acking, say: retried later
            logger.error(
                "message_settle_failed",
                stream=queue.stream,
                message_id=message.id,
                error_type=type(result).__name__,
                error=str(result)[:300],
            )


async def run_consumer(
    queue: StreamQueue,
    handlers: Mapping[str, Handler],
    stop: asyncio.Event,
    *,
    reclaim_interval_seconds: float = DEFAULT_RECLAIM_INTERVAL_SECONDS,
    error_pause_seconds: float = ERROR_PAUSE_SECONDS,
    concurrency: int = 1,
) -> None:
    log = logger.bind(stream=queue.stream, group=queue.group, consumer=queue.consumer)
    log.info("consumer_started")
    next_reclaim = 0.0  # reclaim once at startup: picks up work a crashed worker left
    try:
        while not stop.is_set():
            try:
                messages: list[Message] = []
                if time.monotonic() >= next_reclaim:
                    messages.extend(await queue.claim_due())
                    next_reclaim = time.monotonic() + reclaim_interval_seconds
                if not messages:
                    read = await _wait_or_stop(stop, queue.read())
                    if read is None:
                        break
                    messages.extend(read)
                # Finish the batch even if stop is set meanwhile: each handler is
                # short, and an interrupted one would only be retried later.
                await _handle_batch(queue, handlers, messages, concurrency)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the worker alive through Redis blips
                log.error("consumer_error", error_type=type(exc).__name__, error=str(exc)[:300])
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=error_pause_seconds)
    finally:
        log.info("consumer_stopped")
