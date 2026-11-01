# 0009: Alert correlation rules

- Status: accepted
- Date: 2026-11-01

## Context

Until M3 every alert opened its own incident. In the chaos run, breaking the payment service fired two alerts, one on payment and one on checkout (which calls payment), and the on-call engineer saw two incidents for one problem. Real outages spread further: one bad dependency can trip alerts on several callers within a few minutes.

## Options considered

1. **Alertmanager grouping only** (`group_by`). Already happens, but it groups by labels, not by how services depend on each other, and only within one notification.
2. **Rules on time, service and the dependency graph.** Explainable, cheap, testable as pure functions.
3. **Learned or embedding-based clustering.** Can catch relations the graph misses, but needs data we don't have yet, and its decisions are hard to explain.

## Decision

Option 2, first match wins:

1. Same fingerprint as an alert in an open incident (a flapping alert) joins it.
2. Same service as an open incident that started within the window (default 10 minutes) joins it.
3. A service directly connected in the service graph (caller or callee) to one in an open incident within the window joins it.
4. Otherwise a new incident opens.

When several incidents match, the most recently started one wins. The graph is a YAML `calls` map; one for the OpenTelemetry Demo ships with the package (`IIP_SERVICE_GRAPH=builtin:otel-demo`). Without a graph, only rules 1 and 2 apply.

Every decision is stored on the incident's timeline with a sentence explaining it, for example: "HighErrorRate is on checkout, which is connected to payment in the service graph, and it started within 10 minutes of the incident."

Incidents now resolve when all their alerts have resolved and nothing has fired for a quiet period (default 5 minutes; the plan said 15). `resolved_at` is when the last alert ended. The shorter default lets the free demo resolve incidents before its server sleeps after 15 idle minutes.

## Consequences

- Correlation reads open incidents and then writes, so two alerts arriving at once could both open an incident. A transaction-scoped Postgres advisory lock makes correlation run one at a time. It is held for a few milliseconds per webhook request, which caps ingestion at the speed of one correlation at a time; the load test measures it.
- Candidate incidents are filtered in SQL (`services && ARRAY[...]` with a GIN index, plus the time window), so the lookup stays fast with many open incidents.
- Known misses: two unrelated failures on connected services within the window are merged, and a failure that spreads two hops away opens a second incident. Both show on the timeline. The Jaeger connector (later in M3) can replace the static graph with one built from real traces.
- The ingestion response's `resolved_incident_ids` is now always empty, because resolution happens in the background. `attached_incident_ids` lists incidents that gained alerts.
