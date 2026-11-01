"""Stream queue behaviour against a real Redis.

Each test uses its own stream name, so tests can't see each other's messages,
and backoffs are shrunk to milliseconds so retries happen quickly.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from iip.queue.redis import create_redis
from iip.queue.streams import Message, PermanentError, StreamQueue, publish
from iip.worker.consumer import handle_message, run_consumer


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = create_redis(redis_url, socket_timeout=10)
    yield client
    await client.aclose()


@pytest.fixture
def stream() -> str:
    return f"test.jobs.{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def cleanup(redis: Redis, stream: str) -> AsyncIterator[None]:
    yield
    await redis.delete(stream, f"{stream}.dlq")


def make_queue(redis: Redis, stream: str, consumer: str = "c1", **overrides: float) -> StreamQueue:
    options: dict[str, float] = {
        "block_ms": 100,
        "base_backoff_s": 0.05,
        "max_backoff_s": 0.2,
        **overrides,
    }
    return StreamQueue(
        redis=redis,
        stream=stream,
        group="workers",
        consumer=consumer,
        block_ms=int(options["block_ms"]),
        base_backoff_s=options["base_backoff_s"],
        max_backoff_s=options["max_backoff_s"],
    )


async def dlq_entries(redis: Redis, stream: str) -> list[dict[str, str]]:
    entries = await redis.xrange(f"{stream}.dlq")  # pyright: ignore[reportUnknownMemberType]
    return [fields for _id, fields in entries]  # pyright: ignore[reportUnknownVariableType]


async def test_published_message_is_read_and_acknowledged(
    redis: Redis, stream: str, cleanup: None
) -> None:
    queue = make_queue(redis, stream)
    await queue.ensure_group()
    await publish(redis, stream, "summarize", {"incident_id": "abc"})

    [message] = await queue.read()
    assert message.type == "summarize"
    assert message.payload == {"incident_id": "abc"}
    assert message.deliveries == 1
    assert await queue.pending_count() == 1

    await queue.ack(message)
    assert await queue.pending_count() == 0
    assert await queue.read() == []  # nothing new


async def test_messages_published_before_group_exists_are_not_skipped(
    redis: Redis, stream: str, cleanup: None
) -> None:
    await publish(redis, stream, "summarize", {"n": 1})
    queue = make_queue(redis, stream)

    [message] = await queue.read()
    assert message.payload == {"n": 1}


async def test_ensure_group_is_idempotent(redis: Redis, stream: str, cleanup: None) -> None:
    await make_queue(redis, stream).ensure_group()
    await make_queue(redis, stream).ensure_group()  # BUSYGROUP is swallowed


async def test_failed_message_is_retried_after_backoff(
    redis: Redis, stream: str, cleanup: None
) -> None:
    queue = make_queue(redis, stream)
    await publish(redis, stream, "summarize", {"n": 1})
    [first] = await queue.read()
    # Handler failed: message is not acknowledged.

    assert await queue.claim_due() == []  # backoff not elapsed yet
    await asyncio.sleep(0.08)
    [retry] = await queue.claim_due()
    assert retry.id == first.id
    assert retry.deliveries == 2
    assert retry.payload == {"n": 1}


async def test_crashed_consumers_work_is_claimed_by_another(
    redis: Redis, stream: str, cleanup: None
) -> None:
    crashed = make_queue(redis, stream, consumer="crashed")
    survivor = make_queue(redis, stream, consumer="survivor")
    await publish(redis, stream, "summarize", {"n": 1})
    [taken] = await crashed.read()  # then the process dies without acking

    await asyncio.sleep(0.08)
    [reclaimed] = await survivor.claim_due()
    assert reclaimed.id == taken.id
    await survivor.ack(reclaimed)
    assert await survivor.pending_count() == 0


async def test_two_sweepers_never_claim_the_same_message(
    redis: Redis, stream: str, cleanup: None
) -> None:
    a = make_queue(redis, stream, consumer="a")
    b = make_queue(redis, stream, consumer="b")
    await publish(redis, stream, "summarize", {"n": 1})
    await a.read()
    await asyncio.sleep(0.08)

    claims_a, claims_b = await asyncio.gather(a.claim_due(), b.claim_due())
    assert len(claims_a) + len(claims_b) == 1


async def test_message_is_dead_lettered_after_max_deliveries(
    redis: Redis, stream: str, cleanup: None
) -> None:
    queue = make_queue(redis, stream)
    calls = 0

    async def always_fails(_message: Message) -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("provider down")

    message_id = await publish(redis, stream, "summarize", {"n": 1})
    [message] = await queue.read()
    assert await handle_message(queue, {"summarize": always_fails}, message) == "retry"
    for _ in range(20):
        await asyncio.sleep(0.25)  # longer than max_backoff
        for retry in await queue.claim_due():
            await handle_message(queue, {"summarize": always_fails}, retry)
        if await queue.pending_count() == 0:
            break

    assert calls == queue.max_deliveries
    [dead] = await dlq_entries(redis, stream)
    assert dead["original_id"] == message_id
    assert dead["deliveries"] == str(queue.max_deliveries)
    assert dead["type"] == "summarize"
    assert dead["payload"] == '{"n":1}'
    assert "gave up" in dead["error"]


async def test_permanent_error_dead_letters_immediately(
    redis: Redis, stream: str, cleanup: None
) -> None:
    queue = make_queue(redis, stream)

    async def bad_payload(_message: Message) -> None:
        raise PermanentError("incident does not exist")

    await publish(redis, stream, "summarize", {"n": 1})
    [message] = await queue.read()
    assert await handle_message(queue, {"summarize": bad_payload}, message) == "dead_lettered"

    assert await queue.pending_count() == 0
    [dead] = await dlq_entries(redis, stream)
    assert dead["deliveries"] == "1"
    assert "incident does not exist" in dead["error"]


async def test_unknown_message_type_is_dead_lettered(
    redis: Redis, stream: str, cleanup: None
) -> None:
    queue = make_queue(redis, stream)
    await publish(redis, stream, "mystery", {})
    [message] = await queue.read()

    assert await handle_message(queue, {}, message) == "dead_lettered"
    [dead] = await dlq_entries(redis, stream)
    assert "no handler" in dead["error"]


async def test_consumer_loop_processes_messages_and_stops_promptly(
    redis: Redis, stream: str, cleanup: None
) -> None:
    # A long block: stopping must not wait it out.
    queue = make_queue(redis, stream, block_ms=5000)
    seen: list[int] = []
    done = asyncio.Event()

    async def record(message: Message) -> None:
        seen.append(int(message.payload["n"]))
        if len(seen) == 3:
            done.set()

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(queue, {"summarize": record}, stop, reclaim_interval_seconds=0.05)
    )
    for n in range(3):
        await publish(redis, stream, "summarize", {"n": n})
    await asyncio.wait_for(done.wait(), timeout=5)
    assert sorted(seen) == [0, 1, 2]

    await asyncio.sleep(0.1)  # now parked in a 5 s blocking read
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert await queue.pending_count() == 0


async def test_consumer_loop_retries_until_handler_succeeds(
    redis: Redis, stream: str, cleanup: None
) -> None:
    queue = make_queue(redis, stream)
    attempts: list[int] = []
    done = asyncio.Event()

    async def flaky(message: Message) -> None:
        attempts.append(message.deliveries)
        if len(attempts) < 3:
            raise TimeoutError("slow provider")
        done.set()

    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(queue, {"summarize": flaky}, stop, reclaim_interval_seconds=0.02)
    )
    await publish(redis, stream, "summarize", {"n": 1})
    await asyncio.wait_for(done.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert attempts == [1, 2, 3]
    assert await queue.pending_count() == 0
    assert await dlq_entries(redis, stream) == []
