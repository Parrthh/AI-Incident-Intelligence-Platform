"""Kill a real worker process mid-run and check that no job is lost or doubled.

This is M2's main promise. 100 incidents are queued; a worker process (the same
`iip worker` command self-hosters run) starts on them and is killed with SIGKILL
partway through, with jobs read but not acknowledged and an LLM call in flight.
A second worker must finish everything: exactly one finished summary per
incident, nothing left pending, nothing dead-lettered.
"""

import asyncio
import os
import signal
import sys
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from iip.db.engine import create_engine
from iip.db.models import Analysis
from iip.ingest.alertmanager import AlertmanagerPayload
from iip.ingest.service import ingest_alertmanager
from iip.queue import admin
from iip.queue.jobs import JOB_SUMMARIZE
from iip.queue.redis import create_redis
from iip.queue.streams import publish
from tests.fixtures.loader import load_alertmanager
from tests.integration.conftest import REPO_ROOT
from tests.integration.helpers import sessionmaker_for, wait_for

pytestmark = pytest.mark.integration

JOBS = 100
FAKE_LLM_DELAY_MS = 100  # ~10 jobs/s, so the kill lands mid-run


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


async def queue_incidents(db: AsyncEngine, redis: Redis, stream: str, count: int) -> None:
    template = load_alertmanager("high_error_rate_firing")
    async with sessionmaker_for(db)() as session:
        for n in range(count):
            template["alerts"][0]["fingerprint"] = f"crash-{n}"
            result = await ingest_alertmanager(
                session, AlertmanagerPayload.model_validate(template)
            )
            await session.commit()
            await publish(
                redis, stream, JOB_SUMMARIZE, {"incident_id": str(result.new_incident_ids[0])}
            )


async def start_worker(
    database_url: str, redis_url: str, stream: str
) -> asyncio.subprocess.Process:
    env = {
        **os.environ,
        "IIP_DATABASE_URL": database_url,
        "IIP_REDIS_URL": redis_url,
        "IIP_JOBS_STREAM": stream,
        "IIP_LLM_PROVIDER": "fake",
        "IIP_LLM_FAKE_DELAY_MS": str(FAKE_LLM_DELAY_MS),
        "IIP_QUEUE_RETRY_BASE_SECONDS": "1",
        "IIP_LOG_JSON": "false",
        "IIP_LOG_LEVEL": "WARNING",
    }
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "iip", "worker", cwd=REPO_ROOT, env=env
    )


async def done_count(db: AsyncEngine) -> int:
    async with sessionmaker_for(db)() as session:
        return (
            await session.scalar(
                select(func.count()).select_from(Analysis).where(Analysis.status == "done")
            )
            or 0
        )


async def test_killed_worker_loses_and_duplicates_nothing(
    migrated_database_url: str, redis_url: str, db: AsyncEngine, redis: Redis, stream: str
) -> None:
    await queue_incidents(db, redis, stream, JOBS)

    first = await start_worker(migrated_database_url, redis_url, stream)

    async def some_done() -> bool:
        return await done_count(db) >= 20

    await wait_for(some_done, within=60)
    first.send_signal(signal.SIGKILL)  # no cleanup, no acks: a real crash
    await first.wait()
    done_at_kill = await done_count(db)
    assert done_at_kill < JOBS, "worker finished before the kill; the test proves nothing"

    second = await start_worker(migrated_database_url, redis_url, stream)
    try:

        async def all_done() -> bool:
            return await done_count(db) == JOBS

        await wait_for(all_done, within=90)
    finally:
        second.send_signal(signal.SIGTERM)
        await asyncio.wait_for(second.wait(), timeout=20)

    assert second.returncode == 0  # graceful shutdown on SIGTERM
    async with sessionmaker_for(db)() as session:
        rows = (await session.scalars(select(Analysis))).all()
    assert len(rows) == JOBS  # one row per incident, no duplicates
    assert {r.status for r in rows} == {"done"}
    assert all(r.claimed_by is None for r in rows)
    stats = await admin.queue_stats(redis, stream)
    assert stats.pending == 0
    assert stats.dead_letters == 0
    print(f"killed after {done_at_kill}/{JOBS} done; recovered all {JOBS}, 0 duplicates")
