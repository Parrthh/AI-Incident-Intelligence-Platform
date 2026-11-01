"""Incident lifecycle: the states an incident moves through, and when it resolves.

    open ──► analyzing ──► analyzed ──► resolved
      └──────────┴────────────┴────────────┘ (any active state can resolve)
    resolved ──► open  (never: a new alert after resolution opens a new incident)

M3 uses ``open`` and ``resolved``. ``analyzing`` and ``analyzed`` arrive with the
root-cause analysis in M4; the transitions are defined now so the rules live in
one place.

An incident resolves when every alert in it has resolved and nothing has fired
for the quiet period. Alertmanager already waits before sending "resolved", but
services often recover and fail again a minute later; the quiet period keeps
that from becoming two incidents.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum


class Status(StrEnum):
    OPEN = "open"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    RESOLVED = "resolved"


ACTIVE = frozenset({Status.OPEN, Status.ANALYZING, Status.ANALYZED})

ALLOWED: dict[Status, frozenset[Status]] = {
    Status.OPEN: frozenset({Status.ANALYZING, Status.RESOLVED}),
    Status.ANALYZING: frozenset({Status.ANALYZED, Status.OPEN, Status.RESOLVED}),
    Status.ANALYZED: frozenset({Status.ANALYZING, Status.RESOLVED}),
    Status.RESOLVED: frozenset(),
}


class InvalidTransitionError(ValueError):
    pass


def check_transition(current: Status, target: Status) -> None:
    if target not in ALLOWED[current]:
        raise InvalidTransitionError(f"an incident can't go from {current} to {target}")


def ready_to_resolve(
    alert_statuses: Iterable[str],
    last_activity: datetime | None,
    now: datetime,
    quiet: timedelta,
) -> bool:
    """All alerts resolved, and the last one ended at least ``quiet`` ago."""
    statuses = list(alert_statuses)
    if not statuses or any(s != "resolved" for s in statuses):
        return False
    return last_activity is not None and now - last_activity >= quiet
