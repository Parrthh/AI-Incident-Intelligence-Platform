"""Google Gemini over its REST API.

Plain HTTP with httpx instead of the vendor SDK: one small, well-understood
dependency, explicit timeouts, and full control over error handling.

API reference: https://ai.google.dev/api/generate-content
"""

import time
from typing import Any

import httpx

from iip.llm.base import LLMError, LLMResponse

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url,
            # The key goes in a header, not the URL, so it never lands in access
            # logs or error messages that include the URL.
            headers={"x-goog-api-key": api_key},
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
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        started = time.perf_counter()
        try:
            response = await self._client.post(f"/models/{self.model}:generateContent", json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("gemini request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise LLMError(
                f"gemini connection error: {type(exc).__name__}", retryable=True
            ) from exc
        latency_ms = round((time.perf_counter() - started) * 1000)

        if response.status_code != httpx.codes.OK:
            raise _error_from_response(response)

        return _parse(response.json(), fallback_model=self.model, latency_ms=latency_ms)

    async def list_models(self) -> list[str]:
        response = await self._client.get("/models", params={"pageSize": 200})
        if response.status_code != httpx.codes.OK:
            raise _error_from_response(response)
        models: list[dict[str, Any]] = response.json().get("models", [])
        return [
            str(m.get("name", "")).removeprefix("models/")
            for m in models
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_from_response(response: httpx.Response) -> LLMError:
    status = response.status_code
    # Google's error body: {"error": {"code": 429, "message": "...", "status": "..."}}
    try:
        error = response.json().get("error", {})
        detail = f"{error.get('status', '')}: {error.get('message', '')}".strip(": ")
    except ValueError:
        detail = response.text[:200]
    retryable = status == httpx.codes.TOO_MANY_REQUESTS or status >= 500
    return LLMError(
        f"gemini HTTP {status}: {detail[:300]}", retryable=retryable, status_code=status
    )


def _parse(data: dict[str, Any], *, fallback_model: str, latency_ms: int) -> LLMResponse:
    candidates: list[dict[str, Any]] = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        raise LLMError(f"gemini returned no answer ({reason})", retryable=False)

    candidate = candidates[0]
    parts: list[dict[str, Any]] = (candidate.get("content") or {}).get("parts") or []
    # Thinking models can return their reasoning as parts marked "thought"; only
    # the final answer is wanted.
    text = "".join(str(p.get("text", "")) for p in parts if not p.get("thought")).strip()
    if not text:
        finish = candidate.get("finishReason", "unknown")
        raise LLMError(f"gemini returned empty text (finishReason={finish})", retryable=False)

    usage: dict[str, Any] = data.get("usageMetadata") or {}
    return LLMResponse(
        text=text,
        model=str(data.get("modelVersion") or fallback_model),
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        latency_ms=latency_ms,
    )
