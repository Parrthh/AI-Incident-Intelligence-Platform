# AI Incident Intelligence Platform

A self-hostable incident copilot. It takes in Prometheus/Alertmanager alerts, groups related alerts into a single incident, gathers the logs, traces, metrics and deploys around it, and uses an LLM to suggest root causes. Every claim it makes points to a piece of collected evidence.

> **Status: early development.** Nothing here is usable yet. The plan and progress are public in [`docs/plan/`](docs/plan/README.md).

## Planned features

- **Alert ingestion** from Alertmanager and a generic JSON webhook, with deduplication
- **Correlation** of related alerts into one incident (time windows, fingerprints, service dependency graph)
- **Enrichment** from Prometheus, Loki and Jaeger around the incident window
- **Root-cause hypotheses** from an LLM, ranked, with citations to specific evidence
- **Similar past incidents** using embeddings (pgvector)
- **Slack notifications**
- **Your choice of model:** Gemini, Claude, or a local model through Ollama, so logs never have to leave your network
- **Measured accuracy:** an evaluation harness scores root-cause accuracy against failures injected into the OpenTelemetry Demo

## How it will run

One Docker image plus Postgres (with pgvector) and Redis, started with `docker compose up`. Setup instructions will be added here as milestones land.

## Project plan

| | |
|---|---|
| [Overview](docs/plan/01_PROJECT_OVERVIEW.md) | Problem, scope, success metrics |
| [Architecture](docs/plan/02_ARCHITECTURE.md) | Components, data model, API |
| [Milestones](docs/plan/04_MILESTONES.md) | Build plan |
| [Progress log](docs/plan/07_PROGRESS_LOG.md) | Current status |

## License

[Apache-2.0](LICENSE)
