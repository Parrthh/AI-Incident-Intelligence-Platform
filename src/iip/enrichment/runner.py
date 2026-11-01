"""The enrichment job: collect evidence for one incident from every source.

Sources run side by side, each with its own timeout and circuit breaker. A
source that fails or times out doesn't stop the others: its failure goes on the
timeline ("logs unavailable: timeout"), so a person, and the analysis in M4,
can see what's missing instead of silently getting less evidence.

Storing evidence:
- Each item gets a short ID, ``ev_<kind>_<n>``, numbered per incident and kind
  (``ev_deploy_1``, ``ev_log_3``). Analysis cites these IDs.
- An item seen before (same ``dedupe_key``) is updated in place and keeps its
  ID, so collecting twice never duplicates evidence or renumbers it.
- IDs are assigned under a per-incident advisory lock, so two runs for the same
  incident can't hand out the same number.
"""

import asyncio
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.db.models import Evidence, Incident, IncidentEvent
from iip.enrichment.base import EnrichmentContext, EvidenceItem, EvidenceSource
from iip.enrichment.breaker import CircuitBreaker, CircuitOpenError
from iip.observability.metrics import ENRICHMENT_DURATION, EVIDENCE_COLLECTED

logger = structlog.get_logger(__name__)

EVIDENCE_LOCK_CLASS = 7392003
DEFAULT_SOURCE_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class SourceResult:
    source: str
    items: list[EvidenceItem]
    error: str | None
    duration_ms: int


class Enricher:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        sources: Sequence[EvidenceSource],
        *,
        lookback: timedelta = timedelta(minutes=15),
        source_timeout: float = DEFAULT_SOURCE_TIMEOUT_SECONDS,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._sources = list(sources)
        self._lookback = lookback
        self._timeout = source_timeout
        # One breaker per source, kept for the life of the process.
        self._breakers = {s.name: CircuitBreaker() for s in self._sources}

    @property
    def source_names(self) -> list[str]:
        return [s.name for s in self._sources]

    async def aclose(self) -> None:
        for source in self._sources:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()

    async def enrich(self, incident_id: uuid.UUID) -> list[SourceResult] | None:
        """Collect and store evidence. Returns None if the incident doesn't exist."""
        async with self._sessionmaker() as session:
            incident = await session.get(Incident, incident_id)
            if incident is None:
                return None
            now = datetime.now(UTC)
            ctx = EnrichmentContext(
                incident_id=incident.id,
                title=incident.title,
                services=tuple(incident.services),
                started_at=incident.started_at,
                window_start=incident.started_at - self._lookback,
                window_end=incident.resolved_at or now,
            )

        # No database connection is held while sources run: they can take seconds.
        results = await asyncio.gather(*(self._collect(source, ctx) for source in self._sources))

        async with self._sessionmaker() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:cls, hashtext(:id))"),
                {"cls": EVIDENCE_LOCK_CLASS, "id": str(incident_id)},
            )
            for result in results:
                if result.error is None:
                    stored = await _store(session, incident_id, result)
                    session.add(
                        IncidentEvent(
                            incident_id=incident_id,
                            at=datetime.now(UTC),
                            kind="evidence_collected",
                            payload={
                                "source": result.source,
                                "count": len(result.items),
                                "new": stored,
                                "duration_ms": result.duration_ms,
                            },
                        )
                    )
                else:
                    session.add(
                        IncidentEvent(
                            incident_id=incident_id,
                            at=datetime.now(UTC),
                            kind="evidence_unavailable",
                            payload={
                                "source": result.source,
                                "reason": f"{result.source} unavailable: {result.error}",
                                "duration_ms": result.duration_ms,
                            },
                        )
                    )
            incident = await session.get(Incident, incident_id)
            if incident is not None:
                incident.enriched_at = datetime.now(UTC)
            await session.commit()

        logger.info(
            "incident_enriched",
            incident_id=str(incident_id),
            sources={r.source: (r.error or len(r.items)) for r in results},
        )
        return results

    async def _collect(self, source: EvidenceSource, ctx: EnrichmentContext) -> SourceResult:
        breaker = self._breakers[source.name]
        started = time.perf_counter()
        error: str | None = None
        items: list[EvidenceItem] = []
        try:
            breaker.before_call()
            items = await asyncio.wait_for(source.collect(ctx), timeout=self._timeout)
            breaker.record_success()
        except CircuitOpenError as exc:
            error = str(exc)
        except TimeoutError:
            breaker.record_failure()
            error = f"timeout after {self._timeout:g} s"
        except Exception as exc:  # noqa: BLE001 - any source failure is recorded, never fatal
            breaker.record_failure()
            error = f"{type(exc).__name__}: {exc}"[:300]
        elapsed = time.perf_counter() - started
        outcome = "ok" if error is None else "error"
        ENRICHMENT_DURATION.labels(source=source.name, outcome=outcome).observe(elapsed)
        if error is None:
            EVIDENCE_COLLECTED.labels(source=source.name).inc(len(items))
        else:
            logger.warning("evidence_source_failed", source=source.name, error=error)
        return SourceResult(source.name, items, error, round(elapsed * 1000))


async def _store(session: AsyncSession, incident_id: uuid.UUID, result: SourceResult) -> int:
    """Upsert items; new ones get the next ID for their kind. Returns how many were new."""
    new = 0
    for item in result.items:
        existing = await session.scalar(
            select(Evidence.id).where(
                Evidence.incident_id == incident_id,
                Evidence.kind == item.kind.value,
                Evidence.dedupe_key == item.dedupe_key,
            )
        )
        if existing is None:
            count = await session.scalar(
                select(func.count())
                .select_from(Evidence)
                .where(Evidence.incident_id == incident_id, Evidence.kind == item.kind.value)
            )
            evidence_id = f"ev_{item.kind.value}_{(count or 0) + 1}"
            new += 1
        else:
            evidence_id = existing
        stmt = insert(Evidence).values(
            incident_id=incident_id,
            id=evidence_id,
            kind=item.kind.value,
            source=result.source,
            summary=item.summary[:500],
            payload=item.payload,
            observed_at=item.observed_at,
            dedupe_key=item.dedupe_key,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["incident_id", "id"],
                set_={
                    "summary": stmt.excluded.summary,
                    "payload": stmt.excluded.payload,
                    "observed_at": stmt.excluded.observed_at,
                    "updated_at": func.now(),
                },
            )
        )
    return new
