"""Pure helpers that turn raw telemetry into one-line evidence.

- ``describe_series``: compare a metric before and after the incident started.
- ``log_template``: mask the variable parts of a log line so that lines from the
  same code path group together ("user 42 failed" and "user 7 failed" become
  "user <num> failed").
- ``group_logs``: count lines per template and keep the first time seen.

No I/O, so all of it is unit tested.
"""

import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

# --------------------------------------------------------------------- metrics


@dataclass(frozen=True)
class SeriesChange:
    baseline: float | None  # median before the incident started
    peak: float
    peak_at: datetime
    changed_at: datetime | None  # first point clearly above the baseline
    changed: bool


def describe_series(
    points: Sequence[tuple[datetime, float]],
    incident_start: datetime,
    *,
    min_absolute_change: float = 0.0,
) -> SeriesChange | None:
    """How the series moved around the incident start. None if there's no data after it."""
    before = [v for t, v in points if t < incident_start]
    after = [(t, v) for t, v in points if t >= incident_start]
    if not after:
        return None
    baseline = statistics.median(before) if before else None
    peak_at, peak = max(after, key=lambda p: p[1])
    threshold = (
        max(baseline * 1.5, baseline + min_absolute_change) if baseline is not None else None
    )
    changed = baseline is None or (threshold is not None and peak > threshold)
    changed_at: datetime | None = None
    if changed and threshold is not None:
        # Often before the incident started: alert rules wait (`for: 1m`) before firing.
        changed_at = next((t for t, v in points if v > threshold), None)
    return SeriesChange(baseline, peak, peak_at, changed_at, changed)


# ------------------------------------------------------------------------ logs

_MASKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "<uuid>",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.]+Z?\b"), "<time>"),
    (re.compile(r"(['\"])(?:(?!\1).){1,200}\1"), "<str>"),
    (re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:ms|s|%)?\b"), "<num>"),
]
_SPACES = re.compile(r"\s+")


def log_template(line: str) -> str:
    text = line.strip()[:2000]
    for pattern, token in _MASKS:
        text = pattern.sub(token, text)
    return _SPACES.sub(" ", text)[:300]


@dataclass
class LogPattern:
    service: str
    template: str
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    example: str = ""
    levels: set[str] = field(default_factory=set[str])


def group_logs(
    lines: Iterable[tuple[str, datetime, str, str | None]], top: int = 10
) -> list[LogPattern]:
    """Group (service, time, line, level) by service and template; biggest groups first."""
    groups: dict[tuple[str, str], LogPattern] = {}
    for service, at, line, level in lines:
        template = log_template(line)
        if not template:
            continue
        group = groups.setdefault((service, template), LogPattern(service, template))
        group.count += 1
        if group.first_seen is None or at < group.first_seen:
            group.first_seen = at
            group.example = line.strip()[:500]
        if group.last_seen is None or at > group.last_seen:
            group.last_seen = at
        if level:
            group.levels.add(level.upper())
    ranked = sorted(groups.values(), key=lambda g: (-g.count, g.first_seen or datetime.max))
    return ranked[:top]
