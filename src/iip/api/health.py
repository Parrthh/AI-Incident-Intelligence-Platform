"""Readiness checks.

Liveness (``/healthz``) answers "is the process running?". If it fails, the
platform restarts the container.

Readiness (``/readyz``) answers "can this instance do useful work right now?".
It checks the dependencies the app can't work without (Postgres and Redis, added
in a later step). If it fails, the platform keeps traffic away from this instance
and, during a deploy, keeps the old version running instead of switching over.

Keeping the two separate matters: if liveness checked the database, a database
outage would make the platform restart every app container in a loop, which
fixes nothing and makes recovery slower.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

CheckFn = Callable[[], Awaitable[None]]

# A dependency that takes longer than this to answer counts as not ready.
CHECK_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    error: str | None = None


class ReadinessChecks:
    """A named set of async checks. Each check raises if its dependency is down."""

    def __init__(self) -> None:
        self._checks: dict[str, CheckFn] = {}

    def register(self, name: str, check: CheckFn) -> None:
        self._checks[name] = check

    async def run(self) -> list[CheckResult]:
        names = list(self._checks)
        results = await asyncio.gather(*(self._run_one(n, self._checks[n]) for n in names))
        return list(results)

    async def _run_one(self, name: str, check: CheckFn) -> CheckResult:
        try:
            await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("readiness_check_failed", check=name, error="timeout")
            return CheckResult(name=name, ok=False, error="timeout")
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
            # Only the exception type goes in the response; details stay in logs,
            # because error messages can contain hostnames or credentials.
            logger.warning("readiness_check_failed", check=name, error=repr(exc))
            return CheckResult(name=name, ok=False, error=type(exc).__name__)
        return CheckResult(name=name, ok=True)
