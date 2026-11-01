"""Deterministic provider for tests and for running without an API key.

In JSON mode (root-cause analysis) it builds a small, valid analysis from the
prompt itself: the first listed service as the cause, citing the first two
evidence IDs it sees. That keeps the whole pipeline runnable offline. Tests can
pass ``json_reply`` to return exactly what they need, including bad output.
"""

import asyncio
import json
import re
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
        json_reply: str | None = None,
    ) -> None:
        self.json_reply = json_reply
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
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append(prompt)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._failures:
            raise self._failures.pop(0)
        text = self.reply
        if json_mode:
            text = self.json_reply if self.json_reply is not None else _fake_analysis(prompt)
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
            latency_ms=1,
        )

    async def aclose(self) -> None:
        return None


def _fake_analysis(prompt: str) -> str:
    services = re.search(r"^Services: (.+)$", prompt, re.MULTILINE)
    service = services.group(1).split(",")[0].strip() if services else "unknown"
    cited = list(dict.fromkeys(re.findall(r"\[(ev_[a-z]+_\d+)\]", prompt)))[:2]
    return json.dumps(
        {
            "summary": f"Fake analysis: {service} looks like the cause.",
            "timeline": [],
            "hypotheses": [
                {
                    "rank": 1,
                    "root_cause_service": service,
                    "category": "unknown",
                    "explanation": "Fake provider: picks the first affected service.",
                    "confidence": 0.5,
                    "evidence_ids": cited,
                    "next_steps": ["Check the service's recent changes."],
                }
            ],
            "missing_information": [],
        }
    )
