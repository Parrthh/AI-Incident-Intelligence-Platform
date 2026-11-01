"""Types shared by every evidence source.

A source (Prometheus, Jaeger, logs, deploys, ...) gets the incident's context
and returns evidence items. It never writes to the database itself; the runner
stores items, gives them IDs and records failures, so every source behaves the
same way under errors and timeouts.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class EvidenceKind(StrEnum):
    ALERT = "alert"
    DEPLOY = "deploy"
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"


@dataclass(frozen=True)
class EnrichmentContext:
    incident_id: uuid.UUID
    title: str
    services: tuple[str, ...]
    started_at: datetime
    # The time range to look at: from a while before the incident started (causes
    # come first) until now.
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class EvidenceItem:
    kind: EvidenceKind
    summary: str  # one line a person can read
    observed_at: datetime
    # Identifies the same fact across collection runs (the same deploy, the same
    # log pattern), so collecting again updates it instead of adding a copy.
    dedupe_key: str
    payload: dict[str, Any] = field(default_factory=dict[str, Any])


class EvidenceSource(Protocol):
    @property
    def name(self) -> str: ...

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]: ...


class SourceUnavailableError(Exception):
    """The source can't be reached or answered with an error. Retrying later may help."""
