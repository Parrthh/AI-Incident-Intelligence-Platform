"""Snapshot format: everything the analysis saw for one incident, plus the truth."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from iip.db.models import Alert, Evidence


class Cause(BaseModel):
    service: str
    category: str


class Scenario(BaseModel):
    name: str
    root_cause_service: str
    category: str
    # Other answers a reasonable engineer would also accept, with the reason in
    # the scenario file (e.g. "the demo fakes an outage with a bad address").
    also_correct: list[Cause] = Field(default_factory=list[Cause])

    def accepts(self, service: str, category: str) -> bool:
        if (service, category) == (self.root_cause_service, self.category):
            return True
        return any((c.service, c.category) == (service, category) for c in self.also_correct)

    def accepts_service(self, service: str) -> bool:
        return service == self.root_cause_service or any(
            c.service == service for c in self.also_correct
        )


class SnapshotAlert(BaseModel):
    alertname: str
    status: str
    service: str | None = None
    severity: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict[str, Any])
    annotations: dict[str, Any] = Field(default_factory=dict[str, Any])
    starts_at: datetime
    ends_at: datetime | None = None


class SnapshotEvidence(BaseModel):
    id: str
    kind: str
    source: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])
    observed_at: datetime


class SnapshotIncident(BaseModel):
    title: str
    status: str
    services: list[str]
    started_at: datetime


class Snapshot(BaseModel):
    id: str
    scenario: Scenario
    recorded_at: datetime
    source: dict[str, Any] = Field(default_factory=dict[str, Any])  # commit, demo version, run
    incident: SnapshotIncident
    alerts: list[SnapshotAlert]
    evidence: list[SnapshotEvidence]
    unavailable: dict[str, str] = Field(default_factory=dict[str, str])

    def orm_alerts(self) -> list[Alert]:
        """Transient ORM objects, so the production prompt code can be reused as is."""
        return [
            Alert(
                alertname=a.alertname,
                status=a.status,
                service=a.service,
                severity=a.severity,
                labels=a.labels,
                annotations=a.annotations,
                starts_at=a.starts_at,
                ends_at=a.ends_at,
            )
            for a in self.alerts
        ]

    def orm_evidence(self) -> list[Evidence]:
        incident_id = uuid.uuid5(uuid.NAMESPACE_URL, self.id)
        return [
            Evidence(
                incident_id=incident_id,
                id=e.id,
                kind=e.kind,
                source=e.source,
                summary=e.summary,
                payload=e.payload,
                observed_at=e.observed_at,
                dedupe_key=e.id,
            )
            for e in self.evidence
        ]


def load_snapshots(root: Path) -> list[Snapshot]:
    paths = sorted(root.rglob("*.json"))
    return [Snapshot.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]
