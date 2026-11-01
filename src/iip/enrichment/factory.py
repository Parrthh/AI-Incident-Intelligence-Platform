"""Build the enricher with the sources this deployment has configured."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.config import Settings
from iip.enrichment.base import EvidenceSource
from iip.enrichment.runner import Enricher
from iip.enrichment.sources.database import AlertSource, DeploySource


def build_sources(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> list[EvidenceSource]:
    # Always available: they read this database. Prometheus, traces and logs are
    # added when their URLs are configured.
    return [AlertSource(sessionmaker), DeploySource(sessionmaker)]


def build_enricher(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> Enricher:
    return Enricher(
        sessionmaker,
        build_sources(sessionmaker, settings),
        lookback=timedelta(seconds=settings.evidence_lookback_seconds),
        source_timeout=settings.evidence_source_timeout_seconds,
    )
