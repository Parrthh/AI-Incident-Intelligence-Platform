"""``POST /v1/deploys``: tell the platform a service was deployed.

A deploy shortly before an incident is one of the most useful clues to its
cause. CI pipelines report deploys here (see docs/deploys.md); they become
evidence for incidents on the same service.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import any_, func, literal, update

from iip.api.deps import SessionDep, require_webhook_token
from iip.core.lifecycle import ACTIVE
from iip.db.models import Deploy, Incident

router = APIRouter(prefix="/v1/deploys", tags=["deploys"])


class DeployIn(BaseModel):
    service: Annotated[str, Field(min_length=1, max_length=200)]
    version: Annotated[str | None, Field(max_length=200)] = None
    commit_sha: Annotated[str | None, Field(max_length=64)] = None
    deployed_at: datetime | None = None  # default: now
    description: Annotated[str | None, Field(max_length=2000)] = None
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])


class DeployOut(BaseModel):
    id: uuid.UUID
    service: str
    deployed_at: datetime
    incidents_to_recheck: int


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DeployOut,
    dependencies=[Depends(require_webhook_token)],
)
async def report_deploy(body: DeployIn, session: SessionDep, request: Request) -> DeployOut:
    deployed_at = body.deployed_at or datetime.now(UTC)
    if deployed_at.tzinfo is None:
        deployed_at = deployed_at.replace(tzinfo=UTC)
    deploy = Deploy(
        service=body.service,
        version=body.version,
        commit_sha=body.commit_sha,
        deployed_at=deployed_at,
        description=body.description,
        metadata_=body.metadata,
    )
    session.add(deploy)
    # A deploy to a service that is in an open incident (a rollback or a fix, say)
    # is new evidence: collect again soon.
    debounce = timedelta(seconds=request.app.state.settings.enrichment_debounce_seconds)
    rechecked = await session.execute(
        update(Incident)
        .where(
            Incident.status.in_([s.value for s in ACTIVE]),
            literal(body.service) == any_(Incident.services),
        )
        .values(enrich_after=func.coalesce(Incident.enrich_after, datetime.now(UTC) + debounce))
        .returning(Incident.id)
    )
    count = len(rechecked.all())
    await session.commit()
    return DeployOut(
        id=deploy.id, service=deploy.service, deployed_at=deployed_at, incidents_to_recheck=count
    )
