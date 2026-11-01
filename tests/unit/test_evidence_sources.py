"""Prometheus, Jaeger and Loki sources against fake HTTP responses."""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from iip.enrichment.base import EnrichmentContext, SourceUnavailableError
from iip.enrichment.sources.jaeger import JaegerSource
from iip.enrichment.sources.loki import LokiSource
from iip.enrichment.sources.prometheus import PrometheusSource

T0 = datetime(2026, 11, 1, 14, 38, 20, tzinfo=UTC)
CTX = EnrichmentContext(
    incident_id=uuid.UUID(int=1),
    title="payment error rate above 20%",
    services=("payment",),
    started_at=T0,
    window_start=T0 - timedelta(minutes=15),
    window_end=T0 + timedelta(minutes=5),
)


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def matrix(values: list[tuple[datetime, str]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": {}, "values": [[t.timestamp(), v] for t, v in values]}],
        },
    }


async def test_prometheus_describes_how_each_metric_moved() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        queries.append(query)
        if "STATUS_CODE_ERROR" in query:
            minutes = [-3, -2, -1, 0, 1, 2]
            values = ["0", "0", "NaN", "0.6", "1", "1"]
            return httpx.Response(
                200,
                json=matrix(
                    [(T0 + timedelta(minutes=m), v) for m, v in zip(minutes, values, strict=True)]
                ),
            )
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    source = PrometheusSource("http://prom", transport=transport(handler))
    items = await source.collect(CTX)

    assert len(queries) == 3  # error ratio, p95 latency, request rate
    assert 'service_name="payment"' in queries[0]
    [item] = items  # the other two returned no data
    assert item.kind == "metric"
    assert item.dedupe_key == "payment:error_ratio"
    assert item.summary == (
        "payment error ratio: 0% before, peaked at 100% at 14:39:20 (rose from 14:38:20)"
    )
    assert item.observed_at == T0
    assert item.payload["baseline"] == 0
    assert len(item.payload["points"]) == 5  # NaN dropped


async def test_prometheus_retries_a_server_error_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    source = PrometheusSource("http://prom", transport=transport(handler))
    assert await source.collect(CTX) == []
    assert calls == 4  # 1 failure + 1 retry for the first query, then 2 more queries


async def test_a_bad_query_fails_without_retrying() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text='{"error":"parse error"}')

    source = PrometheusSource("http://prom", transport=transport(handler))
    with pytest.raises(SourceUnavailableError, match="HTTP 400"):
        await source.collect(CTX)
    assert calls == 1


async def test_unreachable_source_says_so() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    source = LokiSource("http://loki", transport=transport(handler))
    with pytest.raises(SourceUnavailableError, match="ConnectError: connection refused"):
        await source.collect(CTX)


def span(
    trace: str, span_id: str, pid: str, op: str, minute: int, *, error: str | None = None
) -> dict[str, Any]:
    tags: list[dict[str, Any]] = []
    if error:
        tags = [
            {"key": "error", "value": True},
            {"key": "otel.status_description", "value": error},
        ]
    return {
        "traceID": trace,
        "spanID": span_id,
        "processID": pid,
        "operationName": op,
        "startTime": int((T0 + timedelta(minutes=minute)).timestamp() * 1_000_000),
        "duration": 12_000,
        "tags": tags,
    }


async def test_jaeger_groups_failing_spans_by_service_operation_and_message() -> None:
    processes = {"p1": {"serviceName": "payment"}, "p2": {"serviceName": "checkout"}}
    failed = "Payment request failed. Invalid token."
    traces = [
        {
            "traceID": f"t{n}",
            "processes": processes,
            "spans": [
                span(f"t{n}", "a", "p1", "Charge", n, error=failed),
                span(f"t{n}", "b", "p2", "PlaceOrder", n, error="failed to charge card"),
                span(f"t{n}", "c", "p2", "GetCart", n),  # fine
            ],
        }
        for n in range(3)
    ]
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"data": traces})

    items = await JaegerSource("http://jaeger", transport=transport(handler)).collect(CTX)

    assert seen["service"] == "payment"
    assert json.loads(seen["tags"]) == {"error": "true"}
    assert [i.summary for i in items] == [
        'payment: 3 failing Charge spans, "Payment request failed. Invalid token." '
        "(first at 14:38:20, example trace t0)",
        'checkout: 3 failing PlaceOrder spans, "failed to charge card" '
        "(first at 14:38:20, example trace t0)",
    ]
    assert items[0].payload["trace_ids"] == ["t0", "t1", "t2"]


async def test_loki_turns_lines_into_counted_patterns() -> None:
    ns = lambda m: str(int((T0 + timedelta(seconds=m)).timestamp() * 1e9))  # noqa: E731
    body = {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"service_name": "payment", "detected_level": "error"},
                    "values": [
                        [ns(5), "Payment request failed. Invalid token. user=42"],
                        [ns(1), "Payment request failed. Invalid token. user=7"],
                    ],
                },
                {
                    "stream": {"service_name": "payment", "detected_level": "warn"},
                    "values": [[ns(9), "retrying charge in 200ms"]],
                },
            ],
        },
    }
    query: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        query.update(request.url.params)
        return httpx.Response(200, json=body)

    items = await LokiSource("http://loki", transport=transport(handler)).collect(CTX)

    assert query["query"].startswith('{service_name=~"payment"} |~')
    assert query["limit"] == "2000"
    assert items[0].summary == (
        "payment log pattern seen 2 times since 14:38:21: "
        '"Payment request failed. Invalid token. user=<val>"'
    )
    assert items[0].payload["lines_scanned"] == 3
    assert items[1].summary.startswith("payment log pattern seen once since 14:38:29")
