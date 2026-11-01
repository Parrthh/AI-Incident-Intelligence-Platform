"""Inbound webhooks.

The handler does only fast work: validate, store, commit, answer 202. Anything
slow (LLM calls, enrichment) runs later, off the request path, so Alertmanager
never times out waiting for us and retries a delivery we already accepted.
"""

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from iip.api.deps import SessionDep, require_webhook_token
from iip.ingest.alertmanager import AlertmanagerPayload
from iip.ingest.service import ingest_alertmanager

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


class IngestResponse(BaseModel):
    received: int
    new_alerts: int
    updated_alerts: int
    new_incident_ids: list[uuid.UUID]
    resolved_incident_ids: list[uuid.UUID]


@router.post(
    "/alertmanager",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResponse,
    dependencies=[Depends(require_webhook_token)],
)
async def alertmanager_webhook(payload: AlertmanagerPayload, session: SessionDep) -> IngestResponse:
    result = await ingest_alertmanager(session, payload)
    await session.commit()
    return IngestResponse(
        received=result.received,
        new_alerts=result.new_alerts,
        updated_alerts=result.updated_alerts,
        new_incident_ids=result.new_incident_ids,
        resolved_incident_ids=result.resolved_incident_ids,
    )
