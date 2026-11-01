"""The whole background path on real Postgres and Redis:
webhook -> outbox -> relay -> stream -> worker -> summary.

Includes the failure cases M2 exists for: Redis down at ingest time, a process
dying between commit and publish, a worker dying mid-job, duplicate jobs, and
jobs that fail temporarily or for good.
"""

import uuid
from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from iip.api.app import create_app
from iip.config import Environment, Settings
from iip.db.engine import create_engine
from iip.db.models import Analysis, OutboxMessage
from iip.llm.base import LLMError
from iip.llm.fake import FakeProvider
from iip.queue.jobs import CONSUMER_GROUP, JOB_SUMMARIZE
from iip.queue.outbox import OutboxRelay, enqueue
from iip.queue.redis import create_redis
from iip.queue.streams import Message, StreamQueue, publish
from iip.worker.consumer import handle_message
from iip.worker.jobs import build_handlers
from tests.fixtures.loader import load_alertmanager
from tests.integration.helpers import analyses, create_incident, sessionmaker_for, wait_for

pytestmark = pytest.mark.integration

URL = "/v1/webhooks/alertmanager"
TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DEAD_REDIS = "redis://127.0.0.1:1/0"


@pytest.fixture
async def db(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


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


def make_settings(database_url: str, redis_url: str, stream: str, **overrides: object) -> Settings:
    return Settings(
        env=Environment.TEST,
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        webhook_token=SecretStr(TOKEN),
        llm_provider="fake",
        llm_max_retries=0,
        jobs_stream=stream,
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **overrides,
    )


def make_queue(redis: Redis, stream: str, consumer: str) -> StreamQueue:
    return StreamQueue(
        redis=redis,
        stream=stream,
        group=CONSUMER_GROUP,
        consumer=consumer,
        block_ms=100,
        base_backoff_s=0.05,
        max_backoff_s=0.2,
    )


async def outbox_rows(db: AsyncEngine) -> list[OutboxMessage]:
    async with sessionmaker_for(db)() as session:
        return list((await session.scalars(select(OutboxMessage))).all())


async def stream_length(redis: Redis, stream: str) -> int:
    return cast(int, await redis.xlen(stream))  # pyright: ignore[reportUnknownMemberType]


async def done_summaries(db: AsyncEngine) -> int:
    async with sessionmaker_for(db)() as session:
        count = await session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.status == "done")
        )
    return count or 0


async def test_webhook_to_summary_end_to_end(
    migrated_database_url: str, redis_url: str, db: AsyncEngine, redis: Redis, stream: str
) -> None:
    settings = make_settings(migrated_database_url, redis_url, stream, run_worker_in_api=True)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)
        assert response.status_code == 202
        await wait_for(lambda: done_summaries(db))
        llm: FakeProvider = app.state.llm

    [row] = await analyses(db)
    assert str(row.incident_id) == response.json()["new_incident_ids"][0]
    assert len(llm.calls) == 1
    [job] = await outbox_rows(db)
    assert job.published_at is not None
    queue = make_queue(redis, stream, "check")
    assert await queue.pending_count() == 0  # acknowledged


async def test_job_survives_redis_outage_at_ingest(
    migrated_database_url: str, db: AsyncEngine, redis: Redis, stream: str
) -> None:
    settings = make_settings(migrated_database_url, DEAD_REDIS, stream)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(URL, json=load_alertmanager("high_error_rate_firing"), headers=AUTH)
        # Ingestion doesn't depend on Redis at all.
        assert response.status_code == 202

        async def failed_attempt() -> bool:
            rows = await outbox_rows(db)
            return bool(rows) and rows[0].attempts >= 1

        await wait_for(failed_attempt)

    [job] = await outbox_rows(db)
    assert job.published_at is None
    assert job.last_error is not None

    # Redis is back: the relay publishes the job that was kept in Postgres.
    relay = OutboxRelay(sessionmaker_for(db), redis)
    assert await relay.drain() == 1
    assert await stream_length(redis, stream) == 1
    [job] = await outbox_rows(db)
    assert job.published_at is not None


async def test_relay_publishes_jobs_left_by_a_dead_process_at_startup(
    migrated_database_url: str, redis_url: str, db: AsyncEngine, redis: Redis, stream: str
) -> None:
    # A process committed the job and died before publishing it.
    async with sessionmaker_for(db)() as session:
        enqueue(session, stream, JOB_SUMMARIZE, {"incident_id": str(uuid.uuid4())})
        await session.commit()

    with TestClient(create_app(make_settings(migrated_database_url, redis_url, stream))):

        async def published() -> bool:
            return await stream_length(redis, stream) == 1

        await wait_for(published)


async def test_relay_does_not_publish_twice(db: AsyncEngine, redis: Redis, stream: str) -> None:
    async with sessionmaker_for(db)() as session:
        enqueue(session, stream, JOB_SUMMARIZE, {"incident_id": str(uuid.uuid4())})
        await session.commit()
    relay = OutboxRelay(sessionmaker_for(db), redis)

    assert await relay.drain() == 1
    assert await relay.drain() == 0
    assert await stream_length(redis, stream) == 1


