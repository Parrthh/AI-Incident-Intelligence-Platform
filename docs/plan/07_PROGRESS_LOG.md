# AI Incident Intelligence Platform: Progress Log

Claude updates this at the end of every work session, in the same PR as the work or a follow-up `docs:` PR. Newest session entries on top.

## Current status

- **Active milestone:** M4 Grounded analysis and eval harness
- **Current step:** M4 step 1 (M3 finished with PRs #25 to #28)
- **Next up:** root-cause hypotheses that cite evidence IDs, validated against the evidence store, and an eval set from the chaos scenarios
- **Blockers:** none
- **Repo:** https://github.com/Parrthh/AI-Incident-Intelligence-Platform
- **Live URL:** https://iip-demo.onrender.com (free tier: sleeps after 15 idle minutes, about 1 minute to wake)

## Open questions for Parth

- Neon: which Postgres major version does the project show? (Local Docker assumes 17 until confirmed.)
- Can you allow the package registries for this session? (See setup guide status table.)
- After the first image publish: is the `iip` package on GHCR public? (Repo → Packages → iip → Package settings → visibility.)

## Environment

- Development: Claude in a cloud session (Linux, Python 3.13 available; project targets Python 3.12)
- Hosting (demo): Render free web service, Neon free Postgres, Upstash free Redis
- LLM (demo): Gemini API free tier
- Chaos tests and evals: GitHub Actions standard runners
- Commit author: Parth Kharkar `<102367787+Parrthh@users.noreply.github.com>`, no AI attribution lines

## Milestone tracker

| Milestone | Status | Started | Finished | Notes |
|---|---|---|---|---|
| Setup (owner tasks) | done | 2026-10-31 | 2026-11-01 | Render connected; Render connector added to the Claude session |
| M0 Foundations | done | 2026-10-31 | 2026-11-01 | |
| M1 Walking skeleton | done | 2026-11-01 | 2026-11-01 | PRs #11 to #15; chaos run forwards real incidents to the live demo |
| M2 Async pipeline | done | 2026-11-01 | 2026-11-01 | PRs #18, #19, #22, #23, #24 |
| M3 Correlation and enrichment | done | 2026-11-01 | 2026-11-01 | PRs #25 to #28; see notes on acceptance item 1 |
| M4 Grounded analysis and evals | not started | | | |
| M5 Incident memory | not started | | | |
| M6 Production hardening | not started | | | |
| M7 Packaging and launch | not started | | | |

## Decisions

| # | Decision | Status | Date |
|---|---|---|---|
| - | Project name "AI Incident Intelligence Platform", code short name `iip` | decided | 2026-10-31 |
| - | Claude develops autonomously; Parth owns and reviews | decided | 2026-10-31 |
| - | $0 hosted demo on free tiers instead of AWS | decided | 2026-10-31 |
| - | Parth is sole commit author, no AI attribution lines | decided | 2026-10-31 |
| 0001 | Python + FastAPI monolith package, separate API and worker entrypoints | accepted | 2026-11-01 |
| 0002 | Redis Streams as queue | accepted | 2026-11-01 |
| 0003 | Evidence IDs and citation validation | accepted | 2026-11-01 |
| 0004 | Free-tier hosted demo (Render + Neon + Upstash); limits recorded | accepted | 2026-11-01 |
| 0005 | LLM provider interface, Gemini first | accepted | 2026-11-01 |
| 0006 | pgvector over a dedicated vector DB | planned (M5) | |
| 0007 | Server-rendered UI first, React when interactivity needs it | accepted | 2026-11-01 |
| 0008 | Transactional outbox for jobs | accepted | 2026-11-01 |
| 0009 | Alert correlation rules | accepted | 2026-11-01 |
| 0010 | Evidence collection (debounce, sources, stable IDs) | accepted | 2026-11-01 |

## Known tech debt

| Item | Introduced | Plan to fix |
|---|---|---|
| No load shedding: past about 125 to 175 alerts/s (runner-dependent) one API process queues requests until clients time out, instead of rejecting quickly with 503 so Alertmanager retries | M2 load test | M6: uvicorn `limit_concurrency` or a rate limit, plus several API processes |
| No `uv.lock` committed: package registries are blocked in the dev session, so the lockfile can't be generated there. CI resolves fresh on each run. | M0 PR1 | Generate and commit `uv.lock` once registry access is allowed, then switch CI to `uv sync --locked` |

## Measured metrics

Only real measurements. Every row needs method, environment and commit.

| Date | Metric | Value | How measured | Environment | Commit |
|---|---|---|---|---|---|
| 2026-11-01 | CI wall time on `main` (lint, typecheck, test, build in parallel) | 58 s (slowest job: `build`; test 30 s, typecheck 10 s, lint 9 s) | GitHub check run start/end timestamps | GitHub-hosted `ubuntu-latest` | f2fc9da |
| 2026-11-01 | Multi-arch image publish (amd64 + arm64) | 72 s | `publish` check run duration | GitHub-hosted `ubuntu-latest`, Docker layer cache warm | f2fc9da |
| 2026-11-01 | Render deploy, start to live | 35 s (build 14 s with cache) | Render events `deploy_started` to `deploy_ended` | Render free, Virginia | f2fc9da |
| 2026-11-01 | Gemini summary latency (smoke test, fixture incident) | 905 ms, 298 input / 110 output tokens | `LLM smoke test` workflow annotation | GitHub runner to Gemini API, gemini-3.5-flash-lite | 07acc6a |
| 2026-11-01 | Chaos `payment_failure`: flag on to incident opened | 210 s | `chaos/run.py` timings (polls every 15 s) | GitHub runner, OTel Demo 3.1.0, alert rules with 3m windows and `for: 1m` | 06b5e5d |
| 2026-11-01 | Chaos `payment_failure`: incident opened to summary done | under 5 s (one poll interval) | `chaos/run.py` timings (polls every 5 s) | same, Gemini gemini-3.5-flash-lite | 06b5e5d |
| 2026-11-01 | Chaos `payment_failure`: flag off to incident resolved | 240 s | `chaos/run.py` timings (polls every 20 s) | same | 06b5e5d |
| 2026-11-01 | Chaos `payment_failure`: incidents opened | 1 (payment only, no false alerts) | `chaos/run.py` result | same, after excluding load-generator and raising threshold to 20% | 06b5e5d |
| 2026-11-01 | Live demo: webhook accepted to summary stored | 1.3 s and 0.96 s (2 incidents) | Render app logs: `request` 202 log line to `summary_done` | Render free (awake), Neon free, Upstash free, Gemini gemini-3.5-flash-lite, chaos run 41316466918 forwarding to the demo | a836dd2 |
| 2026-11-01 | Live demo: Gemini summary call | 820 ms and 825 ms; 280/82 and 282/80 input/output tokens | `summary_done` log fields `latency_ms`, tokens | same | a836dd2 |
| 2026-11-01 | Live demo: webhook response time | 202 ms first request, then 58, 38 and 40 ms | `request` log field `duration_ms` (4 deliveries) | same | a836dd2 |
| 2026-11-01 | Chaos `payment_failure` with the M2 queue: incidents opened | 2 (payment, plus checkout as a downstream symptom); both summarized and resolved | `chaos/run.py` result | GitHub runner, OTel Demo 3.1.0 | a836dd2 |
| 2026-11-01 | Load test: highest webhook rate held (0 errors, achieved within 5% of target, p95 under 500 ms) | 125 alerts/s (2 runs), 175 alerts/s (1 run) | `load.yml` workflow, open-loop stages of 30 s; every alert opens an incident and queues a job; stub LLM | GitHub runner, 4 vCPU, 15 GiB; 125/s runs on AMD EPYC 7763, 175/s run on AMD EPYC 9V45; 1 API process, 1 worker (concurrency 4), Postgres 17, Redis 7.4 in Docker on the same machine as the client | 6a01760, 3220b5e, d73a33f |
| 2026-11-01 | Load test: webhook latency at the highest held rate | p50 23 ms / p95 94 ms and p50 24 ms / p95 107 ms at 125/s; p50 62 ms / p95 227 ms at 175/s | client-side timings in `loadtest/run.py` | same | same |
| 2026-11-01 | Load test: webhook latency at 100 alerts/s | p95 11 to 21 ms across 3 runs | same | same | same |
| 2026-11-01 | Load test: one step past the limit | throughput drops below the target, p95 7 to 31 s, client timeouts; no load shedding yet | stage after the last held one, 3 runs | same | same |
| 2026-11-01 | Load test: alerts lost or summarized twice | 0 lost, 0 duplicates across 51,664 incidents in 3 runs | database check after each run: every load-test incident has exactly one finished summary | same | same |
| 2026-11-01 | Load test: alert stored to summary done (stub LLM) | p50 0.03 to 0.07 s; p95 1.7 to 18.7 s, driven by the backlog in the overload stage | `incidents.created_at` to `analyses.completed_at` | same | same |
| 2026-11-01 | Load test: peak CPU at overload | API 129 to 139% (of one core), worker 101 to 103%, Postgres 44 to 72% | `docker stats` sampled every 2 s | same | same |
| 2026-11-01 | Worker crash test: SIGKILL a worker mid-run with 100 queued jobs | 100/100 summarized, 0 duplicates, 0 pending, 0 dead letters | `tests/integration/test_worker_crash.py` in CI | GitHub runner, real `iip worker` subprocesses, fake LLM 100 ms per call | 85163d9 |
| 2026-11-01 | Noise reduction (alerts per incident) | payment_failure: 2 alerts, 1 incident (4 runs); payment_unreachable: 1 alert, 1 incident (2 runs) | `chaos/run.py` result `alerts` and `incident_count` | GitHub runner, OTel Demo 3.1.0, alert threshold 20% error ratio | 32a6858 to 9d52b72 |
| 2026-11-01 | Evidence collection time per source (one incident) | alerts 11 to 18 ms, deploys 13 to 17 ms, metrics 18 to 38 ms, logs 25 to 55 ms, traces 59 to 207 ms | `duration_ms` on `evidence_collected` timeline events, 4 chaos runs | same, sources in the same Docker network | faf8bdd, 9d52b72 |
| 2026-11-01 | Summary to evidence available | 25 to 65 s (debounce set to 30 s in chaos runs; default 60 s) | `chaos/run.py` `seconds_to_evidence` | same | faf8bdd, 9d52b72 |
| 2026-11-01 | Load test with correlation (per-service locks) | 125 alerts/s held, p95 19 ms at 100/s, 69 ms at 125/s | `load.yml` | GitHub runner, 4 vCPU AMD EPYC 9V45 | 3cfd864 |
| 2026-11-01 | Container start to serving (incl. migrations against Neon) | 4 s (migrations 2 s) | App log timestamps `migrations_starting` to `Uvicorn running` | Render free, Neon free (us-east-1) | f2fc9da |

### Eval history (from M4)

| Date | Prompt version | Model | Scenarios / snapshots | Top-1 | Top-3 | Citation validity | Cost / incident | p50 latency | Commit |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | analyze_v1 | gemini-3.5-flash-lite | 12 / 24 | 38% (service only: 67%) | 38% | 100% | $0 (free tier); 1,627 in / 556 out tokens mean | 1,748 ms | 85fa5d2 |

### Adoption (from M7, weekly)

| Week of | Stars | Image pulls | Active installs (opt-in) | External issues / PRs | Demo visitors |
|---|---|---|---|---|---|
| | | | | | |

## Session log

### 2026-11-01 (M3)
Milestone / step: M3 all steps
Done:
- PR #25 correlation (same fingerprint, same service, or a connected service within 10 minutes; reasons on a new incident timeline; resolution after a 5-minute quiet period; ADR-0009). The load test caught a global lock costing throughput; per-service advisory locks fixed it.
- PR #26 evidence store with stable IDs, `POST /v1/deploys`, debounced enrichment job with timeouts, circuit breakers and "unavailable" notes (ADR-0010). PR #27 Prometheus, Jaeger and Loki sources, log pattern grouping, Loki in the chaos environment. PR #28 `payment_unreachable` scenario, error-ratio baseline fix.
- M3 acceptance: (1) grouping of related services works (payment and checkout become one incident in every chaos run; 3 or more services covered by integration tests only, because no demo flag fires alerts on 3+ services with the current 20% rule; a `cart_failure` scenario was tried and dropped); (2) incidents auto-resolve; (3) metrics, logs and traces evidence with stable IDs in the payment_failure run; payment_unreachable has no log evidence (the down service writes no logs); (4) with Loki stopped, the next collection records "logs unavailable" and keeps the other evidence; (5) correlation has table-driven unit tests.
- Known weakness: metric evidence is noisy in the demo because payment traffic is low (about 0.1 requests/s). Trace groups overlap across services.
Next: M4 (grounded analysis and the eval harness).

### 2026-11-01 (M2)
Milestone / step: M2 PR A and PR B, M1 acceptance
Done:
- PR #18 Redis Streams queue with backoff retries and a dead-letter stream (ADR-0002). PR #19 transactional outbox, queue-driven summaries with per-incident claims, BackgroundTasks removed (ADR-0008). CI now annotates failing tests on the PR.
- Parth added the `IIP_DEMO_WEBHOOK_TOKEN` secret. Chaos run 41316466918 forwarded alerts to the live demo: 2 incidents opened, summarized and resolved there. M1 acceptance met.
- Note: this run opened a second incident for checkout (a symptom of the payment failure). Earlier runs opened only payment. M3 correlation should group them.
- PR #22 `iip dlq` and `iip queue` commands; a test that SIGKILLs a real worker process mid-run (100/100 recovered, 0 duplicates). PR #23 Prometheus `/metrics` and a provisioned Grafana dashboard. PR #24 load test workflow and concurrent job handling (`IIP_WORKER_CONCURRENCY`, default 4).
- Load test findings: the webhook held 125 alerts/s on EPYC 7763 runners and 175/s on an EPYC 9V45 runner, with no loss or duplicates. Past that, latency climbs to seconds because nothing sheds load; the API process is CPU-bound (about 1.3 cores). Recorded as tech debt for M6. Runner hardware varies between runs, so every number lists its CPU.
- M2 acceptance: webhook does no slow work; worker and Redis failures lose nothing (crash test, outbox test with Redis down); retries and DLQ; `/metrics` and dashboard (`docs/images/load-test-grafana.png`); ADR-0002 and ADR-0008. The plan's "stop Redis for 30 s" is covered by the Redis-down-at-ingest test rather than a timed outage.
Next: M3 (correlation and enrichment).
- UI pass for non-engineer visitors (Parth: a recruiter or anyone should understand the project): where incidents come from, short definitions, milestone descriptions, summary context. Prompt `summary_v1` (no raw timestamps, no "only affected service" claims, plain-language impact). No eval gate exists yet (M4), so v1 was checked by reading its output on the live demo. Chaos runs now Mon, Wed, Fri.

### 2026-11-01 (M1)
Milestone / step: M1 steps 1 to 10
Done:
- PR #11 alert ingestion (idempotent upsert, `xmax` insert detection, resolved is final); PR #12 Gemini summaries (provider interface, redaction, versioned prompt, analyses table, live smoke test); PR #13 read API and server-rendered UI (ADR-0007); PR #14 chaos environment on GitHub Actions; PR #15 fake test credentials built at runtime (GitHub flagged a made-up key; Parth to close the alert as "used in tests").
- First chaos run found two rule problems: the load generator alerted on its own latency, and a 5% error threshold flapped on background payment errors (around 7%). Fixed by excluding load-generator and raising the threshold to 20% with `keep_firing_for: 1m`. Second run: one incident, summarized, resolved.
- Real Alertmanager payloads captured and committed as fixtures; home page redesigned for linking from a resume.
Metrics captured:
- See Measured metrics (Gemini latency, chaos timings).
Next:
- Live demo data once the `IIP_DEMO_WEBHOOK_TOKEN` secret exists; then M2.

### 2026-11-01 (deploy)
Milestone / step: M0 steps 12 and 13
Done:
- Parth connected the Render Blueprint and the Render connector.
- First deploys failed and were fixed with the Render logs:
  - Exit 127: the shell start command was mangled by Render's command parsing. Fixed with `iip api --migrate` (PR #7), and CI now starts the image with the exact command from `render.yaml`.
  - Exit 3: `REDIS_URL` was not a `rediss://` URL. Settings now reject wrong schemes at startup with a message that never includes the value (PR #8). Parth corrected the value.
  - `/readyz` 503 with `AuthenticationError` from Upstash: wrong password in the URL; Parth re-copied it.
  - `/version` reported `unknown` on Render: the image's empty `IIP_GIT_SHA` hid `RENDER_GIT_COMMIT`. Fixed in PR #8.
- Deploy check enabled (`DEMO_URL` set). Zero-downtime handover confirmed in logs: the new instance served before the old one shut down cleanly.
Metrics captured:
- CI, publish, deploy and startup timings (see Measured metrics).
Next:
- Confirm the deploy check passes on this commit, then start M1.

### 2026-11-01
Milestone / step: M0 steps 1 to 11, 13, 14
Done:
- PR #1: project skeleton, settings, `/healthz` `/readyz` `/version`, JSON logging with request IDs, ruff/pyright/pytest, CI.
- PR #3: Postgres and Redis connections, Alembic migrations with an advisory lock, pgvector migration, Dockerfile, dev compose, CI service containers and image build job.
- This PR: worker heartbeat and `iip worker`, in-API worker for the demo, `render.yaml`, GHCR publish workflow, deploy check workflow, ADR-0001, ADR-0004 with verified free-tier limits, README dev quickstart.
Decisions:
- Render health check uses `/healthz`, not `/readyz` (Render restarts on failure). Migrations run at startup. See ADR-0004.
- Upstash's 500K commands/month limit means M2 consumers must use long blocking reads.
Metrics captured:
- None yet (CI duration and deploy time get recorded once the first deploy runs).
Next:
- Parth connects Render; then set `DEMO_URL` and verify the deploy check end to end.
Open questions:
- See "Open questions for Parth".

### 2026-10-31
Milestone / step: setup, M0 kickoff
Done:
- Planned the project and wrote the plan docs.
- Switched hosting from AWS to free tiers so Claude can develop and deploy without Parth in the loop.
- Repo created by Parth and connected; initial commit with `CLAUDE.md`, plan docs, README, license and `.gitignore`.
Decisions:
- See the Decisions table (name, roles, hosting, attribution).
Next:
- M0 steps 1 to 8 as PRs.
Open questions:
- See "Open questions for Parth".

Template:

```
### YYYY-MM-DD
Milestone / step: M_ step _
Done:
- ...
Decisions:
- ...
Metrics captured:
- ...
Next:
- ...
Open questions:
- ...
```
