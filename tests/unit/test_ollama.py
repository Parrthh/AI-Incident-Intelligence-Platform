import json

import httpx
import pytest

from iip.llm.base import LLMError
from iip.llm.ollama import OllamaProvider


async def test_chat_request_and_response() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:3b",
                "message": {"role": "assistant", "content": '{"summary": "x"}'},
                "prompt_eval_count": 120,
                "eval_count": 8,
            },
        )

    provider = OllamaProvider(model="qwen2.5:3b", transport=httpx.MockTransport(handler))
    response = await provider.generate("hello", system="be brief", json_mode=True)

    assert seen["format"] == "json"
    assert seen["stream"] is False
    assert seen["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    assert (response.text, response.input_tokens, response.output_tokens) == (
        '{"summary": "x"}',
        120,
        8,
    )


async def test_errors_are_classified() -> None:
    provider = OllamaProvider(
        model="m",
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text="model not found")),
    )
    with pytest.raises(LLMError) as info:
        await provider.generate("hi")
    assert info.value.retryable is False
