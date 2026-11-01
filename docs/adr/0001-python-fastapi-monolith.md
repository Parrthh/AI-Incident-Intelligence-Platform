# 0001: One Python package with separate API and worker entrypoints

- Status: accepted
- Date: 2026-11-01

## Context

The platform has two kinds of work: answering HTTP requests (webhooks, UI API) quickly, and slow background work (enrichment, LLM calls). It's built and run by one developer, self-hosted by small teams, and must stay simple to install. The LLM and data tooling it needs is strongest in Python.

## Options considered

1. **Microservices** (separate repos or services for ingestion, correlation, analysis). Independent scaling and deploys, but many images, network calls between them, and a much harder install for self-hosters. Far more than a project this size needs.
2. **One process doing everything** (web server runs background work in-process). Simplest to run, but slow LLM calls compete with request handling, and a crash loses in-flight work.
3. **One package, two entrypoints**: `iip api` and `iip worker` from the same code and the same Docker image, talking through Postgres and Redis.

## Decision

Option 3. One Python package (`src/iip`), one image, separate `api` and `worker` processes. FastAPI for HTTP, asyncio throughout.

## Consequences

- One image to build, scan and version; self-hosters run the same image twice with different commands.
- API and worker scale and restart independently, and the API stays fast because it never does slow work.
- Shared code (models, config, connectors) has no versioning problems between services.
- Module boundaries inside the package (`api`, `worker`, `core`, `connectors`, `llm`) have to be kept clean by discipline, since nothing physically separates them.
- Where a host can't run a second process (the free demo, ADR-0004), `IIP_RUN_WORKER_IN_API=true` runs the worker loop as a task inside the API process. The code is the same; only the entrypoint differs.
