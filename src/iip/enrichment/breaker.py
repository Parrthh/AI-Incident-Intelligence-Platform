"""A small circuit breaker for evidence sources.

When a source keeps failing (Jaeger is down, say), waiting for its timeout on
every incident only slows enrichment down. After ``threshold`` failures in a
row the breaker "opens" and calls fail immediately for ``cooldown`` seconds.
Then one trial call is let through ("half-open"): success closes the breaker,
failure opens it again.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


class CircuitOpenError(Exception):
    pass


@dataclass
class CircuitBreaker:
    threshold: int = 3
    cooldown: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "open" if self.clock() - self._opened_at < self.cooldown else "half_open"

    def before_call(self) -> None:
        if self.state == "open":
            raise CircuitOpenError(f"skipped: failed {self._failures} times in a row")

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold or self.state == "half_open":
            self._opened_at = self.clock()
