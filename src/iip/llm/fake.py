"""Deterministic provider for tests and for running without an API key."""

import asyncio
from collections.abc import Sequence

from iip.llm.base import LLMError, LLMResponse


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        *,
        model: str = "fake-model",
        reply: str = "Fake summary of the incident.",
        failures: Sequence[LLMError] = (),
        delay_seconds: float = 0.0,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.model = model
        self.reply = reply
        self._failures = list(failures)  # raised in order before succeeding
        self.calls: list[str] = []

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(prompt)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._failures:
            raise self._failures.pop(0)
        return LLMResponse(
            text=self.reply,
            model=self.model,
            input_tokens=len(prompt) // 4,
            output_tokens=len(self.reply) // 4,
            latency_ms=1,
        )

    async def aclose(self) -> None:
        return None
