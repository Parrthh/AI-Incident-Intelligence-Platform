"""Landing page at ``/``.

A placeholder until the web UI arrives in M1. It gives anyone who opens the bare
URL (a recruiter, a curious visitor, a platform probe) something meaningful
instead of a 404: what this is, which build is running, and where to look next.
"""

from html import escape
from importlib.resources import files
from string import Template

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from iip import __version__
from iip.config import Settings

router = APIRouter(tags=["system"])

REPO_URL = "https://github.com/Parrthh/AI-Incident-Intelligence-Platform"

# Loaded once at import. $-placeholders (string.Template) so the CSS braces in the
# file need no escaping.
_PAGE = Template(files("iip.api").joinpath("templates/home.html").read_text(encoding="utf-8"))


@router.api_route(
    "/", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False
)
async def home(request: Request) -> HTMLResponse:
    settings: Settings = request.app.state.settings
    sha = settings.commit_sha
    html = _PAGE.substitute(
        version=escape(__version__),
        commit=escape(sha[:12] if sha != "unknown" else sha),
        repo=REPO_URL,
        repo_short="github.com/Parrthh/AI-Incident-Intelligence-Platform",
    )
    return HTMLResponse(html)