async def test_worker_dying_mid_job_is_recovered(
    db: AsyncEngine, redis: Redis, stream: str
) -> None:
    incident_id = await create_incident(db)
    await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": str(incident_id)})
    sm = sessionmaker_for(db)

    # Worker A takes the job, claims the analysis row and dies during the LLM call
    # (the hung provider is never awaited to completion).
    crashed = make_queue(redis, stream, "crashed")
    [message] = await crashed.read()
    async with sm() as session:
        session.add(
            Analysis(
                incident_id=incident_id,
                kind="summary",
                status="pending",
                provider="fake",
                model="fake-model",
                prompt_version="summary_v1",
                attempts=0,
                claimed_by=message.id,
                claimed_at=await session.scalar(select(func.now())),
            )
        )
        await session.commit()

    # Worker B's sweep finds the abandoned job and finishes it.
    survivor = make_queue(redis, stream, "survivor")
    llm = FakeProvider()
    handlers = build_handlers(sm, llm, llm_max_retries=0)

    async def reclaimed() -> list[Message]:
        return await survivor.claim_due()

    [retry] = await wait_for(reclaimed)
    assert retry.id == message.id
    assert await handle_message(survivor, handlers, retry) == "done"

    [row] = await analyses(db)
    assert row.status == "done"
    assert len(llm.calls) == 1
    assert await survivor.pending_count() == 0


async def test_duplicate_jobs_summarize_once(db: AsyncEngine, redis: Redis, stream: str) -> None:
    incident_id = await create_incident(db)
    for _ in range(3):
        await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": str(incident_id)})
    queue = make_queue(redis, stream, "w")
    llm = FakeProvider()
    handlers = build_handlers(sessionmaker_for(db), llm, llm_max_retries=0)

    outcomes = [await handle_message(queue, handlers, m) for m in await queue.read()]

    assert outcomes == ["done", "done", "done"]  # all acknowledged
    assert len(llm.calls) == 1
    assert len(await analyses(db)) == 1


async def test_temporary_llm_failure_is_retried_through_the_queue(
    db: AsyncEngine, redis: Redis, stream: str
) -> None:
    incident_id = await create_incident(db)
    await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": str(incident_id)})
    queue = make_queue(redis, stream, "w")
    llm = FakeProvider(failures=[LLMError("rate limited", retryable=True)])
    handlers = build_handlers(sessionmaker_for(db), llm, llm_max_retries=0)

    [message] = await queue.read()
    assert await handle_message(queue, handlers, message) == "retry"
    [row] = await analyses(db)
    assert row.status == "failed"  # visible while waiting for the retry

    async def due() -> list[Message]:
        return await queue.claim_due()

    [retry] = await wait_for(due)
    assert await handle_message(queue, handlers, retry) == "done"
    [row] = await analyses(db)
    assert row.status == "done"
    assert row.attempts == 2


async def test_permanent_llm_failure_is_dead_lettered(
    db: AsyncEngine, redis: Redis, stream: str
) -> None:
    incident_id = await create_incident(db)
    await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": str(incident_id)})
    queue = make_queue(redis, stream, "w")
    llm = FakeProvider(failures=[LLMError("bad api key", retryable=False)])

    [message] = await queue.read()
    outcome = await handle_message(
        queue, build_handlers(sessionmaker_for(db), llm, llm_max_retries=0), message
    )

    assert outcome == "dead_lettered"
    assert await stream_length(redis, f"{stream}.dlq") == 1
    [row] = await analyses(db)
    assert row.status == "failed"


async def test_bad_payload_is_dead_lettered(db: AsyncEngine, redis: Redis, stream: str) -> None:
    await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": "not-a-uuid"})
    queue = make_queue(redis, stream, "w")
    [message] = await queue.read()

    handlers = build_handlers(sessionmaker_for(db), FakeProvider(), llm_max_retries=0)
    assert await handle_message(queue, handlers, message) == "dead_lettered"


async def test_job_for_deleted_incident_is_acknowledged(
    db: AsyncEngine, redis: Redis, stream: str
) -> None:
    await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": str(uuid.uuid4())})
    queue = make_queue(redis, stream, "w")
    [message] = await queue.read()

    handlers = build_handlers(sessionmaker_for(db), FakeProvider(), llm_max_retries=0)
    assert await handle_message(queue, handlers, message) == "done"
    assert await analyses(db) == []


async def test_three_failures_then_success_gives_one_summary_after_four_attempts(
    db: AsyncEngine, redis: Redis, stream: str
) -> None:
    incident_id = await create_incident(db)
    await publish(redis, stream, JOB_SUMMARIZE, {"incident_id": str(incident_id)})
    queue = make_queue(redis, stream, "w")
    outage = [LLMError("503 from provider", retryable=True)] * 3
    llm = FakeProvider(failures=outage)
    handlers = build_handlers(sessionmaker_for(db), llm, llm_max_retries=0)

    [message] = await queue.read()
    outcomes = [await handle_message(queue, handlers, message)]

    async def due() -> list[Message]:
        return await queue.claim_due()

    while outcomes[-1] == "retry":
        [retry] = await wait_for(due)
        outcomes.append(await handle_message(queue, handlers, retry))

    assert outcomes == ["retry", "retry", "retry", "done"]
    [row] = await analyses(db)
    assert row.status == "done"
    assert row.attempts == 4
    assert len(llm.calls) == 4
    assert await queue.pending_count() == 0
