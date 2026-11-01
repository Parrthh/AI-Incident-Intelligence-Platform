# AI Incident Intelligence Platform: Engineering Standards

These are the rules for how code is written, reviewed and shipped. They exist so the project looks and behaves like production software, and so every decision can be explained in an interview.

## Git workflow

- `main` is always deployable. It's protected: changes go through PRs with green CI.
- Claude opens PRs and squash-merges them once CI is green. Parth reviews merged and open PRs when he wants; anything needing his decision is called out in the PR and the progress log.
- One branch per step or small feature: `m1/webhook-endpoint`, `m2/outbox`, `fix/dup-alerts`.
- Small PRs (ideally under ~400 changed lines). Each PR description says what changed, why, how it was tested, and anything risky.
- Squash merge. The squash commit message follows Conventional Commits:
  - `feat: add alertmanager webhook endpoint`
  - `fix: prevent duplicate alerts on redelivery`
  - `refactor:`, `test:`, `docs:`, `ci:`, `chore:`, `perf:`
- Link PRs to GitHub issues. Use a GitHub Project board (columns: Backlog, This milestone, In progress, Done) to track work.
- Commit author is always Parth (`Parth Kharkar <102367787+Parrthh@users.noreply.github.com>`), with no AI attribution trailers. See `CLAUDE.md`.
- Before merging, Claude reads its own full diff as a reviewer would and fixes what it finds.

## Code style (Python)

- ruff for linting and formatting; pyright (or mypy) in strict-ish mode for `src/`. CI fails on any error.
- Type hints on all function signatures. Pydantic models at every boundary (HTTP, queue messages, LLM output, config).
- Domain logic (`core/`) is pure where possible: no I/O, easy to unit test. I/O lives in `connectors/`, `db/`, `llm/`, `notify/`.
- No bare `except:`. Catch specific exceptions; let unexpected ones propagate to a handler that logs and reports them.
- Every outbound network call has a timeout. No exceptions.
- Constants and thresholds come from config, not magic numbers in code.
- Async code: never call blocking I/O inside async functions; use async clients (`httpx.AsyncClient`, `asyncpg`, `redis.asyncio`).

## Code style (TypeScript / React)

- TypeScript strict mode. ESLint + Prettier in CI.
- API types generated from the FastAPI OpenAPI schema (e.g. `openapi-typescript`) so frontend and backend can't drift.
- Server state via TanStack Query; no hand-rolled fetch-in-useEffect.

## Testing

| Level | What | Tools | Runs |
|---|---|---|---|
| Unit | Pure logic: correlation, redaction, prompt building, scoring, parsers | pytest | Every PR, must be fast |
| Integration | DB, Redis, API endpoints, worker handlers against real Postgres/Redis | pytest + Testcontainers (or CI service containers) | Every PR |
| Failure | Worker crash, Redis outage, LLM errors, duplicate delivery | pytest + docker control | Every PR (keep fast) or nightly |
| End to end | Webhook in → analysis visible via API, with mocked LLM | pytest or Playwright for UI | Every PR on `main` |
| Eval | LLM quality on recorded scenarios | `evals/run.py` | PRs touching prompts/LLM code |
| Load | Throughput and latency | k6 or Locust | Manually per milestone, results recorded |

Rules:
- Every bug fix comes with a test that fails without the fix.
- Tests never call a real LLM or external service (except the eval workflow, deliberately).
- Use recorded fixtures for external payloads.
- Aim for meaningful coverage of `core/` and handlers; don't chase a percentage.

## Observability requirements (for every new feature)

Before a feature is "done", answer:
1. What log lines does it emit, at what level, with which IDs (`request_id`, `incident_id`, `alert_id`)?
2. What metrics does it add (counter for events, histogram for durations, gauge for sizes)?
3. Does it propagate trace context?
4. How would we know if it silently stopped working? (Usually: a metric and an alert.)

Logging rules: JSON to stdout; never log secrets, API keys, full prompts with raw evidence, or user passwords. Log LLM prompt and response sizes and IDs, not contents (contents can be stored in the DB for debugging, after redaction).

## Error handling and resilience

- Classify errors as retryable (timeouts, connection errors, 429, 5xx) or permanent (validation, 4xx other than 429).
- Retries use exponential backoff with jitter and a max attempt count.
- Anything consumed from the queue must be idempotent.
- Degrade gracefully: if a data source or the LLM is down, the incident still exists and the UI says what's missing.

## Security

- Secrets only in environment variables: `.env` locally (gitignored), Render environment settings and GitHub Actions secrets in production. Never in code, logs, images or CI logs.
- Least privilege: GitHub workflows request only the permissions they need; third-party apps (Render, Claude) get access to this repo only.
- All public endpoints over HTTPS. Webhooks authenticated. UI authenticated (from M6).
- Validate all input with Pydantic. Parameterized SQL only (SQLAlchemy does this; never format SQL strings by hand).
- Redact evidence before it reaches any LLM provider.
- Containers run as non-root with minimal base images; scan images in CI.
- Pin dependency versions with a lockfile; Dependabot keeps them updated.

## Database

- Every schema change is an Alembic migration, reviewed in the PR. Never edit the database by hand in production.
- Migrations must be backward compatible with the previous app version during deploys (add columns as nullable first, backfill, then tighten).
- Index anything you filter or join on; check query plans (`EXPLAIN ANALYZE`) for list endpoints.
- Timestamps are `timestamptz` in UTC.

## LLM usage

- Prompts live in `prompts/` as versioned files. Changing a prompt means a new version file, not an in-place edit, once it's been used in production.
- Every analysis stores provider, model, prompt version, token counts, cost, latency and errors.
- Output is validated against a schema; invalid output is retried once with the validation error, then marked failed.
- Prompt or model changes go through the eval gate.

## Architecture Decision Records (ADRs)

Write an ADR for any decision that is hard to reverse or that someone might reasonably question. Store in `docs/adr/NNNN-short-title.md`. Keep them short.

Template:
```markdown
# NNNN: Title

- Status: proposed | accepted | superseded by NNNN
- Date: YYYY-MM-DD

## Context
What problem are we solving? What constraints matter (cost, simplicity, self-hosting, skills)?

## Options considered
1. Option A: pros, cons
2. Option B: pros, cons
3. Option C: pros, cons

## Decision
What we chose, in one or two sentences.

## Consequences
What gets easier, what gets harder, what we'd need to revisit and when.
```

## Definition of done (every PR)

- [ ] CI green (lint, types, tests, build, scans).
- [ ] Tests added or updated for the change.
- [ ] Logs, metrics and traces considered (see above).
- [ ] Docs updated if behavior or config changed (README, `docs/`, `.env.example`).
- [ ] Migration included and backward compatible, if schema changed.
- [ ] ADR written if a significant decision was made.
- [ ] Deployed to the hosted instance and smoke-tested after merge.

## Metrics honesty rules

- A number goes in the progress log only with: the value, how it was measured (command, tool, dataset), the date, the commit SHA, and the environment (runner type, Render instance type, or machine specs).
- Resume bullets use only numbers from the log.
- Report the conditions that matter ("on a t4g.small", "across 30 recorded incidents from 12 scenarios"). Small, honest numbers with clear methodology are more impressive in interviews than big vague ones.

## Writing style for docs, commits and posts

- Plain, direct English. Short sentences.
- No em dashes. Avoid words and patterns that read as AI-written ("delve", "robust", "seamless", "leverage" as a verb, "in today's fast-paced world").
- Lead with what the reader needs: what it is, how to run it, what it can't do yet.
