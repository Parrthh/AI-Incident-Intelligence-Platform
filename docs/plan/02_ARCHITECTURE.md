# AI Incident Intelligence Platform: Architecture

This is the target architecture. Early milestones build simplified versions of it; each milestone in `04_MILESTONES.md` says which parts exist at that point. When a decision here changes, write an ADR (`docs/adr/`) and update this file.

## High-level flow

```
                  ┌──────────────────────────── "Victim" system (dev/demo) ───────────────────────────┐
                  │  OpenTelemetry Demo services → Prometheus / Loki / Jaeger                          │
                  │  Alert rules → Alertmanager ─────────────┐        Chaos scripts inject failures    │
                  └──────────────────────────────────────────┼──────────────────────────────────────────┘
                                                             │ webhook (bearer token)
                                                             ▼
┌──────────────────────────────────────── iip (this project) ─────────────────────────────────────────┐
│                                                                                                      │
│  API service (FastAPI)                                                                               │
│   • POST /v1/webhooks/alertmanager  → validate, dedupe, store alert → enqueue "alert.received"      │
│   • POST /v1/deploys                → store deploy event                                             │
│   • REST API for the web UI                                                                          │
│                │                                                                                     │
│                ▼                                                                                     │
│  Redis Streams (queues + consumer groups)                                                            │
│                │                                                                                     │
│                ▼                                                                                     │
│  Worker service                                                                                      │
│   1. Correlator   → attach alert to open incident or open a new one                                  │
│   2. Enricher     → pull logs (Loki), traces (Jaeger), metrics (Prometheus), deploys (Postgres)      │
│   3. Analyzer     → build prompt from evidence → LLM provider → validate structured output          │
│   4. Embedder     → embed incident summary → pgvector                                                │
│   5. Notifier     → Slack                                                                            │
│                │                                                                                     │
│                ▼                                                                                     │
│  Postgres + pgvector (alerts, incidents, evidence, analyses, feedback, embeddings)                   │
│                                                                                                      │
│  Web UI (React) ← REST API                                                                           │
│  /metrics (Prometheus format) for the platform's own health                                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Components

### API service
- FastAPI app. Stateless, horizontally scalable.
- Webhook endpoints authenticate with a bearer token (Alertmanager supports this in `http_config`).
- Writes are idempotent: an alert is identified by its Alertmanager `fingerprint` plus `startsAt`, so re-delivery does not create duplicates.
- Never calls the LLM on the request path. It stores, enqueues, and returns `202 Accepted` quickly.
- Serves the UI's REST API and `/healthz` (liveness), `/readyz` (DB and Redis reachable), `/metrics`.

### Queue: Redis Streams
- Streams: `alerts.received`, `incidents.enrich`, `incidents.analyze`, `incidents.notify`.
- Each stage uses a consumer group so multiple worker replicas share work.
- Delivery is at-least-once, so every handler must be idempotent.
- Failed messages are retried with exponential backoff; after N attempts they move to a dead-letter stream (`*.dlq`) and an alert fires.
- Why Redis Streams over SQS or Kafka: one dependency that self-hosters already know, runs anywhere, and teaches the same concepts (consumer groups, acks, pending entries, redelivery). See ADR-0002 (to write in M2).

### Worker service
Runs the pipeline stages as separate consumers. Stages:

1. **Correlator.** Rules (in order):
   - Same fingerprint as an alert in an open incident → attach.
   - Same service, or a service directly connected in the dependency graph, with an open incident started within the correlation window (default 10 minutes) → attach.
   - Otherwise → open a new incident.
   - Incident resolves when all its alerts are resolved and a quiet period (default 15 minutes) has passed.
2. **Enricher.** For the incident's window (start minus 15 min, to now) and affected services, collect: top error log lines (grouped by pattern), slowest and failed traces, recent deploys, and a few key metric series. Each item is stored as an `evidence` row with a stable ID so the LLM can cite it.
3. **Analyzer.** Builds a prompt from evidence (trimmed to a token budget), calls the LLM through a provider interface, and validates the response against a Pydantic schema. Rejects hypotheses that cite evidence IDs that don't exist. Records model, prompt version, tokens, cost and latency.
4. **Embedder.** Embeds the final incident summary for similarity search.
5. **Notifier.** Posts to Slack on incident open and analysis ready, with a link to the UI.

### Connectors (pluggable)
Interfaces so other backends can be added later:
- `MetricsSource` (Prometheus HTTP API)
- `LogSource` (Loki LogQL API)
- `TraceSource` (Jaeger query API)
- `DependencyGraphSource` (Jaeger dependencies endpoint, or a static YAML file)

### LLM providers (pluggable)
- `LLMProvider` interface with `analyze(prompt, schema) -> result` and `embed(text) -> vector`.
- Implementations: Google Gemini (first, because the hosted demo uses its free tier), Anthropic (Claude), Ollama (local). Provider and model come from config.
- Every call has a timeout, retry with backoff on transient errors, and a per-incident token budget.
- Prompts live in versioned files (`prompts/analyze_v3.md`), and the version is stored with every analysis.

### Web UI
- M1: server-rendered pages (Jinja2) using the same query layer as the JSON API (ADR-0007). React + TypeScript + Vite to be reconsidered in M3 when the timeline needs interactivity.
- Pages: incident list (filter by status, service), incident detail (timeline, alerts, evidence, analysis with clickable citations, similar incidents, feedback buttons), settings (read-only view of config in MVP).
- Built as static files and served by the API container, so self-hosting stays one container plus Postgres and Redis.

### The platform's own observability
- Structured JSON logs with a `request_id` / `incident_id` on every line.
- Prometheus metrics, for example:
  - `iip_alerts_received_total{source}`
  - `iip_incidents_opened_total`
  - `iip_stage_duration_seconds{stage}` (histogram)
  - `iip_alert_to_analysis_seconds` (histogram)
  - `iip_llm_tokens_total{provider,model,direction}`
  - `iip_llm_cost_usd_total{provider,model}`
  - `iip_queue_pending{stream}` and `iip_dlq_messages_total{stream}`
- OpenTelemetry tracing across API → queue → worker stages.
- A Grafana dashboard JSON committed to the repo.

## Data model (Postgres)

Initial tables. Migrations are managed with Alembic.

```
alerts
  id               uuid pk
  source           text            -- 'alertmanager' | 'generic'
  fingerprint      text
  status           text            -- 'firing' | 'resolved'
  alertname        text
  service          text null
  severity         text null
  labels           jsonb
  annotations      jsonb
  starts_at        timestamptz
  ends_at          timestamptz null
  received_at      timestamptz
  incident_id      uuid fk null
  raw              jsonb
  unique (fingerprint, starts_at)

