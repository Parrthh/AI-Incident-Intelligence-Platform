import pytest

from iip.enrichment.breaker import CircuitBreaker, CircuitOpenError


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_opens_after_threshold_and_half_opens_after_cooldown() -> None:
    clock = Clock()
    breaker = CircuitBreaker(threshold=3, cooldown=60, clock=clock)

    for _ in range(2):
        breaker.before_call()
        breaker.record_failure()
    assert breaker.state == "closed"  # two failures: still trying

    breaker.record_failure()
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        breaker.before_call()  # fails fast instead of waiting on a dead source

    clock.now = 61
    assert breaker.state == "half_open"
    breaker.before_call()  # one trial call is allowed
    breaker.record_failure()
    assert breaker.state == "open"  # trial failed: open again right away

    clock.now = 122
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == "closed"


def test_success_resets_the_failure_count() -> None:
    breaker = CircuitBreaker(threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == "closed"
