"""Inbound webhooks.

The handler does only fast work: validate, store, commit, answer 202. Anything
slow (LLM calls, enrichment) runs later in the worker, so Alertmanager never
times out waiting for us and retries a delivery we already accepted.

Jobs for new incidents are written to the outbox in the same transaction as the
alerts (ADR-0008), so a committed incident always has its job. The outbox relay
is then nudged to publish them to Redis right away.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from iip.api.deps import SessionDep, require_webhook_token
from iip.ingest.alertmanager import AlertmanagerPayload
from iip.ingest.service import ingest_alertmanager
from iip.queue.jobs import JOB_SUMMARIZE
from iip.queue.outbox import OutboxRelay, enqueue

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
async def alertmanager_webhook(
    payload: AlertmanagerPayload,
    session: SessionDep,
    request: Request,
) -> IngestResponse:
    settings = request.app.state.settings
    result = await ingest_alertmanager(session, payload)
    if settings.llm_provider != "none":
        for incident_id in result.new_incident_ids:
            enqueue(session, settings.jobs_stream, JOB_SUMMARIZE, {"incident_id": str(incident_id)})
    await session.commit()

    relay: OutboxRelay | None = request.app.state.outbox_relay
    if relay is not None and result.new_incident_ids:
        relay.notify()
    return IngestResponse(
        received=result.received,
        new_alerts=result.new_alerts,
        updated_alerts=result.updated_alerts,
        new_incident_ids=result.new_incident_ids,
        resolved_incident_ids=result.resolved_incident_ids,
    )
