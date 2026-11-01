# 0008: Transactional outbox for jobs

- Status: accepted
- Date: 2026-11-01

## Context

When an alert opens an incident, two things must happen: the incident is committed to Postgres, and a "summarize this incident" job is queued in Redis (ADR-0002). There is no transaction that covers both systems.

- Commit, then publish: if the process dies or Redis is down between the two, the incident never gets a summary.
- Publish, then commit: if the commit fails, the worker gets a job for an incident that doesn't exist.

## Options considered

1. **Publish after commit and accept rare losses.** Simplest. Loses jobs exactly when things are already going wrong (Redis blip, deploy restart).
2. **Transactional outbox.** Write the job as a row in Postgres in the same transaction as the incident. A relay copies unpublished rows to Redis.
3. **Change data capture** (Debezium reading the Postgres WAL). The textbook large-scale version of option 2, but a whole extra service to run.

## Decision

Option 2.

- `outbox` table: stream, type, payload, `published_at`, attempts, last error. A partial index on unpublished rows keeps the relay's query cheap.
- The webhook calls `enqueue()` before `commit()`, then `relay.notify()`.
- `OutboxRelay` runs in the API process. It publishes when notified, once at startup, and otherwise every 10 minutes. It locks rows with `FOR UPDATE SKIP LOCKED`, so several API instances can run relays without publishing the same row at the same time.
- On a Redis error the relay records it on the row and backs off from 5 s to 5 minutes.
- Published rows are deleted after 7 days.

## Consequences

- An incident committed to Postgres always gets its job, even if Redis was down when the alert arrived. Integration tests cover Redis being down at ingest and a process dying between commit and publish.
- A job can be published twice (Redis accepted it, then the database update failed). Handlers are idempotent, so the cost is one extra queue message.
- The fallback sweep is 10 minutes on purpose. Neon's free plan suspends the database after 5 idle minutes and allows 100 compute hours a month (ADR-0004). Polling every few seconds would keep it awake around the clock.
- Summaries now have one row per incident and prompt version (a unique constraint), with a claim (`claimed_by`, `claimed_at`) so duplicate jobs or a redelivered job after a crash never run the LLM twice for the same incident. See `src/iip/llm/summarize.py`.
