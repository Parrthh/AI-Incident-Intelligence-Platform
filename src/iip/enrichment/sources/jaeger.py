"""Failing traces from Jaeger.

Asks Jaeger's query API for traces with an error on each affected service in
the incident window, then groups the failing spans by service, operation and
error message. Each group becomes one evidence item, for example:

    payment: 41 failing Charge spans, "Payment request failed. Invalid token."
    (first at 14:38:21, example trace 4bf92f3577b34da6)

Spans from other services in the same traces are counted too, which shows how
far a failure spread (checkout calling payment, say).
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from iip.enrichment.base import EnrichmentContext, EvidenceItem, EvidenceKind
from iip.enrichment.http import SourceClient

TRACES_PER_SERVICE = 50
TOP_GROUPS = 8


@dataclass
class _Group:
    service: str
    operation: str
    message: str
    count: int = 0
    first_seen: datetime | None = None
    trace_ids: list[str] = field(default_factory=list[str])
    max_duration_ms: float = 0.0


def _tags(span: dict[str, Any]) -> dict[str, Any]:
    tags = cast(list[dict[str, Any]], span.get("tags", []))
    return {str(t.get("key")): t.get("value") for t in tags}


def _error_message(span: dict[str, Any], tags: dict[str, Any]) -> str:
    message = tags.get("otel.status_description") or tags.get("error.message")
    if not message:
        for log in cast(list[dict[str, Any]], span.get("logs", [])):
            fields = {f.get("key"): f.get("value") for f in log.get("fields", [])}
            message = fields.get("exception.message") or fields.get("message")
            if message:
                break
    return str(message or "error")[:300]


def _is_error(tags: dict[str, Any]) -> bool:
    return tags.get("error") in (True, "true") or tags.get("otel.status_code") == "ERROR"


def failing_span_groups(traces: list[dict[str, Any]]) -> list[_Group]:
    groups: dict[tuple[str, str, str], _Group] = {}
    seen_spans: set[str] = set()
    for trace in traces:
        processes = cast(dict[str, dict[str, Any]], trace.get("processes", {}))
        for span in cast(list[dict[str, Any]], trace.get("spans", [])):
            span_id = f"{span.get('traceID')}:{span.get('spanID')}"
            if span_id in seen_spans:
                continue
            seen_spans.add(span_id)
            tags = _tags(span)
            if not _is_error(tags):
                continue
            service = str(processes.get(span.get("processID", ""), {}).get("serviceName", "?"))
            operation = str(span.get("operationName", "?"))
            message = _error_message(span, tags)
            group = groups.setdefault(
                (service, operation, message), _Group(service, operation, message)
            )
            group.count += 1
            at = datetime.fromtimestamp(int(span.get("startTime", 0)) / 1_000_000, UTC)
            if group.first_seen is None or at < group.first_seen:
                group.first_seen = at
            trace_id = str(span.get("traceID"))
            if trace_id not in group.trace_ids and len(group.trace_ids) < 3:
                group.trace_ids.append(trace_id)
            group.max_duration_ms = max(group.max_duration_ms, int(span.get("duration", 0)) / 1000)
    return sorted(groups.values(), key=lambda g: -g.count)


class JaegerSource:
    name = "traces"

    def __init__(self, base_url: str, **client: Any) -> None:
        self._http = SourceClient(base_url, **client)

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        traces: list[dict[str, Any]] = []
        for service in ctx.services:
            data = await self._http.get_json(
                "/api/traces",
                {
                    "service": service,
                    "start": int(ctx.window_start.timestamp() * 1_000_000),
                    "end": int(ctx.window_end.timestamp() * 1_000_000),
                    "limit": TRACES_PER_SERVICE,
                    "tags": json.dumps({"error": "true"}),
                },
            )
            traces.extend(cast(list[dict[str, Any]], data.get("data") or []))
        items: list[EvidenceItem] = []
        for group in failing_span_groups(traces)[:TOP_GROUPS]:
            first = group.first_seen or ctx.started_at
            spans = "span" if group.count == 1 else "spans"
            items.append(
                EvidenceItem(
                    kind=EvidenceKind.TRACE,
                    summary=(
                        f"{group.service}: {group.count} failing {group.operation} {spans}, "
                        f'"{group.message}" (first at {first:%H:%M:%S}, '
                        f"example trace {group.trace_ids[0][:16]})"
                    ),
                    observed_at=first,
                    dedupe_key=f"{group.service}:{group.operation}:{group.message[:120]}",
                    payload={
                        "service": group.service,
                        "operation": group.operation,
                        "error": group.message,
                        "count": group.count,
                        "trace_ids": group.trace_ids,
                        "max_duration_ms": round(group.max_duration_ms, 1),
                    },
                )
            )
        return items

    async def aclose(self) -> None:
        await self._http.aclose()
