"""Shared helpers for integration tests."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from iip.db.models import Analysis, OutboxMessage
from iip.ingest.alertmanager import AlertmanagerPayload
from iip.ingest.service import ingest_alertmanager
from iip.llm.base import LLMResponse
from iip.llm.fake import FakeProvider
from iip.llm.summarize import summarize_incident
from iip.queue.jobs import JOB_SUMMARIZE
from tests.fixtures.loader import load_alertmanager


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_incident(
    engine: AsyncEngine, fixture: str = "high_error_rate_firing"
) -> uuid.UUID:
    payload = AlertmanagerPayload.model_validate(load_alertmanager(fixture))
    async with sessionmaker_for(engine)() as session:
        result = await ingest_alertmanager(session, payload)
        await session.commit()
    return result.new_incident_ids[0]


async def analyses(engine: AsyncEngine) -> list[Analysis]:
    async with sessionmaker_for(engine)() as session:
        return list((await session.scalars(select(Analysis))).all())


async def wait_for[T](
    probe: Callable[[], Awaitable[T]], *, within: float = 10.0, interval: float = 0.05
) -> T:
    """Poll ``probe`` until it returns something truthy."""
    deadline = asyncio.get_running_loop().time() + within
    while True:
        value = await probe()
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"condition not met within {within}s (last value {value!r})")
        await asyncio.sleep(interval)


class SlowProvider(FakeProvider):
    """A fake provider whose calls take a while, to make concurrent runs overlap."""

    def __init__(self, delay: float, **kwargs: object) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]
        self.delay = delay

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        await asyncio.sleep(self.delay)
        return await super().generate(
            prompt, system=system, max_output_tokens=max_output_tokens, temperature=temperature
        )


def run_summary_jobs(client: TestClient) -> int:
    """Do the worker's job inline: summarize every incident with an unpublished job.

    For tests about pages and APIs that only need summaries to exist, without
    running Redis and the worker. Returns how many jobs ran.
    """
    app = cast(FastAPI, client.app)
    portal = client.portal
    assert portal is not None, "use inside `with TestClient(app)`"

    async def work() -> int:
        sm: async_sessionmaker[AsyncSession] = app.state.sessionmaker
        async with sm() as session:
            rows = list(
                (
                    await session.scalars(
                        select(OutboxMessage).where(
                            OutboxMessage.type == JOB_SUMMARIZE,
                            OutboxMessage.published_at.is_(None),
                        )
                    )
                ).all()
            )
            for row in rows:
                incident_id = uuid.UUID(row.payload["incident_id"])
                await summarize_incident(sm, app.state.llm, incident_id, max_retries=0)
                row.published_at = datetime.now(UTC)
            await session.commit()
        return len(rows)

    return portal.call(work)
