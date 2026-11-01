"""Inbound webhooks.

The handler does only fast work: validate, store, commit, answer 202. Anything
slow (LLM calls, enrichment) runs later, off the request path, so Alertmanager
never times out waiting for us and retries a delivery we already accepted.

Summaries for new incidents are scheduled with ``BackgroundTasks``, which run
after the response is sent. Known M1 tech debt: they live only in this process,
so a restart loses pending ones. M2 replaces this with a durable queue.
"""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from pydantic import BaseModel

from iip.api.deps import SessionDep, require_webhook_token
from iip.ingest.alertmanager import AlertmanagerPayload
from iip.ingest.service import ingest_alertmanager
from iip.llm.summarize import summarize_incident

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
    background: BackgroundTasks,
) -> IngestResponse:
    result = await ingest_alertmanager(session, payload)
    # Commit before scheduling: the background task reads the incident in its own
    # session, so it must already be visible.
    await session.commit()

    llm = request.app.state.llm
    if llm is not None:
        settings = request.app.state.settings
        for incident_id in result.new_incident_ids:
            background.add_task(
                summarize_incident,
                request.app.state.sessionmaker,
                llm,
                incident_id,
                max_retries=settings.llm_max_retries,
            )
    return IngestResponse(
        received=result.received,
        new_alerts=result.new_alerts,
        updated_alerts=result.updated_alerts,
        new_incident_ids=result.new_incident_ids,
        resolved_incident_ids=result.resolved_incident_ids,
    )
