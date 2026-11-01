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
from iip.incidents.queries import InvalidCursorError, get_incident_detail, list_incidents

templates = Jinja2Templates(
    env=Environment(
        loader=PackageLoader("iip.web", "templates"),
        autoescape=select_autoescape(["html"]),
    )
)


def prompt_version(prompt_id: str) -> str:
    """Show a stored prompt ID like ``summary_v0`` as the plain number ``0``."""
    _, _, version = prompt_id.rpartition("_v")
    return version or prompt_id


templates.env.filters["prompt_version"] = prompt_version

router = APIRouter(include_in_schema=False)


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
    return templates.TemplateResponse(
        request,
        "incident.html",
        _context(request, nav="incidents", incident=incident, alerts=alerts, summary=summary),
    )