incidents
  id               uuid pk
  title            text
  status           text            -- 'open' | 'analyzing' | 'analyzed' | 'resolved'
  severity         text
  services         text[]
  started_at       timestamptz
  resolved_at      timestamptz null
  created_at, updated_at

incident_events                    -- timeline entries
  id, incident_id fk, at timestamptz, kind text, payload jsonb

evidence
  id               text pk         -- short stable ID like 'ev_log_7f3a' that the LLM cites
  incident_id      uuid fk
  kind             text            -- 'log' | 'trace' | 'metric' | 'deploy' | 'alert'
  summary          text            -- short human-readable line
  payload          jsonb
  observed_at      timestamptz

deploys
  id, service, version, commit_sha, deployed_at, metadata jsonb

analyses
  id               uuid pk
  incident_id      uuid fk
  status           text            -- 'pending' | 'done' | 'failed'
  provider, model, prompt_version  text
  summary          text
  hypotheses       jsonb           -- [{rank, cause, confidence, evidence_ids[], next_steps[]}]
  tokens_in, tokens_out  int
  cost_usd         numeric(10,6)
  latency_ms       int
  error            text null
  created_at

feedback
  id, analysis_id fk, hypothesis_rank int null, verdict text ('correct'|'incorrect'|'partial'),
  note text, created_by text, created_at

incident_embeddings
  incident_id      uuid pk fk
  model            text
  embedding        vector(N)       -- N depends on the embedding model
  created_at

users                               -- from M6
  id, email, password_hash or oidc_subject, role, created_at
```

## API (v1)

```
POST /v1/webhooks/alertmanager        Alertmanager webhook payload       → 202
POST /v1/webhooks/generic             {title, service, severity, ...}    → 202
POST /v1/deploys                      {service, version, commit_sha, at} → 201

GET  /v1/incidents?status=&service=&cursor=
GET  /v1/incidents/{id}               incident + alerts + timeline + evidence + latest analysis
POST /v1/incidents/{id}/reanalyze     → 202
GET  /v1/incidents/{id}/similar?k=5
POST /v1/analyses/{id}/feedback       {hypothesis_rank, verdict, note}

GET  /healthz   /readyz   /metrics
```

Conventions: cursor pagination, RFC 7807 problem-details JSON for errors, ISO 8601 UTC timestamps, OpenAPI schema auto-generated by FastAPI and committed for review.

## Repository layout

```
AI-Incident-Intelligence-Platform/
├── CLAUDE.md             # instructions for Claude sessions
├── render.yaml           # hosted demo infrastructure (Render Blueprint)
├── src/iip/
│   ├── api/              # FastAPI app, routers, request/response schemas
│   ├── worker/           # stream consumers, stage runner
│   ├── core/             # domain logic: correlation, evidence, incident lifecycle
│   ├── connectors/       # prometheus.py, loki.py, jaeger.py, interfaces.py
│   ├── llm/              # providers/, prompts loader, schemas, budget
│   ├── notify/           # slack.py
│   ├── db/               # SQLAlchemy models, session, repositories
│   ├── observability/    # logging, metrics, tracing setup
│   └── config.py         # pydantic-settings
├── migrations/           # Alembic
├── prompts/              # versioned prompt files
├── web/                  # React app
├── evals/                # scenarios/, runner, reports/
├── chaos/                # failure injection scripts and scenario definitions
├── deploy/
│   ├── compose/          # docker-compose files: dev, self-host, chaos env
│   ├── demo-env/         # OpenTelemetry Demo overrides, alert rules
│   └── grafana/          # dashboards
├── docs/
│   ├── plan/             # project plan (this file and siblings)
│   ├── adr/              # architecture decision records
│   ├── runbooks/         # what to do when X breaks
│   └── self-hosting.md
├── tests/                # unit/, integration/, e2e/
├── .github/workflows/    # ci.yml, release.yml, chaos.yml, eval.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Tech stack and why

