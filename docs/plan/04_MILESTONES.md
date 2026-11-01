# AI Incident Intelligence Platform: Milestones in Detail

Each milestone has the same structure:

- **Goal:** what exists at the end.
- **Concepts you'll learn:** read about these first (see `06_LEARNING_RESOURCES.md`).
- **Learning spike (optional, for Parth):** a small throwaway experiment that makes the main concept click. Claude does not need these to build; they're there so Parth can learn the concepts behind each PR.
- **Steps:** the build, in order. Each step should end with something runnable and a commit.
- **Acceptance criteria:** the milestone is done only when all of these are true.
- **Measure:** numbers to capture in `07_PROGRESS_LOG.md`.
- **Interview check:** questions Parth should be able to answer out loud when done. Claude's PR descriptions should make these answerable.

Claude works on a branch per step or small group of steps and merges to `main` through a PR with green CI. See `05_ENGINEERING_STANDARDS.md`. Steps marked **(Parth)** need Parth; everything else Claude does on its own.

---

## M0: Foundations

**Goal:** an empty but production-shaped app. A FastAPI service with health checks runs in Docker locally, CI checks every PR, and merging to `main` deploys it to the Render free tier automatically.

**Concepts you'll learn:** Python project layout with uv; 12-factor config (settings from environment variables); structured logging; Docker images (multi-stage builds, non-root user, layer caching); Docker Compose; CI pipelines; GitHub Actions; container registries; Git-driven deploys and infrastructure as code (Render Blueprints); managed Postgres and Redis over TLS; health checks used by a platform to decide whether a deploy succeeded.

**Learning spike (optional, for Parth):** write a 20-line FastAPI app, put it in a Dockerfile, run it, and hit it with curl. Then break the Dockerfile on purpose (copy files in the wrong order) and watch how caching changes rebuild time.

### Steps

1. **Project skeleton**
   - `uv init --package` (or create `pyproject.toml` by hand), `src/iip/` layout as in `02_ARCHITECTURE.md`.
   - Add dependencies: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `structlog` (or stdlib logging with a JSON formatter).
   - Dev dependencies: `ruff`, `pyright` (or `mypy`), `pytest`, `pytest-asyncio`, `httpx`.
2. **Config.** `src/iip/config.py` using `pydantic-settings`. Every setting comes from env vars with a `IIP_` prefix. Fail fast at startup if a required setting is missing. Commit `.env.example`.
3. **App and health checks.** `GET /healthz` returns 200 if the process is alive. `GET /readyz` returns 200 only when dependencies are reachable (for now, none; add DB and Redis checks in step 6).
4. **Logging.** JSON logs to stdout, one line per event, with timestamp, level, message, and a `request_id` added by middleware (generate one if the request has no `X-Request-ID` header, and return it in the response).
5. **Quality gates.**
   - Configure ruff (lint + format) and pyright in `pyproject.toml`.
   - Write the first tests: health endpoints, request ID header.
   - Add `.pre-commit-config.yaml` running ruff and basic hygiene hooks. `pre-commit install`.
