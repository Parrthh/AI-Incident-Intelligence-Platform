"""Shared helpers for integration tests."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from iip.db.models import Analysis
from iip.ingest.alertmanager import AlertmanagerPayload
from iip.ingest.service import ingest_alertmanager
from iip.llm.base import LLMResponse
from iip.llm.fake import FakeProvider
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
