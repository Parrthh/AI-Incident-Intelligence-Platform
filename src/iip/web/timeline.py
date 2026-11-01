"""Build the rows of an incident's timeline for the page.

The timeline mixes two things: events the platform recorded (incident opened,
alert added and why, evidence collected or unavailable, resolved) and evidence
that has a time of its own (a deploy, a log pattern first seen). Sorting them
together shows the story in order, for example a deploy two minutes before the
first alert.
"""

from dataclasses import dataclass
from datetime import datetime

from iip.db.models import Evidence, IncidentEvent

# Evidence kinds placed on the timeline. Alerts are already there as events.
TIMED_EVIDENCE = frozenset({"deploy", "log", "metric", "trace"})


@dataclass(frozen=True)
class TimelineRow:
    at: datetime
    css: str  # styling hook: opened, attached, resolved, evidence, missing, deploy, ...
    title: str
    detail: str | None = None
    evidence_id: str | None = None


def _where(payload: dict[str, object]) -> str:
    service = payload.get("service")
    return f" on {service}" if service else ""


def event_row(event: IncidentEvent) -> TimelineRow:
    p = event.payload
    name = str(p.get("alertname") or "")
    reason = p.get("reason")
    detail = str(reason) if reason else None
    match event.kind:
        case "incident_opened":
            title = f"Incident opened by {name}{_where(p)}" if name else "Incident opened"
            return TimelineRow(event.at, "opened", title, detail)
        case "alert_attached":
            return TimelineRow(
                event.at, "attached", f"{name}{_where(p)} added to this incident", detail
            )
        case "alert_resolved":
            return TimelineRow(event.at, "cleared", f"{name}{_where(p)} resolved")
        case "incident_resolved":
            return TimelineRow(event.at, "resolved", "Incident resolved", detail)
        case "evidence_collected":
            count = int(str(p.get("count") or 0))
            noun = "item" if count == 1 else "items"
            return TimelineRow(
                event.at, "collected", f"Evidence collected from {p.get('source')}: {count} {noun}"
            )
        case "evidence_unavailable":
            return TimelineRow(event.at, "missing", f"No evidence from {p.get('source')}", detail)
        case _:
            return TimelineRow(event.at, "other", event.kind.replace("_", " ").capitalize(), detail)


def build_timeline(events: list[IncidentEvent], evidence: list[Evidence]) -> list[TimelineRow]:
    rows = [event_row(e) for e in events]
    rows += [
        TimelineRow(e.observed_at, e.kind, e.summary, evidence_id=e.id)
        for e in evidence
        if e.kind in TIMED_EVIDENCE
    ]
    return sorted(rows, key=lambda r: r.at)
