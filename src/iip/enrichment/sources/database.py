"""Evidence that already lives in this database: the incident's alerts, and
deploys reported through ``POST /v1/deploys``."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.db.models import Alert, Deploy
from iip.enrichment.base import EnrichmentContext, EvidenceItem, EvidenceKind


class AlertSource:
    name = "alerts"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        async with self._sessionmaker() as session:
            alerts = (
                await session.scalars(
                    select(Alert)
                    .where(Alert.incident_id == ctx.incident_id)
                    .order_by(Alert.starts_at)
                )
            ).all()
        items: list[EvidenceItem] = []
        for alert in alerts:
            text = alert.annotations.get("description") or alert.annotations.get("summary") or ""
            where = f" on {alert.service}" if alert.service else ""
            items.append(
                EvidenceItem(
                    kind=EvidenceKind.ALERT,
                    summary=f"{alert.alertname}{where} fired: {text}".rstrip(": "),
                    observed_at=alert.starts_at,
                    dedupe_key=f"{alert.fingerprint}@{alert.starts_at.isoformat()}",
                    payload={
                        "alertname": alert.alertname,
                        "service": alert.service,
                        "severity": alert.severity,
                        "status": alert.status,
                        "labels": alert.labels,
                        "annotations": alert.annotations,
                        "starts_at": alert.starts_at.isoformat(),
                        "ends_at": alert.ends_at.isoformat() if alert.ends_at else None,
                    },
                )
            )
        return items


class DeploySource:
    name = "deploys"

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        if not ctx.services:
            return []
        async with self._sessionmaker() as session:
            deploys = (
                await session.scalars(
                    select(Deploy)
                    .where(
                        Deploy.service.in_(ctx.services),
                        Deploy.deployed_at.between(ctx.window_start, ctx.window_end),
                    )
                    .order_by(Deploy.deployed_at)
                    .limit(50)
                )
            ).all()
        items: list[EvidenceItem] = []
        for deploy in deploys:
            version = deploy.version or (deploy.commit_sha or "")[:12] or "a new version"
            when = _relative(ctx.started_at, deploy.deployed_at)
            items.append(
                EvidenceItem(
                    kind=EvidenceKind.DEPLOY,
                    summary=f"{deploy.service} deployed {version}, {when}",
                    observed_at=deploy.deployed_at,
                    dedupe_key=str(deploy.id),
                    payload={
                        "service": deploy.service,
                        "version": deploy.version,
                        "commit_sha": deploy.commit_sha,
                        "description": deploy.description,
                        "metadata": deploy.metadata_,
                    },
                )
            )
        return items


def _relative(incident_start: datetime, deployed_at: datetime) -> str:
    minutes = (incident_start - deployed_at).total_seconds() / 60
    if minutes < 0:
        return f"{-minutes:.0f} min after the incident started"
    if minutes < 1:
        return "less than a minute before the incident started"
    return f"{minutes:.0f} min before the incident started"
