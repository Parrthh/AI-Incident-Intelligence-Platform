import json
from typing import Any

import httpx
import pytest

from iip.llm.base import LLMError
from iip.llm.gemini import GeminiProvider


def provider(handler: Any) -> GeminiProvider:
    return GeminiProvider(
        api_key="test-key",
        model="gemini-test",
        base_url="https://gemini.test/v1beta",
        transport=httpx.MockTransport(handler),
    )


def ok_body(text: str = "Checkout is failing.") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30},
        "modelVersion": "gemini-test-001",
    }


async def test_sends_key_in_header_and_parses_answer() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    p = provider(handler)
    response = await p.generate("hello", system="be brief", max_output_tokens=100)
    await p.aclose()

    assert seen["url"] == "https://gemini.test/v1beta/models/gemini-test:generateContent"
    assert seen["key"] == "test-key"
    assert "test-key" not in seen["url"]  # never in the URL
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 100
    assert response.text == "Checkout is failing."
    assert response.model == "gemini-test-001"
    assert (response.input_tokens, response.output_tokens) == (120, 30)


async def test_ignores_thought_parts() -> None:
    body = ok_body()
    body["candidates"][0]["content"]["parts"] = [
        {"text": "let me think...", "thought": True},
        {"text": "Final answer."},
    ]
    p = provider(lambda _: httpx.Response(200, json=body))

    response = await p.generate("hi")

    assert response.text == "Final answer."


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (500, True), (503, True), (400, False), (401, False), (403, False), (404, False)],
)
async def test_classifies_http_errors(status: int, retryable: bool) -> None:
    body = {"error": {"code": status, "message": "nope", "status": "SOME_STATUS"}}
    p = provider(lambda _: httpx.Response(status, json=body))

    with pytest.raises(LLMError) as exc_info:
        await p.generate("hi")

    assert exc_info.value.retryable is retryable
    assert exc_info.value.status_code == status


async def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError) as exc_info:
        await provider(handler).generate("hi")

    assert exc_info.value.retryable is True


async def test_blocked_prompt_is_permanent() -> None:
    body = {"promptFeedback": {"blockReason": "SAFETY"}}

    with pytest.raises(LLMError, match="SAFETY") as exc_info:
        await provider(lambda _: httpx.Response(200, json=body)).generate("hi")

    assert exc_info.value.retryable is False


async def test_empty_text_is_an_error() -> None:
    body = ok_body()
    body["candidates"][0]["content"]["parts"] = []
    body["candidates"][0]["finishReason"] = "MAX_TOKENS"

    with pytest.raises(LLMError, match="MAX_TOKENS"):
        await provider(lambda _: httpx.Response(200, json=body)).generate("hi")


async def test_list_models_keeps_only_generation_models() -> None:
    body = {
        "models": [
            {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed-b", "supportedGenerationMethods": ["embedContent"]},
        ]
    }

    assert await provider(lambda _: httpx.Response(200, json=body)).list_models() == ["gemini-a"]
