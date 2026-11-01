# AI Incident Intelligence Platform: Learning Resources

This list is for Parth: Claude builds the platform, and these explain the concepts behind each milestone's PRs. Read the section for a milestone while it's in progress, not everything up front. Official docs change, so search for the current page by name if a link moves.

## Core books

- **Site Reliability Engineering** (Google, 2016). Free to read at sre.google/books. The most relevant chapters for the platform:
  - Ch. 4 Service Level Objectives (M6)
  - Ch. 6 Monitoring Distributed Systems (M1, M3)
  - Ch. 10 Practical Alerting (M1)
  - Ch. 12 Effective Troubleshooting (M3, M4: this is what the platform automates)
  - Ch. 14 Managing Incidents (product understanding)
  - Ch. 15 Postmortem Culture (M5)
  - Ch. 22 Addressing Cascading Failures (M2, M3)
- **The Site Reliability Workbook** (Google, 2018). Also free at sre.google/books. Read the SLO and alerting chapters during M6.
- **Designing Data-Intensive Applications, 2nd edition** (Kleppmann and Riccomini, 2026). Paid; check whether Rutgers Libraries gives you O'Reilly Learning access. Read by topic (useful for understanding the PRs Claude opens):
  - Reliability, scalability, maintainability (intro chapters): before M0
  - Encoding and evolution (schemas, backward compatibility): M1, M2
  - Stream processing and messaging: M2
  - Transactions: M2 (outbox pattern)
  - Trouble with distributed systems (timeouts, partial failure): M2, M3

## By milestone

### M0: Foundations
- uv docs (docs.astral.sh/uv): projects, lockfiles, workspaces.
- FastAPI tutorial (fastapi.tiangolo.com), especially "Settings and Environment Variables", "Testing", and "Bigger Applications".
- Docker docs: "Multi-stage builds", "Best practices for writing Dockerfiles", "Docker Compose overview".
- The Twelve-Factor App (12factor.net): read factors III (Config), VI (Processes), XI (Logs).
- GitHub Actions docs: "Understanding GitHub Actions", "Caching dependencies", "Publishing Docker images".
- Render docs: "Blueprints (render.yaml)", "Deploy hooks and health checks", "Free instance types".
- Neon docs: "Connection pooling", "Branching". Upstash docs: "Redis getting started".

### M1: Walking skeleton
- Prometheus docs: "Data model", "Querying basics" (PromQL), "Alerting rules".
- Alertmanager docs: configuration (`route`, `group_by`, `group_wait`, `group_interval`, `repeat_interval`) and "webhook_config" payload format.
- OpenTelemetry Demo docs: architecture, Docker quickstart, and the feature flags used for failure injection.
- Anthropic API docs: Messages API, errors and rate limits, structured output / tool use.
- TanStack Query docs: "Quick Start", "Queries".
- Stripe engineering blog: their post on idempotency keys (a classic explanation of idempotent APIs).

### M2: Async pipeline
- Redis docs: "Redis Streams" introduction, consumer groups, `XREADGROUP`, `XACK`, `XPENDING`, `XAUTOCLAIM`.
- microservices.io: "Transactional outbox" pattern.
- AWS Builders' Library: "Timeouts, retries, and backoff with jitter" and "Avoiding insurmountable queue backlogs". Both are excellent and free.
- k6 docs (grafana.com/docs/k6) or Locust docs: getting started.

### M3: Correlation and enrichment
- OpenTelemetry docs: "Observability primer", "Signals" (traces, metrics, logs).
- Grafana Loki docs: LogQL.
- Jaeger docs: query API and service dependencies.
- drain3 README (log template mining) and the original Drain paper abstract for the idea.
- Martin Fowler: "CircuitBreaker" article.

### M4: Grounded analysis and evals
- Anthropic docs: prompt engineering guide, "Reduce hallucinations", tool use / structured outputs.
- Principles of Chaos Engineering (principlesofchaos.org): one page, read it.
- Articles on LLM evaluation: "LLM-as-a-judge" (and its known biases), building eval sets from production data. Hamel Husain's writing on evals is practical and widely cited.
- Linux `tc netem` basics (for latency injection).

### M5: Incident memory
- pgvector README (GitHub): index types, distance operators, tuning HNSW.
- An introductory article on embeddings and vector search (Pinecone and Weaviate have good free explainers, even though we use pgvector).

### M6: Hardening
- OWASP Cheat Sheet Series: Authentication, Session Management, Password Storage.
- Google SRE Workbook: "Implementing SLOs" and "Alerting on SLOs" (burn-rate alerts).
- Neon docs: "Point-in-time restore" and "Branching" (for the restore drill).
- Trivy docs; GitHub docs on Dependabot and pinning actions.
- OpenTelemetry Python docs: automatic instrumentation for FastAPI, SQLAlchemy, Redis, httpx.

### M7: Launch
- Semantic Versioning (semver.org), Keep a Changelog (keepachangelog.com).
- GitHub docs: issue templates, Discussions, releases.
- Read a few successful Show HN posts for self-hosted tools to see how they describe themselves (problem first, honest limitations, easy demo).

## Habit

After each concept, write 3 to 5 sentences in your own words in `docs/learning-notes.md` in the repo (or a private notes file): what it is, why the platform uses it, one tradeoff. These become your interview answers.
