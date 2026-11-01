"""Server-rendered UI pages (ADR-0007).

Jinja2 escapes every value by default, so alert text written by anyone who can
send alerts (labels, annotations) can't inject HTML or scripts into the page.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, PackageLoader, select_autoescape

from iip import __version__
from iip.api.deps import SessionDep
from iip.db.models import Evidence
from iip.incidents.queries import (
    InvalidCursorError,
    get_incident_detail,
    incident_evidence,
    incident_timeline,
    list_incidents,
)
from iip.web.timeline import build_timeline


def prompt_version(prompt_id: str) -> str:
    """Show a stored prompt ID like ``summary_v0`` as the plain number ``0``."""
    _, _, version = prompt_id.rpartition("_v")
    return version or prompt_id


_env = Environment(
    loader=PackageLoader("iip.web", "templates"),
    autoescape=select_autoescape(["html"]),
)
_env.filters["prompt_version"] = prompt_version  # pyright: ignore[reportUnknownMemberType]
templates = Jinja2Templates(env=_env)

router = APIRouter(include_in_schema=False)

# Order of the evidence sections on the incident page, most telling first.
EVIDENCE_ORDER = ("deploy", "log", "trace", "metric", "alert")


def _context(request: Request, **values: Any) -> dict[str, Any]:
    sha: str = request.app.state.settings.commit_sha
    return {"version": __version__, "commit": sha[:12] if sha != "unknown" else sha, **values}


@router.get("/incidents", response_class=HTMLResponse)
async def incidents_page(
    request: Request,
    session: SessionDep,
    status_filter: Annotated[Literal["open", "resolved"] | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> HTMLResponse:
    try:
        page = await list_incidents(session, status=status_filter, cursor=cursor)
    except InvalidCursorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc
    return templates.TemplateResponse(
        request,
        "incidents.html",
        _context(
            request,
            nav="incidents",
            items=page.items,
            next_cursor=page.next_cursor,
            status=status_filter,
        ),
    )


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
async def incident_page(
    request: Request, incident_id: uuid.UUID, session: SessionDep
) -> HTMLResponse:
    detail = await get_incident_detail(session, incident_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    incident, alerts, summary = detail
    events = await incident_timeline(session, incident_id)
    evidence = await incident_evidence(session, incident_id)
    by_kind: dict[str, list[Evidence]] = {}
    for item in evidence:
        by_kind.setdefault(item.kind, []).append(item)
    return templates.TemplateResponse(
        request,
        "incident.html",
        _context(
            request,
            nav="incidents",
            incident=incident,
            alerts=alerts,
            summary=summary,
            timeline=build_timeline(events, evidence),
            evidence_by_kind=[(k, by_kind[k]) for k in EVIDENCE_ORDER if k in by_kind],
            evidence_count=len(evidence),
        ),
    )
