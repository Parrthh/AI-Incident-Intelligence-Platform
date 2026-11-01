"""Metrics from Prometheus: how each affected service's key numbers moved.

For every service in the incident and every query in the query set, it runs a
range query over the incident window and compares the values before and after
the incident started. Each result is one evidence item, for example:

    payment error ratio: 0% before, peaked at 100% at 14:38:50 (rose from 14:37:20)

Query sets are templates with ``{service}``. ``otel-spanmetrics`` fits services
instrumented with OpenTelemetry and the collector's span metrics (the
OpenTelemetry Demo). Others can be added the same way.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from iip.enrichment.analysis import describe_series
from iip.enrichment.base import EnrichmentContext, EvidenceItem, EvidenceKind
from iip.enrichment.http import SourceClient

MAX_POINTS = 120


@dataclass(frozen=True)
class MetricQuery:
    key: str
    label: str
    template: str
    unit: str  # "ratio" or "ms"
    min_change: float  # smallest change that counts as a real move


QUERY_SETS: dict[str, list[MetricQuery]] = {
    "otel-spanmetrics": [
        MetricQuery(
            "error_ratio",
            "error ratio",
            # The error series only exists once an error happened, so "or ... * 0"
            # turns "no errors yet" into 0 instead of no data; otherwise the
            # baseline would only contain moments with errors.
            '(sum(rate(traces_span_metrics_calls_total{{service_name="{service}",'
            'status_code="STATUS_CODE_ERROR"}}[2m])) or '
            'sum(rate(traces_span_metrics_calls_total{{service_name="{service}"}}[2m])) * 0) / '
            'sum(rate(traces_span_metrics_calls_total{{service_name="{service}"}}[2m]))',
            "ratio",
            0.05,
        ),
        MetricQuery(
            "p95_latency",
            "p95 latency",
            "histogram_quantile(0.95, sum by (le) (rate("
            'traces_span_metrics_duration_milliseconds_bucket{{service_name="{service}"}}[2m])))',
            "ms",
            100.0,
        ),
        MetricQuery(
            "request_rate",
            "request rate",
            'sum(rate(traces_span_metrics_calls_total{{service_name="{service}"}}[2m]))',
            "rps",
            1.0,
        ),
    ],
}


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "no data"
    if unit == "ratio":
        return f"{value * 100:.0f}%"
    if unit == "ms":
        return f"{value:.0f} ms"
    return f"{value:.1f}/s"


class PrometheusSource:
    name = "metrics"

    def __init__(self, base_url: str, query_set: str = "otel-spanmetrics", **client: Any) -> None:
        if query_set not in QUERY_SETS:
            raise ValueError(f"unknown Prometheus query set {query_set!r}")
        self._queries = QUERY_SETS[query_set]
        self._http = SourceClient(base_url, **client)

    async def collect(self, ctx: EnrichmentContext) -> list[EvidenceItem]:
        span = (ctx.window_end - ctx.window_start).total_seconds()
        step = max(15, int(span / MAX_POINTS))
        items: list[EvidenceItem] = []
        for service in ctx.services:
            for query in self._queries:
                data = await self._http.get_json(
                    "/api/v1/query_range",
                    {
                        "query": query.template.format(service=service),
                        "start": ctx.window_start.timestamp(),
                        "end": ctx.window_end.timestamp(),
                        "step": step,
                    },
                )
                item = self._item(ctx, service, query, data)
                if item is not None:
                    items.append(item)
        return items

    def _item(
        self, ctx: EnrichmentContext, service: str, query: MetricQuery, data: Any
    ) -> EvidenceItem | None:
        results = cast(list[dict[str, Any]], data.get("data", {}).get("result", []))
        if not results:
            return None
        points: list[tuple[datetime, float]] = []
        for ts, raw in cast(list[list[Any]], results[0].get("values", [])):
            value = float(raw)
            if value == value:  # skip NaN (0/0 when a service had no traffic)
                points.append((datetime.fromtimestamp(float(ts), UTC), value))
        change = describe_series(points, ctx.started_at, min_absolute_change=query.min_change)
        if change is None:
            return None
        summary = (
            f"{service} {query.label}: {_fmt(change.baseline, query.unit)} before, peaked at "
            f"{_fmt(change.peak, query.unit)} at {change.peak_at:%H:%M:%S}"
        )
        if change.changed_at is not None:
            summary += f" (rose from {change.changed_at:%H:%M:%S})"
        elif not change.changed:
            summary += " (no clear change)"
        return EvidenceItem(
            kind=EvidenceKind.METRIC,
            summary=summary,
            observed_at=change.changed_at or change.peak_at,
            dedupe_key=f"{service}:{query.key}",
            payload={
                "service": service,
                "metric": query.key,
                "unit": query.unit,
                "query": query.template.format(service=service),
                "baseline": change.baseline,
                "peak": change.peak,
                "peak_at": change.peak_at.isoformat(),
                "changed": change.changed,
                "points": [[t.isoformat(), v] for t, v in points[-MAX_POINTS:]],
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()
