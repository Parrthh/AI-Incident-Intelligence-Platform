"""Read-only incident API (``/v1/incidents``), used by the UI and by integrations."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from iip.api.deps import SessionDep
from iip.db.models import Alert, Analysis, Incident
from iip.incidents.queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidCursorError,
    get_incident_detail,
    list_incidents,
)

router = APIRouter(prefix="/v1/incidents", tags=["incidents"])


class SummaryOut(BaseModel):
    status: str
    text: str | None
    model: str
    prompt_version: str
    created_at: datetime
    completed_at: datetime | None
    error: str | None

    @classmethod
    def from_row(cls, row: Analysis) -> "SummaryOut":
        return cls(
            status=row.status,
            text=row.summary,
            model=row.model,
            prompt_version=row.prompt_version,
            created_at=row.created_at,
            completed_at=row.completed_at,
            # Don't expose raw provider errors publicly; the status says enough.
            error="summary failed" if row.status == "failed" else None,
        )


class IncidentOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    severity: str | None
    services: list[str]
    started_at: datetime
    resolved_at: datetime | None
    summary: SummaryOut | None

    @classmethod
    def from_row(cls, row: Incident, summary: Analysis | None) -> "IncidentOut":
        return cls(
            id=row.id,
            title=row.title,
            status=row.status,
            severity=row.severity,
            services=list(row.services),
            started_at=row.started_at,
            resolved_at=row.resolved_at,
            summary=SummaryOut.from_row(summary) if summary else None,
        )


class AlertOut(BaseModel):
    id: uuid.UUID
    alertname: str
    status: str
    service: str | None
    severity: str | None
    labels: dict[str, Any]
    annotations: dict[str, Any]
    starts_at: datetime
    ends_at: datetime | None

    @classmethod
    def from_row(cls, row: Alert) -> "AlertOut":
        return cls(
            id=row.id,
            alertname=row.alertname,
            status=row.status,
            service=row.service,
            severity=row.severity,
            labels=row.labels,
            annotations=row.annotations,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )


class IncidentDetailOut(IncidentOut):
    alerts: list[AlertOut]


class IncidentListOut(BaseModel):
    items: list[IncidentOut]
    next_cursor: str | None


@router.get("", response_model=IncidentListOut)
async def list_incidents_endpoint(
    session: SessionDep,
    status_filter: Annotated[Literal["open", "resolved"] | None, Query(alias="status")] = None,
    service: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> IncidentListOut:
    try:
        page = await list_incidents(
            session, status=status_filter, service=service, cursor=cursor, limit=limit
        )
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc
    return IncidentListOut(
        items=[IncidentOut.from_row(i, s) for i, s in page.items], next_cursor=page.next_cursor
    )


@router.get("/{incident_id}", response_model=IncidentDetailOut)
async def get_incident_endpoint(incident_id: uuid.UUID, session: SessionDep) -> IncidentDetailOut:
    detail = await get_incident_detail(session, incident_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    incident, alerts, summary = detail
    base = IncidentOut.from_row(incident, summary)
    return IncidentDetailOut(**base.model_dump(), alerts=[AlertOut.from_row(a) for a in alerts])
