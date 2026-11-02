"""A local model through Ollama's HTTP API (``/api/chat``).

For self-hosters who can't send data to a hosted model, and for the model
comparison in the eval. API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

import time
from typing import Any, cast

import httpx

from iip.llm.base import LLMError, LLMResponse

_HTTP_OK = 200


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=10.0),
            transport=transport,
        )

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_output_tokens},
        }
        if json_mode:
            body["format"] = "json"
        started = time.perf_counter()
        try:
            response = await self._client.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("ollama request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise LLMError(
                f"ollama connection error: {type(exc).__name__}", retryable=True
            ) from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.status_code != _HTTP_OK:
            raise LLMError(
                f"ollama HTTP {response.status_code}: {response.text[:200]}",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
            )
        data = cast(dict[str, Any], response.json())
        message = cast(dict[str, Any], data.get("message") or {})
        text = str(message.get("content", "")).strip()
        if not text:
            raise LLMError("ollama returned empty text", retryable=False)
        return LLMResponse(
            text=text,
            model=str(data.get("model") or self.model),
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            latency_ms=latency_ms,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
