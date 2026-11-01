"""Operator tools for the job queue: inspect, replay and purge dead letters.

Used by the ``iip dlq`` and ``iip queue`` commands. A dead letter is a job that
failed for good (a permanent error, or too many attempts). It keeps the original
type and payload, so replaying it is just publishing it again.
"""

from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from iip.queue.jobs import CONSUMER_GROUP
from iip.queue.streams import DEFAULT_MAXLEN


@dataclass(frozen=True)
class DeadLetter:
    id: str
    type: str
    payload: str
    error: str
    deliveries: str
    original_id: str
    dead_lettered_at: str


@dataclass(frozen=True)
class QueueStats:
    stream_length: int
    pending: int
    dead_letters: int
    consumers: list[dict[str, Any]]


def dlq_name(stream: str) -> str:
    return f"{stream}.dlq"


async def dlq_list(redis: Redis, stream: str, limit: int = 20) -> list[DeadLetter]:
    """Newest dead letters first."""
    entries = cast(
        list[tuple[str, dict[str, str]]],
        await redis.xrevrange(dlq_name(stream), count=limit),  # pyright: ignore[reportUnknownMemberType]
    )
    return [_dead_letter(entry_id, fields) for entry_id, fields in entries]


async def dlq_replay(redis: Redis, stream: str, ids: list[str] | None = None) -> int:
    """Publish dead letters back onto the stream, then remove them from the DLQ.

    ``ids=None`` replays all of them. Returns how many were replayed. Publish
    happens before delete, so a crash in between duplicates a job rather than
    losing one; handlers are idempotent.
    """
    dlq = dlq_name(stream)
    entries: list[tuple[str, dict[str, str]]]
    if ids is None:
        entries = cast(
            list[tuple[str, dict[str, str]]],
            await redis.xrange(dlq),  # pyright: ignore[reportUnknownMemberType]
        )
    else:
        entries = []
        for entry_id in ids:
            found = cast(
                list[tuple[str, dict[str, str]]],
                await redis.xrange(dlq, min=entry_id, max=entry_id),  # pyright: ignore[reportUnknownMemberType]
            )
            if not found:
                raise KeyError(entry_id)
            entries.extend(found)
    replayed = 0
    for entry_id, fields in entries:
        job = {k: fields[k] for k in ("type", "payload", "created_at") if k in fields}
        await redis.xadd(stream, job, maxlen=DEFAULT_MAXLEN, approximate=True)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
        await redis.xdel(dlq, entry_id)  # pyright: ignore[reportUnknownMemberType]
        replayed += 1
    return replayed


async def dlq_purge(redis: Redis, stream: str) -> int:
    """Delete every dead letter. Returns how many there were."""
    dlq = dlq_name(stream)
    count: int = await redis.xlen(dlq)  # pyright: ignore[reportUnknownMemberType]
    await redis.delete(dlq)
    return count


async def queue_stats(redis: Redis, stream: str) -> QueueStats:
    length: int = await redis.xlen(stream)  # pyright: ignore[reportUnknownMemberType]
    dead: int = await redis.xlen(dlq_name(stream))  # pyright: ignore[reportUnknownMemberType]
    try:
        summary: dict[str, Any] = await redis.xpending(stream, CONSUMER_GROUP)  # pyright: ignore[reportUnknownMemberType]
        consumers: list[dict[str, Any]] = await redis.xinfo_consumers(stream, CONSUMER_GROUP)  # pyright: ignore[reportUnknownMemberType]
    except ResponseError:  # no stream or group yet: nothing has been consumed
        summary, consumers = {"pending": 0}, []
    return QueueStats(
        stream_length=length,
        pending=int(summary.get("pending", 0)),
        dead_letters=dead,
        consumers=consumers,
    )


async def prune_consumers(redis: Redis, stream: str, idle_ms: int = 3_600_000) -> list[str]:
    """Remove consumers that hold no pending jobs and have been idle for ``idle_ms``.

    Each worker process registers its own consumer name (host and PID), so
    restarts and deploys leave old names behind. Only empty ones are removed:
    deleting a consumer that still holds jobs would drop those jobs.
    """
    try:
        consumers: list[dict[str, Any]] = await redis.xinfo_consumers(stream, CONSUMER_GROUP)  # pyright: ignore[reportUnknownMemberType]
    except ResponseError:
        return []
    removed: list[str] = []
    for consumer in consumers:
        if int(consumer.get("pending", 0)) == 0 and int(consumer.get("idle", 0)) >= idle_ms:
            name = str(consumer["name"])
            await redis.xgroup_delconsumer(stream, CONSUMER_GROUP, name)  # pyright: ignore[reportUnknownMemberType]
            removed.append(name)
    return removed


def _dead_letter(entry_id: str, fields: dict[str, str]) -> DeadLetter:
    return DeadLetter(
        id=entry_id,
        type=fields.get("type", ""),
        payload=fields.get("payload", ""),
        error=fields.get("error", ""),
        deliveries=fields.get("deliveries", ""),
        original_id=fields.get("original_id", ""),
        dead_lettered_at=fields.get("dead_lettered_at", ""),
    )
