# AI Incident Intelligence Platform: Project Overview

Short name in code: `iip` (Python package, CLI, Docker image). Repo: `Parrthh/AI-Incident-Intelligence-Platform`.

## One-line pitch

AI Incident Intelligence Platform is a self-hostable incident copilot: it turns a burst of production alerts into one incident with a timeline, the relevant logs, traces and deploys, ranked root-cause hypotheses backed by evidence, and links to similar past incidents.

## The problem

When something breaks in production, the on-call engineer gets a flood of alerts from different services. Most of the first 15 to 30 minutes goes into the same manual work:

1. Figuring out which alerts belong to the same problem.
2. Opening Grafana, the log search, the trace viewer and the deploy history in separate tabs.
3. Lining up timestamps to see what changed first.
4. Remembering whether this happened before and what fixed it.

Large companies build internal tools for this. Small teams, startups and homelab users mostly don't have anything. The platform aims at them.

## Target users

- **Primary:** small engineering teams (2 to 30 engineers) already running Prometheus and Alertmanager, often on Kubernetes or Docker.
- **Secondary:** homelab and self-hosting enthusiasts who run Prometheus at home.
- **Tertiary (for the portfolio):** hiring managers and interviewers looking at the live demo and repo.

Why self-hosted first: teams are reluctant to send logs to a third-party service. A self-hosted tool with a local model option (Ollama) removes that objection and is the realistic path to real users for a solo developer.

## What the platform does (MVP)

1. **Ingest** alerts from Alertmanager (and a generic JSON webhook), with deduplication.
2. **Correlate** related alerts into a single incident using time windows, fingerprints and the service dependency graph.
3. **Enrich** each incident with context from the time window: error logs, slow or failed traces, recent deploys, key metrics.
4. **Analyze** with an LLM: summary, timeline, ranked root-cause hypotheses where every claim cites a piece of collected evidence, and suggested next steps.
5. **Remember**: find similar past incidents using embeddings.
6. **Notify** via Slack when an incident opens and when analysis is ready.
7. **Learn**: users mark hypotheses right or wrong; this feeds the eval set.

## Non-goals (for now)

- Not a replacement for Prometheus, Grafana, PagerDuty or a log store. The platform reads from them.
- No paging or on-call scheduling.
- No automatic remediation (no restarting services, no rollbacks). Suggestions only.
- No multi-tenant SaaS in the MVP. One install serves one team.
- No support for every observability backend at launch. Start with Prometheus, Alertmanager, Loki and Jaeger, behind interfaces so others can be added.

## The development environment for incidents

To build and evaluate the platform we need a system that actually breaks. We use the **OpenTelemetry Demo** (a microservices e-commerce app maintained by the OpenTelemetry project) as the "victim" system. It emits real metrics, logs and traces, and it includes feature flags for injecting failures. We add our own chaos scripts on top (kill containers, add network latency, burn CPU, ship a bad config).

The demo is too heavy for free hosting, so chaos runs happen on GitHub Actions runners (free for public repos). A workflow starts the demo, injects a failure, lets the platform build the incident, and saves a snapshot of the alerts and evidence for the eval set.

Because we inject the failures ourselves, we know the true root cause of every incident. That ground truth is what lets us measure the platform's accuracy honestly.

## Success metrics

These are the numbers we will measure and, if they turn out well, put on the resume. All must come from real measurements (see `05_ENGINEERING_STANDARDS.md`).

**Quality (from the eval harness, M4 onward)**
- Top-1 and top-3 root-cause accuracy across N injected failure scenarios.
- Evidence validity: percent of cited evidence items that exist and support the claim.
- Alert noise reduction: number of raw alerts vs number of incidents created.

**Performance (from load tests and platform dashboards, M2 and M6)**
- p50 / p95 time from first alert to finished analysis.
- Ingestion throughput (alerts per second) without loss.
- Pipeline reliability: zero lost alerts under worker crashes (verified by test).

**Cost (from platform metrics, M4 and M6)**
- Average LLM cost per incident, and reduction achieved by caching and context trimming.

**Adoption (M7 onward)**
- GitHub stars, Docker image pulls, opt-in active installs, external issues and PRs.

## Resume bullet templates (fill with real numbers only)

- Built an open-source incident intelligence platform that groups Prometheus alerts into incidents and generates evidence-backed root-cause analysis; reached top-3 accuracy of __% across __ injected failure scenarios.
- Designed an async ingestion and analysis pipeline (FastAPI, Redis Streams, Postgres) with retries and dead-letter handling; processed __ alerts/sec with zero loss under worker failures; p95 alert-to-analysis time of __s.
- Cut LLM cost per incident by __% through context trimming and response caching while holding accuracy within __ points.
- Launched as a self-hostable tool; __ GitHub stars and __ active installs in the first __ weeks.

## Who does what

- **Claude** builds it: code, tests, CI/CD, deploy config, docs, PRs. See `CLAUDE.md` at the repo root.
- **Parth** owns it: product direction, accounts and keys, reviewing PRs, launch posts, talking to users.

## Milestones

See `04_MILESTONES.md` for detail. Milestones are done in order; each ends with something deployed.

| Milestone | Theme |
|---|---|
| M0 | Foundations: repo, CI, first deploy |
| M1 | Walking skeleton: alert in, summary out |
| M2 | Async pipeline: queue, worker, retries, DLQ |
| M3 | Correlation and enrichment |
| M4 | Grounded analysis and eval harness |
| M5 | Incident memory (similar incidents) |
| M6 | Production hardening |
| M7 | Packaging and launch |

## Hosting budget

The hosted demo targets $0 a month using free tiers (Render, Neon, Upstash, Gemini API, GitHub Actions). See `02_ARCHITECTURE.md`, "Hosted demo". Self-hosters run everything on their own machines.
