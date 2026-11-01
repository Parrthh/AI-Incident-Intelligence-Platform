"""Home page at ``/``: what the project is, shown with a real alert and its summary.

The hero pairs a raw alert with the summary written for it. It uses the newest
summarized incident in this deployment's database. When there isn't one yet (a
fresh install, or no database), it shows an example captured from a chaos run,
and says so on the page. Every number on the page comes from the database.
"""

from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError

from iip import __version__
from iip.incidents.queries import SiteStats, latest_summarized, site_stats
from iip.web.pages import templates

logger = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

REPO_URL = "https://github.com/Parrthh/AI-Incident-Intelligence-Platform"

# Shown on the roadmap. Update when a milestone changes state.
MILESTONES: list[tuple[str, str, str]] = [
    ("Foundations", "done", "Project setup, automated tests and a checked deploy on every change."),
    (
        "Alerts to summaries",
        "done",
        "Receive alerts, open incidents and write an AI summary for each.",
    ),
    (
        "Reliable pipeline",
        "done",
        "A job queue, so no summary is lost when a server restarts or a service is down.",
    ),
    (
        "Grouping and evidence",
        "done",
        "Group related alerts into one incident and gather logs, traces and recent deploys.",
    ),
    (
        "Root cause with citations",
        "active",
        "Suggest likely causes, each pointing at its evidence, and measure accuracy on test cases.",
    ),
    (
        "Similar past incidents",
        "planned",
        "Find earlier incidents that looked like this one and how they were fixed.",
    ),
    (
        "Production hardening",
        "planned",
        "Security, rate limits, data privacy controls and monitoring of the platform itself.",
    ),
    ("Public launch", "planned", "Docs, a one-command install and a release other teams can use."),
]

# Captured from the `payment_failure` chaos run on 2026-11-01: the alert as
# Alertmanager sent it (tests/fixtures/alertmanager/captured_payment_failure_firing.json)
# and the summary gemini-3.5-flash-lite wrote for it in that run.
EXAMPLE = {
    "live": False,
    "alertname": "HighErrorRate",
    "status": "firing",
    "labels": [("service", "payment"), ("severity", "critical")],
    "description": "100% of payment spans ended in error over the last 3 minutes.",
    "starts_at": "2026-11-01T05:20:51Z",
    "summary": (
        "The payment service is experiencing a critical error rate where 100% of payment "
        "spans have ended in error over the last 3 minutes. The payment service is affected "
        "and 100% of its spans are failing. This incident has been ongoing since "
        "2026-11-01T05:20:51.510000+00:00."
    ),
    "model": "gemini-3.5-flash-lite",
    "prompt": "summary_v0",
    "latency": None,  # not recorded for this run
    "link": None,
}

LABELS_SHOWN = ("service", "severity", "instance", "job")


@dataclass(frozen=True)
class StatsView:
    incidents: str
    alerts: str
    summaries: str
    median: str


def _stats_view(stats: SiteStats | None) -> StatsView:
    if stats is None:
        return StatsView("0", "0", "0", "n/a")
    ms = stats.median_summary_ms
    median = "n/a" if ms is None else (f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s")
    return StatsView(f"{stats.incidents:,}", f"{stats.alerts:,}", f"{stats.summaries:,}", median)


def _latency(ms: int | None) -> str | None:
    if ms is None:
        return None
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    showcase: dict[str, Any] = EXAMPLE
    stats: SiteStats | None = None
    factory = getattr(request.app.state, "sessionmaker", None)
    if factory is not None:
        try:
            async with factory() as session:
                stats = await site_stats(session)
                latest = await latest_summarized(session)
        except SQLAlchemyError as exc:
            # The home page must render even if the database is briefly unreachable.
            logger.warning("home_data_unavailable", error=type(exc).__name__)
            latest = None
        if latest is not None:
            incident, alert, analysis = latest
            showcase = {
                "live": True,
                "alertname": alert.alertname,
                "status": alert.status,
                "labels": [(k, alert.labels[k]) for k in LABELS_SHOWN if alert.labels.get(k)],
                "description": alert.annotations.get("description")
                or alert.annotations.get("summary"),
                "starts_at": alert.starts_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": analysis.summary,
                "model": analysis.model,
                "prompt": analysis.prompt_version,
                "latency": _latency(analysis.latency_ms),
                "link": f"/incidents/{incident.id}",
            }

    sha: str = request.app.state.settings.commit_sha
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "showcase": showcase,
            "stats": _stats_view(stats),
            "milestones": [{"name": n, "state": st, "about": a} for n, st, a in MILESTONES],
            "repo_url": REPO_URL,
            "version": __version__,
            "commit": sha[:12] if sha != "unknown" else sha,
        },
    )
