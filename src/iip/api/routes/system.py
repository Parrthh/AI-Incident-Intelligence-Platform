"""Operational endpoints: liveness, readiness, version."""

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from iip import __version__
from iip.api.health import ReadinessChecks
from iip.config import Settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class CheckStatus(BaseModel):
    ok: bool
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


class VersionResponse(BaseModel):
    version: str
    git_sha: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readyz(request: Request, response: Response) -> ReadinessResponse:
    checks: ReadinessChecks = request.app.state.readiness
    results = await checks.run()
    ready = all(r.ok for r in results)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        checks={r.name: CheckStatus(ok=r.ok, error=r.error) for r in results},
    )


@router.get("/version", response_model=VersionResponse)
async def version(request: Request) -> VersionResponse:
    settings: Settings = request.app.state.settings
    return VersionResponse(version=__version__, git_sha=settings.git_sha)
