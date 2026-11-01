"""Build the enricher with the sources this deployment has configured."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.config import Settings
from iip.enrichment.base import EvidenceSource
from iip.enrichment.runner import Enricher
from iip.enrichment.sources.database import AlertSource, DeploySource
from iip.enrichment.sources.jaeger import JaegerSource
from iip.enrichment.sources.loki import LokiSource
from iip.enrichment.sources.prometheus import PrometheusSource


def build_sources(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> list[EvidenceSource]:
    # Always available: they read this database. Prometheus, traces and logs are
    # added when their URLs are configured.
    sources: list[EvidenceSource] = [AlertSource(sessionmaker), DeploySource(sessionmaker)]
    if settings.prometheus_url:
        sources.append(PrometheusSource(settings.prometheus_url, settings.prometheus_query_set))
    if settings.jaeger_url:
        sources.append(JaegerSource(settings.jaeger_url))
    if settings.loki_url:
        sources.append(LokiSource(settings.loki_url))
    return sources


def build_enricher(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> Enricher:
    return Enricher(
        sessionmaker,
        build_sources(sessionmaker, settings),
        lookback=timedelta(seconds=settings.evidence_lookback_seconds),
        source_timeout=settings.evidence_source_timeout_seconds,
    )
