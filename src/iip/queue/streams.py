"""Durable job queue on Redis Streams (ADR-0002).

Vocabulary:
- A **stream** is an append-only log of messages (``XADD``).
- A **consumer group** tracks, per message, which worker received it and whether
  it was acknowledged. Several worker processes in one group share the work;
  each message goes to exactly one of them at a time.
- A message read but not yet acknowledged sits in the group's **pending entries
  list** (PEL), with a delivery count and how long it has been idle.

Delivery is **at least once**. A message is acknowledged (``XACK``) only after
its handler finished, so if a worker crashes mid-job, the message stays pending
and another worker reclaims it. The cost: a handler can run twice for the same
message, so every handler must be idempotent.

Retries reuse the pending list instead of a separate delay queue: a failed
message is simply not acknowledged. ``claim_due`` periodically looks at pending
messages and reclaims the ones that have waited long enough. The wait doubles
with each delivery (exponential backoff). After ``max_deliveries`` the message
moves to a dead-letter stream (``<stream>.dlq``) for a human to inspect.

Redis command budget (the hosted demo's Upstash free tier allows 500K/month):
an idle worker issues one blocking read per ``block_ms`` and one ``XPENDING``
per reclaim interval, about 4 commands a minute with the defaults.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = structlog.get_logger(__name__)

# Streams are trimmed approximately to this many entries, so the log can't grow
# forever. Pending (unacknowledged) entries are referenced by ID; trimming can
# drop their bodies, which claim_due handles by dead-lettering them.
DEFAULT_MAXLEN = 10_000


class PermanentError(Exception):
    """Raised by a handler when retrying can't help (bad payload, missing record).
    The message goes straight to the dead-letter stream."""


@dataclass(frozen=True)
class Message:
    id: str
    type: str
    payload: dict[str, Any]
    created_at: float
    deliveries: int = 1

    @classmethod
    def from_fields(cls, message_id: str, fields: dict[str, str], deliveries: int = 1) -> "Message":
        return cls(
            id=message_id,
            type=fields.get("type", ""),
            payload=json.loads(fields.get("payload", "{}")),
            created_at=float(fields.get("created_at", "0")),
            deliveries=deliveries,
        )


async def publish(
    redis: Redis,
    stream: str,
    message_type: str,
    payload: dict[str, Any],
    *,
    maxlen: int = DEFAULT_MAXLEN,
) -> str:
    """Append a message. Returns its stream ID."""
    fields = {
        "type": message_type,
        "payload": json.dumps(payload, separators=(",", ":")),
        "created_at": f"{time.time():.6f}",
    }
    message_id = await redis.xadd(stream, fields, maxlen=maxlen, approximate=True)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
    return str(message_id)


@dataclass
class StreamQueue:
    redis: Redis
    stream: str
    group: str
    consumer: str
    block_ms: int = 30_000
    batch: int = 10
    max_deliveries: int = 5
    base_backoff_s: float = 10.0
    max_backoff_s: float = 600.0
    _group_ready: bool = field(default=False, init=False)

    @property
    def dlq(self) -> str:
        return f"{self.stream}.dlq"

    def backoff_s(self, deliveries: int) -> float:
        """Wait before the next delivery: 10s, 20s, 40s ... capped."""
        return min(self.max_backoff_s, self.base_backoff_s * 2 ** max(0, deliveries - 1))

    async def ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            # id="0": a brand-new group starts from the beginning of the stream, so
            # messages published before the first worker started aren't skipped.
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)  # pyright: ignore[reportUnknownMemberType]
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):  # group already exists: fine
                raise
        self._group_ready = True

    async def read(self) -> list[Message]:
        """New messages for this consumer, blocking up to ``block_ms`` if there are none."""
        await self.ensure_group()
        response = await self.redis.xreadgroup(  # pyright: ignore[reportUnknownMemberType]
            self.group,
            self.consumer,
            streams={self.stream: ">"},
            count=self.batch,
            block=self.block_ms,
        )
        messages: list[Message] = []
        for _stream, entries in cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]], response or []
        ):
            messages.extend(Message.from_fields(mid, fields) for mid, fields in entries)
        return messages

    async def ack(self, message: Message) -> None:
        await self.redis.xack(self.stream, self.group, message.id)  # pyright: ignore[reportUnknownMemberType]

    async def dead_letter(self, message: Message, reason: str) -> None:
        await self._dead_letter_raw(
            message.id,
            {
                "type": message.type,
                "payload": json.dumps(message.payload, separators=(",", ":")),
                "created_at": f"{message.created_at:.6f}",
            },
            message.deliveries,
            reason,
        )

    async def _dead_letter_raw(
        self, message_id: str, fields: dict[str, str], deliveries: int, reason: str
    ) -> None:
        dlq_fields = {
            **fields,
            "original_id": message_id,
            "deliveries": str(deliveries),
            "error": reason[:2000],
            "dead_lettered_at": f"{time.time():.6f}",
        }
        # Write to the DLQ before acknowledging: if we crash in between, the worst
        # case is a duplicate in the DLQ, never a lost message.
        await self.redis.xadd(self.dlq, dlq_fields, maxlen=DEFAULT_MAXLEN, approximate=True)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
        await self.redis.xack(self.stream, self.group, message_id)  # pyright: ignore[reportUnknownMemberType]
        logger.warning(
            "message_dead_lettered",
            stream=self.stream,
            message_id=message_id,
            deliveries=deliveries,
            reason=reason[:300],
        )

    async def claim_due(self) -> list[Message]:
        """Reclaim pending messages whose backoff has elapsed; dead-letter exhausted ones.

        Covers both failure kinds: a handler that raised (message left pending on
        purpose) and a worker that died mid-job (message left pending by accident).
        """
        await self.ensure_group()
        pending = cast(
            list[dict[str, Any]],
            await self.redis.xpending_range(self.stream, self.group, min="-", max="+", count=100),  # pyright: ignore[reportUnknownMemberType]
        )
        due: list[Message] = []
        for entry in pending:
            message_id = str(entry["message_id"])
            deliveries = int(entry["times_delivered"])
            idle_s = int(entry["time_since_delivered"]) / 1000
            if deliveries >= self.max_deliveries:
                await self._dead_letter_exhausted(message_id, deliveries)
                continue
            wait_s = self.backoff_s(deliveries)
            if idle_s < wait_s:
                continue
            # XCLAIM re-checks the idle time atomically, so two workers sweeping at
            # once can't both claim the same message.
            claimed = cast(
                list[tuple[str, dict[str, str] | None]],
                await self.redis.xclaim(  # pyright: ignore[reportUnknownMemberType]
                    self.stream, self.group, self.consumer, int(wait_s * 1000), [message_id]
                ),
            )
            for claimed_id, fields in claimed:
                if fields is None:  # body trimmed from the stream: nothing to retry
                    await self.redis.xack(self.stream, self.group, claimed_id)  # pyright: ignore[reportUnknownMemberType]
                    continue
                due.append(Message.from_fields(claimed_id, fields, deliveries=deliveries + 1))
        return due

    async def _dead_letter_exhausted(self, message_id: str, deliveries: int) -> None:
        entries = cast(
            list[tuple[str, dict[str, str]]],
            await self.redis.xrange(self.stream, min=message_id, max=message_id),  # pyright: ignore[reportUnknownMemberType]
        )
        fields = entries[0][1] if entries else {"type": "", "payload": "{}", "created_at": "0"}
        await self._dead_letter_raw(
            message_id, fields, deliveries, f"gave up after {deliveries} deliveries"
        )

    async def pending_count(self) -> int:
        await self.ensure_group()
        summary: dict[str, Any] = await self.redis.xpending(self.stream, self.group)  # pyright: ignore[reportUnknownMemberType]
        return int(summary.get("pending", 0))
