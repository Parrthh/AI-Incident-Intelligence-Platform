import pytest

from iip.llm.base import LLMError
from iip.llm.retry import with_retries


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def test_retries_retryable_errors_then_succeeds() -> None:
    sleeps = Sleeps()
    outcomes: list[LLMError | str] = [
        LLMError("rate limited", retryable=True),
        LLMError("server error", retryable=True),
        "ok",
    ]

    async def call() -> str:
        outcome = outcomes.pop(0)
        if isinstance(outcome, LLMError):
            raise outcome
        return outcome

    assert await with_retries(call, max_retries=2, sleep=sleeps) == "ok"
    assert len(sleeps.delays) == 2
    # Full jitter: each wait is between 0 and the exponential cap (1s, then 2s).
    assert 0 <= sleeps.delays[0] <= 1.0
    assert 0 <= sleeps.delays[1] <= 2.0


async def test_gives_up_after_max_retries() -> None:
    sleeps = Sleeps()
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        raise LLMError("still down", retryable=True)

    with pytest.raises(LLMError, match="still down"):
        await with_retries(call, max_retries=2, sleep=sleeps)
    assert calls == 3  # first try + 2 retries


async def test_permanent_errors_are_not_retried() -> None:
    sleeps = Sleeps()
    calls = 0

    async def call() -> str:
        nonlocal calls
        calls += 1
        raise LLMError("bad api key", retryable=False)

    with pytest.raises(LLMError):
        await with_retries(call, max_retries=5, sleep=sleeps)
    assert calls == 1
    assert sleeps.delays == []
