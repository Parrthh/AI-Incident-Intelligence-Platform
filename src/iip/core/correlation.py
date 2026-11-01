"""Decide whether a new alert belongs to an open incident (M3).

Pure functions, no I/O: the caller passes in the alert, the open incidents and
the service graph, and gets back a decision with a reason a person can read.
The reason is stored on the incident's timeline, so every grouping can be
explained afterwards.

Rules, first match wins:
1. **Same alert firing again.** An open incident already has an alert with this
   fingerprint (it resolved and fired again, or flaps).
2. **Same service.** An open incident covers this service and started within the
   correlation window.
3. **Connected service.** An open incident covers a service that calls this one,
   or is called by it, and started within the window.
4. Otherwise, open a new incident.

When several incidents match a rule, the most recently started one wins: it's
the most likely to be the same ongoing problem.

Where it fails, on purpose kept simple: two unrelated failures on connected
services within the window are merged, and a failure that spreads to a service
two hops away opens a second incident. Both are visible on the timeline.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from iip.core.graph import ServiceGraph


class Rule(StrEnum):
    SAME_FINGERPRINT = "same_fingerprint"
    SAME_SERVICE = "same_service"
    CONNECTED_SERVICE = "connected_service"
    NEW = "new_incident"


@dataclass(frozen=True)
class AlertFacts:
    fingerprint: str
    alertname: str
    service: str | None
    starts_at: datetime


@dataclass(frozen=True)
class OpenIncident:
    id: uuid.UUID
    started_at: datetime
    services: frozenset[str]
    fingerprints: frozenset[str]


@dataclass(frozen=True)
class Decision:
    rule: Rule
    reason: str
    incident_id: uuid.UUID | None = None  # None means open a new incident

    @property
    def is_new(self) -> bool:
        return self.incident_id is None


def correlate(
    alert: AlertFacts,
    open_incidents: Sequence[OpenIncident],
    graph: ServiceGraph,
    window: timedelta,
) -> Decision:
    newest_first = sorted(open_incidents, key=lambda i: i.started_at, reverse=True)

    for incident in newest_first:
        if alert.fingerprint in incident.fingerprints:
            return Decision(
                Rule.SAME_FINGERPRINT,
                f"{alert.alertname} fired again; it is already part of this incident.",
                incident.id,
            )

    if alert.service is None:
        return Decision(
            Rule.NEW,
            f"{alert.alertname} has no service label, so it can't be matched to an incident.",
        )

    recent = [i for i in newest_first if abs(alert.starts_at - i.started_at) <= window]
    minutes = _minutes(window)

    for incident in recent:
        if alert.service in incident.services:
            return Decision(
                Rule.SAME_SERVICE,
                f"{alert.alertname} is on {alert.service}, which this incident already covers, "
                f"and it started within {minutes} of the incident.",
                incident.id,
            )

    for incident in recent:
        linked = sorted(s for s in incident.services if graph.connected(alert.service, s))
        if linked:
            return Decision(
                Rule.CONNECTED_SERVICE,
                f"{alert.alertname} is on {alert.service}, which is connected to "
                f"{_join(linked)} in the service graph, and it started within {minutes} "
                "of the incident.",
                incident.id,
            )

    return Decision(Rule.NEW, _new_reason(alert, open_incidents, minutes))


def _new_reason(alert: AlertFacts, open_incidents: Sequence[OpenIncident], minutes: str) -> str:
    if not open_incidents:
        return f"{alert.alertname} on {alert.service}: no other incident was open."
    return (
        f"{alert.alertname} on {alert.service}: no open incident covers this service or a "
        f"connected one within {minutes}."
    )


def _minutes(window: timedelta) -> str:
    total = int(window.total_seconds() // 60)
    return "1 minute" if total == 1 else f"{total} minutes"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