6. **Docker and Compose.**
   - Multi-stage `Dockerfile`: builder stage installs deps with uv; runtime stage is slim, runs as a non-root user, has a `HEALTHCHECK`.
   - `deploy/compose/docker-compose.dev.yml`: `api`, `postgres` (a `pgvector/pgvector` image matching Neon's Postgres major version), `redis`. Named volumes for data.
   - Add SQLAlchemy + `asyncpg` + Alembic. `alembic init migrations`. Make `/readyz` check Postgres and Redis.
   - `make dev` (or a `justfile`) to bring everything up with one command.
7. **CI (`.github/workflows/ci.yml`)**, on every PR and push to `main`:
   - Jobs: `lint` (ruff), `typecheck` (pyright), `test` (pytest with Postgres and Redis as service containers), `build` (docker buildx build, no push on PRs).
   - Cache uv and Docker layers.
   - After the first run, add these job names as required status checks in the `main` ruleset.
8. **Container registry.** On push to `main`, build and push the image to GHCR as `ghcr.io/parrthh/iip:<git-sha>` and `:main`. Build multi-arch (`linux/amd64,linux/arm64`) so ARM servers and Apple Silicon users can run it.
9. **Verify free-tier limits.** Check current free-tier terms for Render (web services, sleep behavior, build minutes), Neon (storage, compute hours, branching, restore window) and Upstash (commands per day, storage, connections). Record them with links and the date in `docs/adr/0004-free-tier-hosted-demo.md`. If any limit breaks the plan, stop and raise it with Parth.
10. **Demo entrypoint.** `IIP_RUN_WORKER_IN_API=true` makes the API process also start the worker loop (a no-op loop in M0 that only logs a heartbeat, real work from M2). Separate entrypoints stay the default for self-hosting.
11. **Render Blueprint.** `render.yaml` with one free web service (Docker runtime, built from the repo Dockerfile), health check path `/healthz` (Render restarts on failure, so it must be liveness; see ADR-0004), `autoDeploy` limited to commits that pass CI, a pre-deploy command that runs migrations (if the free tier doesn't support pre-deploy commands, run migrations at startup guarded by a Postgres advisory lock, and note it in ADR-0004), non-secret env vars set inline, secret env vars (`DATABASE_URL`, `REDIS_URL`, `GEMINI_API_KEY`) marked so Render asks for them, and the webhook token generated by Render.
12. **(Parth) Connect Render.** Parth creates the Blueprint in Render and pastes the three secrets (see `03_SETUP_GUIDE.md` sections 5 and 6), then sends Claude the service URL.
13. **Post-deploy smoke test.** A GitHub Actions job (on push to `main`, after CI) waits for the new commit to be live (`/version` returns the commit SHA) and then checks `/healthz` and `/readyz`. It fails loudly if the deploy didn't happen.
14. **Docs.** README with what the project is (one paragraph), status, local dev quickstart, and an architecture diagram placeholder. Write `docs/adr/0001-python-fastapi-monolith.md` and `docs/adr/0004-free-tier-hosted-demo.md`.

### Acceptance criteria
- [ ] `make dev` starts API, Postgres and Redis locally; `/readyz` returns 200.
- [ ] A PR with a lint error or failing test cannot be merged.
- [ ] Merging to `main` deploys automatically to Render, and the smoke test confirms `/readyz` returns 200 on the new commit.
- [ ] No secrets in the repo, the image, or CI logs.
- [ ] The hosted demo costs $0, with free-tier limits documented in ADR-0004.

### Measure
- CI duration (cold and cached). Deploy duration from merge to live.

### Interview check
- Why multi-stage Docker builds, and why a non-root user?
- Difference between liveness and readiness checks, and how does Render use the health check during a deploy?
- Where do migrations run during a deploy, and what goes wrong if two instances run them at once?
- What does the demo give up by running the API and worker in one process, and why is that acceptable only for the demo?

---

## M1: Walking skeleton

**Goal:** a real failure in the OpenTelemetry Demo triggers a Prometheus alert, Alertmanager sends it to the platform, the platform stores it, an LLM writes a short summary, and it shows up in a web UI, both locally and deployed.

**Concepts you'll learn:** Prometheus data model and PromQL; alerting rules and `for` durations; Alertmanager routing, grouping and webhooks; webhook security; idempotency and deduplication; Pydantic models for external payloads; calling an LLM API safely (timeouts, retries); basic React data fetching.

**Learning spike (optional, for Parth):** run Prometheus and Alertmanager alone with Docker Compose, write one alert rule that always fires (`vector(1) > 0`), and point Alertmanager at https://webhook.site or a 10-line local Flask/FastAPI app that prints the payload. Read the JSON payload carefully.

### Steps

1. **Run the OpenTelemetry Demo.** Use its Docker Compose quickstart in the chaos environment: a GitHub Actions workflow (`chaos.yml`) on a standard runner, or locally if Docker is available. Query its Prometheus, Jaeger and log backends from scripts. Note which observability backends the current version ships (it has changed between versions) and record it in the progress log.
2. **Find the right metrics.** Through the demo's Prometheus API, find the metrics for request rate, error rate and latency per service (the demo exports span metrics from the OpenTelemetry Collector). Write PromQL for each and save the queries in `deploy/demo-env/queries.md`.
3. **Add Alertmanager and alert rules to the demo environment.** In `deploy/demo-env/`, add an override compose file that adds an Alertmanager container and mounts an alert rules file into the demo's Prometheus. Start with three rules:
   - `HighErrorRate`: error ratio > 5% for 2 minutes, per service.
   - `HighLatencyP95`: p95 latency above a threshold for 2 minutes, per service.
   - `ServiceDown`: no requests for 2 minutes from a normally busy service.
   Every rule gets `service` and `severity` labels and a `summary` annotation.
4. **Trigger a failure.** Use one of the demo's built-in failure feature flags (see its docs) and confirm the alert fires in Prometheus and reaches Alertmanager.
5. **Webhook endpoint.**
   - Pydantic models for the Alertmanager webhook payload (version 4). Validate strictly, but store the raw payload too.
   - `POST /v1/webhooks/alertmanager`, protected by a bearer token from config (compare with `hmac.compare_digest`). Configure Alertmanager's webhook receiver with `http_config.authorization`.
   - Migration for the `alerts` table (see `02_ARCHITECTURE.md`). Insert with `ON CONFLICT (fingerprint, starts_at) DO UPDATE` for status changes (firing → resolved), so redelivery never duplicates.
   - For M1, each new firing alert creates its own incident (correlation comes in M3). Add the `incidents` table.
   - Return `202 Accepted` quickly.
6. **Record fixtures.** Save real Alertmanager payloads from step 4 to `tests/fixtures/alertmanager/`. Tests use these, so they never need the demo running.
7. **First LLM summary.**
   - `llm/providers/base.py` with an `LLMProvider` protocol; implement Anthropic first.
   - Settings: provider, model, timeout (e.g. 30s), max retries (2) with exponential backoff and jitter on 429/5xx/timeouts.
   - Prompt v0: given the alert labels and annotations, write a 3-sentence summary. Store prompt in `prompts/summary_v0.md`.
   - Call it with FastAPI `BackgroundTasks` after the webhook returns. **This is deliberate tech debt:** a crash loses the work. Write it down in the log; M2 fixes it. Understanding why this is fragile is the point of M2.
   - Add the `analyses` table and store model, prompt version, tokens, latency and errors.
8. **Web UI v0.**
   - `web/` with Vite + React + TypeScript + TanStack Query.
   - API endpoints: `GET /v1/incidents`, `GET /v1/incidents/{id}`.
   - Pages: incident list (title, service, severity, status, started time) and incident detail (alerts and summary). Plain but clean styling; no need for polish yet.
   - Serve the built UI from the API container (FastAPI `StaticFiles`).
9. **Tests.** Unit tests for payload parsing; integration tests (Testcontainers Postgres) for the webhook: valid payload stored, duplicate ignored, resolved updates status, bad token rejected with 401, malformed body rejected with 422. Mock the LLM provider.
10. **Deploy.** The LLM key and webhook token are already in Render's environment. Deploy. Use a small script (`scripts/send_fixture.py`) to POST recorded fixtures to the deployed instance, so the live demo has data even without the OTel demo running in the cloud.

### Acceptance criteria
- [ ] Turning on a failure flag in the chaos environment produces an incident with an LLM summary within a few minutes (verified by the chaos workflow).
- [ ] Sending the same webhook twice creates one alert row.
- [ ] Requests without the right token get 401.
- [ ] The deployed site shows incidents created from recorded fixtures.
- [ ] Tests pass in CI without the OTel demo or a real LLM.

### Measure
- Webhook response time (should be well under 100 ms since the LLM is off the request path).
- LLM latency and tokens per summary.

### Interview check
- What does Alertmanager's `group_by` / `group_wait` do, and how does it differ from what the platform's correlation will do?
- How do you make a webhook endpoint idempotent?
- Why is `BackgroundTasks` a bad fit for important work? (Answer fully after M2.)

---

## M2: Async pipeline

**Goal:** all slow work runs in a separate worker process fed by Redis Streams, with retries, a dead-letter queue, graceful shutdown, and proof that no alert is lost when a worker crashes.

**Concepts you'll learn:** message queues vs direct calls; at-least-once vs at-most-once vs exactly-once delivery; consumer groups; acknowledgements and pending entries; visibility timeouts and reclaiming stuck messages; idempotent consumers; exponential backoff with jitter; dead-letter queues; the transactional outbox pattern (why "write to DB then publish" can lose messages); graceful shutdown on SIGTERM; backpressure.

**Learning spike (optional, for Parth):** in a Python REPL with `redis-py`: `XADD` a few messages, create a consumer group, `XREADGROUP` without `XACK`, kill the reader, then use `XPENDING` and `XAUTOCLAIM` to see and reclaim the unacknowledged message. This is the core idea of the whole milestone.

### Steps

1. **Read and decide.** Write `docs/adr/0002-redis-streams-queue.md` comparing Redis Streams, SQS and Kafka for this use case. Include what you give up.
2. **Queue abstraction.** `worker/queue.py` with `publish(stream, payload)`, a consumer loop that reads with `XREADGROUP`, calls a handler, and `XACK`s on success. Messages carry: `id`, `type`, `attempt`, `created_at`, `trace_context`, and the payload.
3. **Transactional outbox.** Add an `outbox` table. The webhook handler inserts the alert and an outbox row in the same DB transaction. A relay loop in the worker reads unsent outbox rows, publishes them to Redis, and marks them sent. Now a Redis outage cannot lose an accepted alert.
4. **Worker entrypoint.** `python -m iip.worker` runs the relay and the consumers. Add it to the compose files as a separate service. In the hosted demo, `IIP_RUN_WORKER_IN_API=true` runs the same loop inside the API process. Remove `BackgroundTasks`.
5. **Retries and DLQ.**
   - On handler failure: do not ack; schedule a retry with exponential backoff and jitter (re-publish with `attempt + 1` and a `not_before` time, or use a delayed sorted set; choose one and justify it in the ADR).
   - Separate retryable errors (timeouts, 429, 5xx) from permanent ones (validation errors). Permanent errors go straight to the DLQ.
   - After `max_attempts` (e.g. 5), move to `<stream>.dlq` with the error attached.
   - A periodic reclaimer uses `XAUTOCLAIM` to take over messages pending longer than the visibility timeout (crashed consumers).
6. **Idempotent handlers.** The analysis handler checks whether a finished analysis already exists for that incident and input hash before calling the LLM. Document why this matters with at-least-once delivery.
7. **Graceful shutdown.** On SIGTERM, stop reading new messages, finish in-flight work (with a deadline), then exit. Set `stop_grace_period` in compose to match.
8. **DLQ tooling.** CLI commands: `iip dlq list`, `iip dlq replay <id>`, `iip dlq purge`. An admin API endpoint can come later.
9. **Metrics.** Add Prometheus metrics: messages processed/failed per stream, handler duration histogram, pending count, DLQ size, outbox lag. Expose `/metrics` on API and worker.
10. **Failure tests (the important part).**
    - Integration test: publish 100 alerts, kill the worker container mid-run, restart it, assert all 100 are processed exactly once in the DB.
    - Test: stop Redis for 30 seconds during ingestion; assert no alerts lost after it returns (outbox drains).
    - Test: LLM provider mock fails 3 times then succeeds; assert one analysis, 4 attempts.
    - Test: permanent error goes to DLQ on the first attempt.
11. **Load test.** Write a k6 or Locust script that posts fixture webhooks at increasing rates. Find the rate where p95 webhook latency degrades or errors appear. Record the environment it ran on (runner type, CPU, RAM).

### Acceptance criteria
- [ ] Webhook handler does no slow work; worker does it all.
- [ ] Killing the worker or Redis mid-run loses zero alerts (proved by automated tests).
- [ ] Failed messages retry with backoff and end up in the DLQ after max attempts.
- [ ] `/metrics` shows queue health; a Grafana dashboard (even basic) visualizes it.
- [ ] ADR-0002 written.

### Measure
- Max sustained ingestion rate (alerts/sec) with zero loss, and p95 webhook latency at that rate.
- Results of the crash tests (e.g. "100/100 processed, 0 duplicates").

### Interview check
- Explain at-least-once delivery and why consumers must be idempotent.
- What problem does the transactional outbox solve? What would happen without it?
- How does your system recover a message whose consumer crashed mid-processing?
- What happens if the LLM provider is down for an hour?

---

## M3: Correlation and enrichment

**Goal:** related alerts are grouped into one incident, and each incident is enriched with relevant logs, traces, deploys and metrics, each stored as citable evidence and shown on a timeline.

**Concepts you'll learn:** the three pillars of observability (metrics, logs, traces) and how they connect through trace IDs and service names; service dependency graphs; time-window correlation; state machines for lifecycle (open → analyzing → analyzed → resolved); querying Prometheus, Loki (LogQL) and Jaeger APIs; log template mining (grouping similar log lines); partial failure handling (one source down should not break enrichment); deploy tracking.

**Learning spike (optional, for Parth):** trigger a failure in the demo, then by hand (curl or a notebook) query Prometheus for the error rate, the log backend for error lines, and Jaeger for failed traces in the same 15-minute window. Try to find the root cause yourself. This manual process is exactly what you're automating, and doing it once shows you which evidence is actually useful.

### Steps

1. **Logs backend.** If the demo doesn't ship Loki, add Loki and route the collector's logs to it in your demo override (or write a `LogSource` for whatever backend the demo uses). Confirm you can query error logs per service.
2. **Dependency graph.** `DependencyGraphSource` using Jaeger's dependencies endpoint, with a fallback to a static `services.yaml`. Cache it (refresh every 10 minutes).
3. **Correlation engine** (`core/correlation.py`), pure functions with no I/O so they are easy to unit test:
   - Inputs: the new alert, open incidents, dependency graph, config (window, quiet period).
   - Rules from `02_ARCHITECTURE.md`. Return a decision: attach to incident X, or create a new incident, with a reason string.
   - Store the reason as a timeline event. It makes the system explainable.
   - Write table-driven unit tests for each rule and edge case (alert arrives after incident resolved, two incidents could both match, flapping alerts).
4. **Incident lifecycle.** Explicit state machine with allowed transitions. Resolution job: periodic task that resolves incidents whose alerts are all resolved and quiet for the configured period.
5. **Debounce analysis.** Don't analyze on every new alert. Wait until the incident has been quiet for ~60 seconds (or a max wait of ~3 minutes) before enqueueing enrichment. Re-analyze if significant new alerts arrive later.
6. **Connectors** (`connectors/`), each with timeouts, retries, and a circuit breaker or at least a fast failure path:
   - `PrometheusSource`: range queries for error rate, latency and saturation of affected services.
   - `LokiSource`: error-level logs for affected services in the window, capped (e.g. 2,000 lines).
   - `JaegerSource`: failed and slowest traces for affected services, capped.
7. **Log pattern grouping.** Use a template-mining library such as `drain3` to collapse thousands of log lines into patterns with counts and first-seen times. Store patterns as evidence, not raw lines.
8. **Evidence store.** Each evidence item gets a short stable ID (`ev_log_3`, `ev_trace_1`, `ev_deploy_1`, `ev_metric_2`), a one-line human summary, the payload, and an `observed_at` time.
9. **Deploy events.** `POST /v1/deploys` endpoint, plus an example GitHub Actions step in the docs showing how users report deploys. For the demo, a chaos scenario can simulate a "bad deploy" event.
10. **Partial failure.** If a connector fails, enrichment continues; record "logs unavailable: timeout" as a timeline event. The analysis prompt (M4) must know what's missing.
11. **UI: incident timeline.** A vertical timeline mixing alerts, deploys, log pattern first-seen times, and metric anomalies. Evidence panel with tabs by kind.
12. **Noise reduction measurement.** Run several failure scenarios and count raw alerts vs incidents.

### Acceptance criteria
- [ ] A single failure that fires alerts on 3 to 4 related services produces one incident, with a recorded reason for each grouping decision.
- [ ] Incidents auto-resolve after the quiet period.
- [ ] Each incident has evidence from at least metrics, logs and traces, with stable IDs.
- [ ] Stopping Loki during enrichment still produces an incident with other evidence and a clear "logs unavailable" note.
- [ ] Correlation logic has thorough unit tests.

### Measure
- Alert-to-incident ratio across scenarios (noise reduction).
- Enrichment duration p50/p95 per connector.

### Interview check
- How do metrics, logs and traces complement each other in debugging?
- How does your correlation decide two alerts are related? Where does it fail?
- What's a circuit breaker and when would you use one here?

---

## M4: Grounded analysis and eval harness

**Goal:** the LLM produces structured, evidence-cited root-cause hypotheses, and an automated eval harness measures their accuracy against known injected failures, running in CI on every prompt or model change.

**Concepts you'll learn:** structured output (JSON schema / tool use) and validation; grounding and citation; prompt versioning; context budgeting (fitting evidence into a token limit by priority); chaos engineering basics; building eval datasets; offline vs online evaluation; metrics like top-k accuracy; LLM-as-judge and validating it against human grades; regression gating in CI; cost accounting.

**Learning spike (optional, for Parth):** take one recorded incident's evidence, paste it into the Claude console with a draft prompt asking for JSON hypotheses with evidence IDs, and iterate by hand 5 to 10 times. Notice which evidence the model relies on and where it makes things up.

### Steps

1. **Output schema** (Pydantic):
   ```
   AnalysisResult:
     summary: str                      # 2 to 4 sentences
     timeline: list[{at, description, evidence_ids}]
     hypotheses: list[{rank, root_cause_service, category, explanation, confidence (0 to 1), evidence_ids, next_steps}]
     missing_information: list[str]
   ```
   `category` is a fixed enum (e.g. `bad_deploy`, `dependency_failure`, `resource_exhaustion`, `network_latency`, `config_error`, `code_bug`, `unknown`). Fixed enums make scoring possible.
2. **Prompt v1** (`prompts/analyze_v1.md`): role, rules ("only cite evidence IDs provided", "say unknown rather than guess", "consider deploys near the start time"), evidence block, schema. Use the provider's structured output / tool-use feature where available, and validate anyway.
3. **Citation validation.** Reject or drop hypotheses citing IDs that don't exist. Track how often this happens; it's a hallucination metric.
4. **Context budget.** Rank evidence by relevance (affected services first, closest to start time, highest counts), then fit into a token budget. Log what was dropped.
5. **Chaos scenario catalog** (`chaos/scenarios/*.yaml`). Aim for 12 to 15 scenarios, each with: name, how to inject (demo feature flag, `docker stop`, `tc netem` latency via a sidecar, CPU stress, a fake bad deploy event plus a config change), duration, and the **ground truth** (`root_cause_service`, `category`).
6. **Scenario runner** (`chaos/run.py`, run by the `chaos.yml` GitHub Actions workflow on demand or weekly): injects the failure, waits, removes it, waits for the platform to finish enrichment, then **records a snapshot**: the alerts and all evidence for the incident, saved as JSON and committed to `evals/datasets/` through a PR. Run each scenario 2 to 3 times for variety.
7. **Offline eval runner** (`evals/run.py`): loads snapshots, runs only the analysis step (no live demo needed), scores results, and writes a report (markdown + JSON) with:
   - Top-1 and top-3 accuracy (service and category both correct).
   - Service-only accuracy.
   - Citation validity rate.
   - Mean confidence when right vs wrong (calibration check).
   - Latency, tokens and cost per incident.
   - Per-scenario breakdown.
8. **Summary quality (optional, careful).** An LLM judge scores summary clarity 1 to 5. Before trusting it, **(Parth)** grades 20 to 30 summaries by hand and the agreement with the judge is measured. Report the agreement number alongside the judge scores.
9. **CI gate** (`.github/workflows/eval.yml`): runs when `prompts/`, `src/iip/llm/`, or the eval code changes. Fails the PR if top-3 accuracy drops more than N points below the stored baseline. Posts the report as a PR comment. Uses the `GEMINI_API_KEY` repo secret (free tier).
10. **Model comparison.** Run the eval across 2 to 3 models (e.g. two Gemini models on the free tier and a small local model via Ollama on the runner; add a Claude model if Parth adds a paid key). Record accuracy vs cost vs latency in a table. This becomes a great README section and blog post.
11. **UI.** Hypotheses ranked with confidence bars; clicking an evidence ID highlights it in the evidence panel; "missing information" shown clearly.
12. **ADR-0003** on evidence IDs and citation validation.

### Acceptance criteria
- [ ] Every hypothesis cites only real evidence IDs (validated in code).
- [ ] At least 12 scenarios with 25+ recorded snapshots.
- [ ] `make eval` produces a report locally; CI runs it on prompt changes and gates on regressions.
- [ ] Model comparison table committed to the repo.

### Measure
- Top-1 / top-3 accuracy, citation validity, cost and latency per incident, for each prompt version and model. Keep a history table in the log so you can show improvement over time (e.g. "prompt v1 → v4 raised top-3 from X% to Y%").

### Interview check
- How do you know your LLM feature works? How did you build ground truth?
- How do you prevent and detect hallucinated root causes?
- Why evaluate offline on snapshots instead of live?
- How did you validate the LLM judge?

---

## M5: Incident memory

**Goal:** each incident shows similar past incidents and what resolved them, and users can give feedback on hypotheses.

**Concepts you'll learn:** embeddings; cosine similarity; vector indexes (HNSW vs IVFFlat) and recall/speed tradeoffs; pgvector; what text to embed (and why raw logs are a bad choice); hybrid filtering (vector similarity plus metadata filters); retrieval evaluation (precision@k); feedback loops.

**Learning spike (optional, for Parth):** embed 20 short incident summaries with any embedding model, compute cosine similarity in numpy, and check whether the nearest neighbors make sense. Then try embedding raw log lines instead and compare.

### Steps

1. **Embedding provider** method on the LLM interface; support a hosted model and an Ollama model. Store the model name with each vector; changing models means re-embedding (write a backfill command).
2. **Migration:** enable `pgvector`, add `incident_embeddings`, create an HNSW index with cosine distance.
3. **What to embed:** a normalized text built from the final analysis (affected services, category, summary, top hypothesis). Write a function for it and test it.
4. **Resolution notes.** Add a field where the user writes what actually fixed the incident when resolving it. Include it in similar-incident results.
5. **Similar incidents API:** `GET /v1/incidents/{id}/similar?k=5`, excluding the incident itself, with a minimum similarity threshold.
6. **Feed similar incidents into analysis.** Include the top 2 to 3 similar past incidents (summary + resolution) in the analysis prompt as extra context, clearly labeled as historical. Re-run the eval to see if accuracy changes. Keep it only if it helps.
7. **Feedback UI.** Buttons on each hypothesis: correct / partially / incorrect, plus an optional note. Store in `feedback`. Add a script that turns confirmed feedback into new eval cases.
8. **Retrieval eval.** Using scenario snapshots, check whether incidents from the same scenario type are retrieved as neighbors. Report precision@3.

### Acceptance criteria
- [ ] Incident detail shows relevant similar incidents with resolution notes.
- [ ] Retrieval precision@3 measured and recorded.
- [ ] Eval run with and without historical context; decision recorded.
- [ ] Feedback stored and exportable to the eval set.

### Measure
- Precision@3 for retrieval. Accuracy change from adding historical context. Similarity query latency.

### Interview check
- What is an embedding and why does cosine similarity work for this?
- Why pgvector instead of Pinecone or another vector DB? (ADR-0006)
- How does HNSW trade recall for speed?

---

## M6: Production hardening

**Goal:** the platform is secure, observable, cost-controlled, reproducible from code, and has documented SLOs and runbooks.

**Concepts you'll learn:** authentication and sessions; password hashing (argon2); RBAC basics; PII and secret redaction; rate limiting; SLIs, SLOs and error budgets; alerting on symptoms; runbooks; backups and restore drills; infrastructure as code (keeping every hosted setting in `render.yaml`); supply chain security (image scanning, dependency audit); caching LLM results; database branching and point-in-time restore.

**Learning spike (optional, for Parth):** in the Neon console, create a branch from the main database, connect to it with `psql`, then delete it. Branching is what makes the restore drill cheap.

### Steps

1. **Authentication.**
   - UI and read APIs require login. Built-in users with argon2-hashed passwords and secure, HTTP-only, SameSite session cookies. First admin is created by a CLI command.
   - Roles: `admin` (settings, DLQ) and `viewer`. Webhooks keep using bearer tokens; support multiple tokens so they can be rotated.
   - The public demo uses a read-only demo account (or an anonymous read-only mode for the demo instance only).
   - OIDC/SSO login can be a post-launch feature if users ask.
2. **Redaction before LLM calls.** A redaction step removes emails, IPs (optionally), tokens, API keys, JWTs, credit-card-like numbers and high-entropy strings from evidence before it enters a prompt. Unit tests with nasty examples. Config to add custom patterns. Log counts of redactions, never the values.
3. **Rate limiting** on webhook and login endpoints (per token / per IP).
4. **LLM cost controls.**
   - Per-incident token budget and a daily spend cap; when exceeded, skip analysis and say so in the UI.
   - Cache: hash of (prompt version, model, evidence set) → stored analysis. Re-analysis with identical inputs is free.
   - Measure cost per incident before and after trimming and caching.
5. **SLOs** (write `docs/slo.md`):
   - Ingestion availability: 99.5% of webhook requests succeed (non-5xx) over 28 days.
   - Analysis latency: 95% of incidents analyzed within 3 minutes of the debounce completing.
   - Freshness: no alert waits in the queue more than 60 seconds.
   Define each SLI as a PromQL query. Add burn-rate alerts.
6. **The platform monitors itself.** Alert rules for the platform's own health (DLQ growing, queue lag, error rate, disk usage, backup failure) routed to Slack. Bonus: point those alerts at the platform itself and write about it.
7. **Dashboards.** Grafana dashboard JSON in `deploy/grafana/` covering pipeline throughput, stage latency, queue depth, LLM tokens and cost, error rates, SLO status.
8. **Tracing.** OpenTelemetry instrumentation for FastAPI, SQLAlchemy, Redis and HTTP clients; propagate trace context through queue messages so one trace spans webhook → worker → LLM call.
9. **Backups and restore drill.** A scheduled GitHub Actions job runs `pg_dump` against Neon weekly and stores the dump as a workflow artifact (short retention). Then actually restore a dump into a fresh Neon branch and verify row counts. Document the steps in `docs/runbooks/restore-db.md` and record how long it took.
10. **Runbooks** in `docs/runbooks/`: DLQ growing, LLM provider outage, free-tier limit reached, demo asleep or failing to wake, high cost alert. Each: symptoms, checks, fixes, escalation.
11. **Infrastructure as code.** Every hosted setting lives in `render.yaml` and is changed only by PR. Document the Neon and Upstash settings (region, plan, extensions) in `docs/self-hosting.md` and ADR-0004 so the demo can be rebuilt from scratch by following them.
12. **Supply chain.** Trivy image scan and `pip-audit` / `npm audit` in CI (fail on high/critical). Enable Dependabot version updates. Pin GitHub Actions to commit SHAs.
13. **Full load test.** Re-run the M2 load test against the deployed instance with the whole pipeline (LLM mocked or using a cheap model) and record results.
14. **ADR** for the paid upgrade path (separate worker service, paid Neon/Upstash): when it would be worth it and the monthly cost.

### Acceptance criteria
- [ ] No unauthenticated access to incident data except the deliberate demo mode.
- [ ] Redaction tests pass; no secrets reach prompts in any test case.
- [ ] SLOs documented with working PromQL; dashboards committed.
- [ ] A backup was restored successfully and the procedure is documented.
- [ ] The hosted demo can be rebuilt from `render.yaml` plus the documented Neon/Upstash settings.
- [ ] CI includes security scanning.

### Measure
- Cost per incident before and after optimization. SLO compliance over the first weeks. Restore time. Load test results on the deployed instance.

### Interview check
- What's an error budget and how would you use it?
- How do you keep sensitive data out of LLM prompts?
- What does keeping infrastructure in `render.yaml` buy you, and what parts of the setup still live outside the repo?
- Walk through your restore procedure.

---

## M7: Packaging and launch

**Goal:** a stranger can install the platform in under 10 minutes, a public demo shows it working, and the launch brings real users whose usage you can measure.

**Concepts you'll learn:** semantic versioning and releases; changelogs; writing docs for strangers; demo data and replay modes; privacy-respecting telemetry; open-source community basics; launch strategy.

### Steps

1. **One-command install.** `docker compose up` from a `deploy/compose/` bundle with sensible defaults, plus clear config for Alertmanager integration. Test it on a clean GitHub Actions runner (scripted) and time it.
2. **Helm chart (optional).** Only if early users run Kubernetes and ask for it.
3. **Demo mode.** A `IIP_DEMO=true` mode that loads recorded scenario data and replays incidents on a loop, so the hosted demo always looks alive.
4. **Docs.**
   - README: what it is, a short GIF or screenshot, quickstart, how it works (architecture diagram), model comparison results, roadmap, contributing.
   - `docs/self-hosting.md`: requirements, configuration reference (every env var), connecting Alertmanager, Loki, Jaeger, choosing an LLM provider or Ollama, upgrading.
   - `docs/privacy.md`: exactly what data goes to the LLM provider and how redaction works.
5. **Releases.** Semver tags (`v0.1.0`), a release workflow that builds multi-arch images tagged with the version, generates a changelog, and publishes a GitHub Release.
6. **Opt-in telemetry.** On first start, telemetry is off unless enabled in config. If enabled, send a weekly anonymous ping: random install ID, version, counts of incidents and alerts (no content, no hostnames). Document it fully. This is how you get honest "active installs" numbers. Host the receiver as a small endpoint on the existing Render service.
7. **Community setup.** Issue templates (bug, feature), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, label a few `good first issue`s, enable GitHub Discussions.
8. **Launch content.**
   - Blog post: "How I evaluated an LLM root-cause analyzer with chaos engineering" (dev.to, Medium, or your site). Include the model comparison.
   - **(Parth)** Show HN post, and posts to r/selfhosted, r/devops, r/sre, r/homelab. Read each community's rules first; lead with the problem and the honest limitations.
   - Short demo video (Claude scripts it, Parth records or approves it).
9. **Listen and iterate.** Claude triages and responds to issues; Parth handles anything needing a personal reply. Respond within a day or two. Track feature requests. Fix install friction first.

### Acceptance criteria
- [ ] A fresh install on a clean machine takes under 10 minutes following only the README.
- [ ] Public demo always shows realistic incidents.
- [ ] v0.1.0 released with multi-arch images.
- [ ] Launch posts published.

### Measure (weekly, in the log)
- GitHub stars, image pulls, opt-in active installs, issues and PRs from others, demo site visitors.

### Interview check
- How did you get users and what did they teach you?
- What would you build next and why?

---

## Backlog (post-launch ideas, pick based on user feedback)

- More sources: Datadog, Grafana Alerting, PagerDuty webhooks, CloudWatch, OpenSearch logs.
- Kubernetes events as evidence (pod restarts, OOMKills).
- Postmortem draft generation.
- Slack app with buttons (acknowledge, give feedback) instead of a plain webhook.
- MCP server exposing incidents so other AI agents can query them.
- OIDC/SSO login.
- Multi-tenant hosted version (only if there's real demand).
