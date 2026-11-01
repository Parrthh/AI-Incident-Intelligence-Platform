# 0010: Evidence collection

- Status: accepted
- Date: 2026-11-01

## Context

M4's root-cause analysis must be grounded: every claim cites a piece of evidence a person can check. M3 collects that evidence: the incident's alerts, recent deploys, and, where connected, metrics, failing traces and error logs. Sources are slow or down sometimes, alerts for one incident arrive over a few minutes, and the same incident may be collected more than once.

## Decision

- **One job per incident, after a debounce.** Ingestion sets `incidents.enrich_after` to one minute out and pushes it back as related alerts join, capped at three minutes after the incident opened. A sweep in the worker turns due incidents into `enrich` jobs through the outbox (ADR-0008). A deploy to a service in an open incident schedules another run.
- **Sources behind one interface** (`EvidenceSource.collect(ctx) -> list[EvidenceItem]`). They run side by side, each with a timeout (20 s) and a circuit breaker (opens after 3 failures in a row, retries after 60 s). Sources never write to the database; the runner does, so they all behave the same under failure.
- **Partial failure is visible.** A failed or timed-out source adds a timeline event ("logs unavailable: timeout") instead of failing the job. M4's prompt will be told what is missing.
- **Short, stable evidence IDs.** `ev_<kind>_<n>`, numbered per incident and kind, assigned under a per-incident advisory lock. Each item has a `dedupe_key` (the deploy's ID, a log pattern), so collecting again updates items in place and never renumbers them.
- **Evidence on the timeline.** Items with their own time (deploys, log patterns first seen, metric changes, traces) are merged into the incident timeline, so a deploy two minutes before the first alert reads in order.

## Consequences

- Analysis can cite `ev_deploy_1` and the citation stays valid after later collection runs.
- The sweep polls Postgres every 10 seconds while the worker runs. On the free demo the worker only runs while the web service is awake.
- The hosted demo can only use sources it can reach. Prometheus, Jaeger and the logs backend of the chaos environment live on a temporary GitHub runner, so the live site gets alerts and deploys as evidence, and the chaos run checks the full set. How to get the rest onto the live site is a follow-up.
