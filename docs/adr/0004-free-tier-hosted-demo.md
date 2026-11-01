# 0004: Free-tier hosted demo (Render + Neon + Upstash)

- Status: accepted
- Date: 2026-11-01

## Context

The hosted copy is a demo for recruiters and prospective users. Real users self-host. The goals are: $0 a month, no servers for anyone to manage, and deploys driven entirely from the repo so development needs no one logging into dashboards. AWS (the original plan) needed manual account and IAM work and costs money once credits run out.

## Free-tier limits (checked 2026-11-01)

| Service | Limit | Source |
|---|---|---|
| Render web service | Spins down after 15 minutes without inbound traffic; about 1 minute to wake | [Render: Deploy for Free](https://render.com/docs/free) |
| Render | 750 free instance hours per workspace per month; services are suspended for the rest of the month if exceeded. Spun-down services don't use hours | same |
| Render | Free instances only for web services, static sites, Postgres and Key Value: **no background workers**. No persistent disk. Outbound ports 25/465/587 blocked | same |
| Render | Build minutes are limited per month (amount not stated on the page); builds stop for the month if exhausted without a payment method | same |
| Neon | 100 CU-hours per project per month (about 400 hours at 0.25 CU) | [Neon plans](https://neon.com/docs/introduction/plans) |
| Neon | 1 GB storage per project; compute suspends after 5 minutes idle (can't be disabled on Free); 6-hour restore window; 5 GB egress per month | same |
| Upstash Redis | **500K commands per month**, 256 MB data, 10 GB bandwidth, 10K commands per second, 1 database | [Upstash pricing](https://upstash.com/docs/redis/overall/pricing) |

These change; re-check when something behaves unexpectedly.

## Options considered

1. **AWS (EC2 + Compose):** most control, recognizable on a resume, but needs Parth for account and IAM setup and costs roughly $10 to $20 a month after credits.
2. **Vercel + Supabase:** good free tiers, but Vercel can't run a long-lived worker, and Supabase-specific features would make the demo differ from what self-hosters run.
3. **Render (free web service) + Neon (Postgres + pgvector) + Upstash (Redis):** $0, Git-driven deploys from `render.yaml`, runs our own Docker image, standard Postgres and Redis.

## Decision

Option 3, configured as follows:

- **API and worker in one process** (`IIP_RUN_WORKER_IN_API=true`), because the free plan has no background workers. Self-hosted installs keep them separate (ADR-0001).
- **Migrations at startup** (`iip api --migrate`: one process, no shell, so no quoting differences between hosts), since free instances have no pre-deploy step. A transaction-scoped Postgres advisory lock makes concurrent runs safe.
- **Render health check = `/healthz`** (liveness). Render restarts the service when its health check fails, so pointing it at `/readyz` would turn a database outage into a restart loop. It would also run a Postgres and Redis check on every health probe, which could keep Neon from suspending and spending Upstash commands. Readiness is verified from outside by the `Deploy check` workflow after each deploy.
- **Deploy only green commits** (`autoDeployTrigger: checksPass`). Every commit to `main` deploys, so the deploy check can always expect the latest SHA.
- **Same region everywhere:** Render Virginia, Neon and Upstash in AWS us-east-1.
- **Neon pooled endpoint** for the app, with asyncpg statement caching disabled (PgBouncer transaction mode).

## Consequences

- The demo is slow on the first request after 15 idle minutes. Nothing is lost while it sleeps: accepted data is in Postgres and queued messages are in Redis.
- **Redis budget:** 500K commands a month is about 11 per minute around the clock. Workers must use long blocking reads (one `XREADGROUP ... BLOCK` per 30 seconds or more) instead of tight polling, and must not send periodic commands while idle. Because Render sleeps the service when there's no traffic, real usage should stay well under the limit, but M2 must measure it.
- **Neon compute budget:** 100 CU-hours covers about 400 hours a month at the smallest size, which isn't enough to stay awake 24/7. Neon's 5-minute auto-suspend plus Render's sleep keep it within budget as long as nothing pings the database constantly. Uptime monitors must hit `/healthz`, never `/readyz`.
- The 6-hour restore window is short, so M6 adds independent `pg_dump` backups.
- Anything that needs to always be on (uptime SLOs, real customer traffic) needs the paid upgrade: a separate background worker and paid Neon/Upstash plans. Only `render.yaml` and env vars change; M6 writes that ADR.
- Recorded uptime and latency numbers from the demo reflect free-tier cold starts and must be labeled that way.
