"""Worker loop.

In M0 the worker does no real work yet: it only logs a heartbeat so the
deployment shape (separate process for self-hosters, in-process task on the free
demo) is in place before the queue consumers arrive in M2.

The loop must stop promptly when asked. ``stop`` is an ``asyncio.Event`` set on
shutdown (SIGTERM from Docker or Render); the loop waits on it instead of
sleeping, so shutdown never has to wait out a full heartbeat interval.

Free-tier note (ADR-0004): Upstash's free plan allows 500K Redis commands a
month. This loop deliberately sends no Redis commands, and M2's consumers must
use long blocking reads rather than tight polling.
"""

import asyncio
import signal

import structlog

logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 60.0


async def run_worker(stop: asyncio.Event, *, heartbeat_seconds: float | None = None) -> None:
    interval = heartbeat_seconds if heartbeat_seconds is not None else HEARTBEAT_INTERVAL_SECONDS
    logger.info("worker_started")
    beats = 0
    try:
        while not stop.is_set():
            beats += 1
            logger.info("worker_heartbeat", beats=beats)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
    finally:
        logger.info("worker_stopped", beats=beats)


async def run_worker_until_signalled() -> None:
    """Entrypoint for the standalone ``iip worker`` process."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_worker(stop)
