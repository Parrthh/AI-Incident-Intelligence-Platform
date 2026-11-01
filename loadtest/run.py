"""Load test: post alerts to the webhook at rising rates and measure what happens.

    python loadtest/run.py --url http://127.0.0.1:8000 --token load-test-token \
        --database-url postgresql://iip:iip-dev-only@127.0.0.1:5432/iip --out result.json

Each stage sends alerts at a fixed rate for a fixed time. Requests are sent on a
schedule (open loop), not one after another, so a slow server can't quietly
lower the load it receives (coordinated omission). Every alert has a new
fingerprint, so every request opens an incident, writes an outbox row and
queues a summary job: the most expensive path.

A stage "holds" when the achieved rate is within 5% of the target, there are no
errors, and p95 latency is under the threshold. The run stops after the first
stage that doesn't hold. Then it waits for the worker to drain the backlog and
checks that every incident got exactly one summary.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx

DEFAULT_STAGES = (25, 50, 100, 125, 150, 175, 200, 300)


@dataclass
class Stage:
    target_rps: int
    seconds: float
    sent: int = 0
    accepted: int = 0
    errors: int = 0
    error_kinds: dict[str, int] = field(default_factory=dict[str, int])
    latencies_ms: list[float] = field(default_factory=list[float])
    wall_seconds: float = 0.0

    def summary(self, p95_limit_ms: float) -> dict[str, Any]:
        lat = sorted(self.latencies_ms)

        def pct(q: float) -> float | None:
            if not lat:
                return None
            return round(lat[min(len(lat) - 1, int(q * len(lat)))], 1)

        achieved = self.accepted / self.wall_seconds if self.wall_seconds else 0.0
        p95 = pct(0.95)
        holds = (
            self.errors == 0
            and achieved >= 0.95 * self.target_rps
            and p95 is not None
            and p95 < p95_limit_ms
        )
        return {
            "target_rps": self.target_rps,
            "achieved_rps": round(achieved, 1),
            "sent": self.sent,
            "accepted": self.accepted,
            "errors": self.errors,
            "error_kinds": self.error_kinds,
            "p50_ms": pct(0.50),
            "p95_ms": p95,
            "p99_ms": pct(0.99),
            "max_ms": round(lat[-1], 1) if lat else None,
            "mean_ms": round(statistics.fmean(lat), 1) if lat else None,
            "holds": holds,
        }


def alert_payload(run_id: str, n: int) -> dict[str, Any]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    # A unique service per alert, so correlation never groups them: every request
    # opens an incident and queues a job, the most expensive path.
    labels = {"alertname": "LoadTest", "service": f"svc-{run_id}-{n}", "severity": "warning"}
    alert = {
        "status": "firing",
        "labels": labels,
        "annotations": {"summary": f"load test alert {n}", "description": "synthetic"},
        "startsAt": now,
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://loadtest/",
        "fingerprint": f"{run_id}-{n}",
    }
    return {
        "version": "4",
        "groupKey": f"loadtest:{run_id}:{n}",
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "iip",
        "groupLabels": {"alertname": "LoadTest"},
        "commonLabels": labels,
        "commonAnnotations": {},
        "externalURL": "http://alertmanager.local",
        "alerts": [alert],
    }


async def run_stage(
    client: httpx.AsyncClient, url: str, token: str, stage: Stage, run_id: str, counter: list[int]
) -> None:
    interval = 1.0 / stage.target_rps
    total = int(stage.target_rps * stage.seconds)
    headers = {"Authorization": f"Bearer {token}"}
    tasks: list[asyncio.Task[None]] = []

    async def one(n: int) -> None:
        start = time.perf_counter()
        try:
            response = await client.post(url, json=alert_payload(run_id, n), headers=headers)
        except httpx.HTTPError as exc:
            stage.errors += 1
            kind = type(exc).__name__
            stage.error_kinds[kind] = stage.error_kinds.get(kind, 0) + 1
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        if response.status_code == 202:
            stage.accepted += 1
            stage.latencies_ms.append(elapsed_ms)
        else:
            stage.errors += 1
            kind = str(response.status_code)
            stage.error_kinds[kind] = stage.error_kinds.get(kind, 0) + 1

    began = time.perf_counter()
    for i in range(total):
        # Sleep until this request's slot. If we fall behind, send right away.
        delay = began + i * interval - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        counter[0] += 1
        stage.sent += 1
        tasks.append(asyncio.create_task(one(counter[0])))
    await asyncio.gather(*tasks)
    stage.wall_seconds = time.perf_counter() - began


async def drain(database_url: str, run_id: str, expected: int, within: float) -> dict[str, Any]:
    """Wait until every load-test incident has a finished summary."""
    conn = await asyncpg.connect(database_url)
    try:
        began = time.perf_counter()
        query = """
            SELECT count(*) FILTER (WHERE an.status = 'done') AS done,
                   count(an.id) AS rows,
                   count(DISTINCT i.id) AS incidents
            FROM alerts a
            JOIN incidents i ON i.id = a.incident_id
            LEFT JOIN analyses an ON an.incident_id = i.id
            WHERE a.fingerprint LIKE $1
        """
        while True:
            row = await conn.fetchrow(query, f"{run_id}-%")
            if row is None:  # pragma: no cover - an aggregate always returns a row
                raise RuntimeError("no row from count query")
            done, rows, incidents = int(row["done"]), int(row["rows"]), int(row["incidents"])
            settled = done >= max(expected, incidents) and done == rows
            if settled or time.perf_counter() - began > within:
                break
            await asyncio.sleep(1)
        lag = await conn.fetchrow(
            """
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY d) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY d) AS p95,
                   max(d) AS max
            FROM (
                SELECT extract(epoch FROM an.completed_at - i.created_at) AS d
                FROM alerts a
                JOIN incidents i ON i.id = a.incident_id
                JOIN analyses an ON an.incident_id = i.id AND an.status = 'done'
                WHERE a.fingerprint LIKE $1
            ) t
            """,
            f"{run_id}-%",
        )
        if lag is None:  # pragma: no cover
            raise RuntimeError("no row from lag query")
        return {
            "expected": expected,
            "incidents": incidents,
            "summaries_done": done,
            "summary_rows": rows,
            # Requests that timed out on the client may still have been stored, so
            # loss is counted from what the server stored: incidents without a
            # finished summary. Accepted alerts with no incident would also be loss.
            "lost": (incidents - done) + max(0, expected - incidents),
            "stored_after_client_timeout": max(0, incidents - expected),
            "duplicates": rows - incidents,
            "drain_seconds": round(time.perf_counter() - began, 1),
            "incident_to_summary_p50_s": _round(lag["p50"]),
            "incident_to_summary_p95_s": _round(lag["p95"]),
            "incident_to_summary_max_s": _round(lag["max"]),
        }
    finally:
        await conn.close()


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--stages", default=",".join(map(str, DEFAULT_STAGES)))
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--p95-limit-ms", type=float, default=500)
    parser.add_argument("--drain-timeout", type=float, default=600)
    parser.add_argument("--out", default="loadtest-result.json")
    args = parser.parse_args()

    run_id = f"lt-{uuid.uuid4().hex[:8]}"
    webhook = f"{args.url.rstrip('/')}/v1/webhooks/alertmanager"
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=200)
    timeout = httpx.Timeout(10.0)
    stages: list[dict[str, Any]] = []
    accepted = 0
    counter = [0]
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        for rps in (int(s) for s in args.stages.split(",")):
            stage = Stage(target_rps=rps, seconds=args.seconds)
            await run_stage(client, webhook, args.token, stage, run_id, counter)
            result = stage.summary(args.p95_limit_ms)
            stages.append(result)
            accepted += stage.accepted
            print(json.dumps(result), flush=True)
            if not result["holds"]:
                break
            await asyncio.sleep(5)  # let queues settle between stages

    drained = await drain(args.database_url, run_id, accepted, args.drain_timeout)
    held = [s for s in stages if s["holds"]]
    report = {
        "run_id": run_id,
        "stage_seconds": args.seconds,
        "p95_limit_ms": args.p95_limit_ms,
        "max_sustained_rps": held[-1]["target_rps"] if held else 0,
        "at_max": held[-1] if held else None,
        "stages": stages,
        "pipeline": drained,
    }
    Path(args.out).write_text(json.dumps(report, indent=2))  # noqa: ASYNC240 - one small file at the end
    print(json.dumps(report["pipeline"]), flush=True)
    return 0 if drained["lost"] == 0 and drained["duplicates"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
