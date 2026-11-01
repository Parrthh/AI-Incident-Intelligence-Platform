"""DLQ and queue admin commands against a real Redis."""

import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from iip.queue import admin
from iip.queue.jobs import CONSUMER_GROUP
from iip.queue.redis import create_redis
from iip.queue.streams import Message, StreamQueue, publish

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = create_redis(redis_url, socket_timeout=10)
    yield client
    await client.aclose()


@pytest.fixture
async def stream(redis: Redis) -> AsyncIterator[str]:
    name = f"test.jobs.{uuid.uuid4().hex[:10]}"
    yield name
    await redis.delete(name, f"{name}.dlq")


def make_queue(redis: Redis, stream: str, consumer: str = "w") -> StreamQueue:
    return StreamQueue(
        redis=redis, stream=stream, group=CONSUMER_GROUP, consumer=consumer, block_ms=100
    )


async def dead_letter_one(redis: Redis, stream: str, payload: dict[str, str]) -> Message:
    queue = make_queue(redis, stream)
    await publish(redis, stream, "summarize", payload)
    [message] = await queue.read()
    await queue.dead_letter(message, "PermanentError: bad api key")
    return message


async def test_list_shows_newest_first_with_error(redis: Redis, stream: str) -> None:
    await dead_letter_one(redis, stream, {"incident_id": "a"})
    await dead_letter_one(redis, stream, {"incident_id": "b"})

    letters = await admin.dlq_list(redis, stream)

    assert [d.payload for d in letters] == ['{"incident_id":"b"}', '{"incident_id":"a"}']
    assert letters[0].error == "PermanentError: bad api key"
    assert letters[0].type == "summarize"


async def test_replay_puts_the_job_back_and_removes_the_dead_letter(
    redis: Redis, stream: str
) -> None:
    await dead_letter_one(redis, stream, {"incident_id": "a"})
    [letter] = await admin.dlq_list(redis, stream)

    assert await admin.dlq_replay(redis, stream, [letter.id]) == 1

    assert await admin.dlq_list(redis, stream) == []
    [job] = await make_queue(redis, stream).read()
    assert job.type == "summarize"
    assert job.payload == {"incident_id": "a"}


async def test_replay_all_and_unknown_id(redis: Redis, stream: str) -> None:
    for name in ("a", "b", "c"):
        await dead_letter_one(redis, stream, {"incident_id": name})

    with pytest.raises(KeyError):
        await admin.dlq_replay(redis, stream, ["0-1"])
    assert await admin.dlq_replay(redis, stream) == 3
    assert len(await make_queue(redis, stream).read()) == 3


async def test_purge(redis: Redis, stream: str) -> None:
    await dead_letter_one(redis, stream, {"incident_id": "a"})

    assert await admin.dlq_purge(redis, stream) == 1
    assert await admin.dlq_list(redis, stream) == []


async def test_stats(redis: Redis, stream: str) -> None:
    empty = await admin.queue_stats(redis, stream)
    assert (empty.stream_length, empty.pending, empty.dead_letters) == (0, 0, 0)

    await dead_letter_one(redis, stream, {"incident_id": "a"})
    await publish(redis, stream, "summarize", {"incident_id": "b"})
    await make_queue(redis, stream).read()  # read, not acked

    stats = await admin.queue_stats(redis, stream)
    assert stats.pending == 1
    assert stats.dead_letters == 1
    assert [c["name"] for c in stats.consumers] == ["w"]


async def test_prune_only_removes_idle_consumers_without_jobs(redis: Redis, stream: str) -> None:
    await publish(redis, stream, "summarize", {"incident_id": "a"})
    await make_queue(redis, stream, "busy").read()  # holds one pending job
    await make_queue(redis, stream, "empty").read()  # holds nothing

    removed = await admin.prune_consumers(redis, stream, idle_ms=0)

    assert removed == ["empty"]
    stats = await admin.queue_stats(redis, stream)
    assert [c["name"] for c in stats.consumers] == ["busy"]
    assert stats.pending == 1
