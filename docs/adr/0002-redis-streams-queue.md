# 0002: Redis Streams for background jobs

- Status: accepted
- Date: 2026-11-01

## Context

Ingesting an alert must be fast and must not depend on the LLM. Summaries (M1), evidence collection (M3) and analysis (M4) run in the background. In M1 they ran as FastAPI `BackgroundTasks`, which lose the job if the process restarts between the webhook response and the LLM call, and can't retry later.

We need jobs that survive restarts, retry with backoff, give up into a place a human can inspect, and can be shared by several worker processes. The hosted demo uses Upstash Redis on the free plan, which allows 500K commands a month (ADR-0004), so an idle worker must be cheap.

## Options considered

1. **Postgres as the queue** (`SELECT ... FOR UPDATE SKIP LOCKED`). One less moving part, but workers must poll, and every poll on Neon keeps the database awake and spends compute hours (100 CU-hours a month on the free plan).
2. **Redis lists** (`BRPOP`). Simple, but a message is gone from Redis the moment a worker takes it. A crash mid-job loses it unless we build a "processing" list and a reaper ourselves.
3. **Redis Streams with a consumer group.** Messages stay in a pending list until acknowledged, with a delivery count and idle time per message. Crash recovery and retries come from `XPENDING` and `XCLAIM`.
4. **A hosted broker** (SQS, RabbitMQ, Kafka). More features, but another account and either cost or setup work, which goes against the "no owner involvement" hosting rule.

## Decision

Option 3. One stream per job kind, one consumer group (`workers`), one consumer name per process.

- Workers read with `XREADGROUP ... BLOCK 30000`: one command per 30 s when idle, instead of constant polling.
- Handlers succeed, raise `PermanentError` (dead-letter now) or raise anything else (retry).
- A failed message is not acknowledged. Every 30 s a sweep (`XPENDING`) reclaims messages whose idle time is past `10 s * 2^(deliveries - 1)` (capped at 10 minutes). `XCLAIM` re-checks the idle time atomically, so two sweepers can't take the same message.
- After 5 deliveries the message is copied to `<stream>.dlq` with the error and delivery count, then acknowledged.
- Streams are trimmed approximately to 10,000 entries.

## Consequences

- Delivery is at least once. Handlers must be idempotent (summaries reuse an existing row for the incident).
- Idle cost: about 2 blocking reads and 2 sweeps a minute, roughly 4 commands a minute or about 175K a month for a worker that never sleeps. The demo's web service sleeps after 15 minutes idle, so real use is lower. This leaves room for the API's own commands under the 500K limit.
- Blocking reads need a socket timeout longer than the block time, so the consumer uses its own Redis client (`create_redis(url, socket_timeout=...)`).
- A message only reaches Redis if the publish succeeds. PR B of M2 adds a transactional outbox in Postgres so a job written with the alert can't be lost when Redis is briefly down.
- The worst-case delay before a job is dead-lettered is about 150 s of backoff plus sweep intervals. Retries inside one delivery (the LLM client's own backoff) are separate and short.
- Redis is now required for background work. Self-hosters already run it (docker compose includes it).