| Area | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Best LLM and data ecosystem; Parth's strength |
| Packaging | uv | Fast, lockfile-based, simple |
| API | FastAPI + Pydantic v2 | Typed schemas, async, auto OpenAPI |
| DB | Postgres 16 + pgvector | One database for relational data and vectors |
| ORM / migrations | SQLAlchemy 2 + Alembic | Industry standard, explicit migrations |
| Queue | Redis Streams | Consumer groups and acks with one simple dependency |
| Frontend | React + TypeScript + Vite | Simple static build, easy to self-host |
| LLM | Anthropic / Gemini / Ollama behind one interface | Choice for users, local option for privacy |
| Lint / types / tests | ruff, pyright (or mypy), pytest, Testcontainers | Fast feedback, real DB in integration tests |
| Containers | Docker, Docker Compose | Dev parity and the self-hosting format |
| Hosting (demo) | Render (web service), Neon (Postgres + pgvector), Upstash (Redis) | Free tiers, Git-driven deploys, no servers to manage |
| IaC | `render.yaml` Blueprint | All hosted infra described in the repo, changed through PRs |
| CI/CD | GitHub Actions + GHCR | Free for public repos; also runs chaos tests and evals |
| Default LLM (demo) | Gemini API free tier | $0; demo only processes synthetic chaos-test data |

## Hosted demo

The hosted copy is a demo for recruiters and prospective users. Real users self-host with Docker Compose. The demo targets **$0 a month** on free tiers. Free-tier limits change, so M0 verifies each one before relying on it and records it in ADR-0004.

```
Internet → Render web service (HTTPS on *.onrender.com, Docker image)
              ├─ API (FastAPI) + web UI
              └─ worker loop (same process in demo mode, see below)
           → Neon Postgres (pgvector)        DATABASE_URL
           → Upstash Redis (TLS)             REDIS_URL
           → Gemini API (free tier)          GEMINI_API_KEY
```

- **Deploys:** `render.yaml` (a Render Blueprint) defines the service, its env vars and health check. Render auto-deploys every commit on `main` whose checks pass. Parth connects the Blueprint once; after that every infra change is a PR. Migrations run at container start (free instances have no pre-deploy step), protected by an advisory lock. Render's health check uses `/healthz`; the `Deploy check` workflow verifies `/readyz` after each deploy (ADR-0004).
- **API and worker in one process (demo only).** Render's free tier doesn't include background workers, so with `IIP_RUN_WORKER_IN_API=true` the API process also runs the worker loop as an asyncio task. Self-hosted installs run them as separate containers, which is the design the code is built around. The worker code is identical either way; only the entrypoint differs.
- **Sleep and cold starts.** Free Render services sleep after a period of inactivity and wake on the next request, which takes some time. Nothing is lost while asleep: accepted alerts are already in Postgres (outbox) and queued messages wait in Redis.
- **Secrets** live in Render's environment settings (entered by Parth) and GitHub Actions secrets. They never appear in the repo.
- **Demo data.** The OpenTelemetry Demo is far too heavy for free hosting. Chaos runs on GitHub Actions record real incidents as snapshots; demo mode (M7) replays them through the pipeline so the site always has realistic data.
- **Backups.** Neon keeps a short point-in-time restore window on its free tier. A scheduled GitHub Actions job also runs `pg_dump` weekly and stores it as a workflow artifact, so there is a restore path independent of Neon (M6).
- **Upgrade path (ADR in M6):** move to a paid Render instance with a separate background worker, and paid Neon/Upstash plans, once real traffic justifies it. Only `render.yaml` and env vars change.

## Key design decisions to record as ADRs

- ADR-0001: Python + FastAPI monolith package with separate API and worker entrypoints
- ADR-0002: Redis Streams as the queue
- ADR-0003: Evidence IDs and citation validation to ground LLM output
- ADR-0004: Free-tier hosted demo (Render + Neon + Upstash) and API+worker in one process for the demo only
- ADR-0005: LLM provider interface (Gemini, Anthropic, Ollama)
- ADR-0006: pgvector instead of a dedicated vector database
