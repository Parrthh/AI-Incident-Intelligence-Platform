# AI Incident Intelligence Platform: Progress Log

Claude updates this at the end of every work session, in the same PR as the work or a follow-up `docs:` PR. Newest session entries on top.

## Current status

- **Active milestone:** M0 Foundations
- **Current step:** M0 acceptance check (deploy check verifying `/readyz` on the live demo)
- **Next up:** M1 walking skeleton (Alertmanager webhook, first LLM summary, UI v0)
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
| M0 Foundations | in progress | 2026-10-31 | | |
| M1 Walking skeleton | not started | | | |
| M2 Async pipeline | not started | | | |
| M3 Correlation and enrichment | not started | | | |
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
| 0002 | Redis Streams as queue | planned (M2) | |
| 0003 | Evidence IDs and citation validation | planned (M4) | |
| 0004 | Free-tier hosted demo (Render + Neon + Upstash); limits recorded | accepted | 2026-11-01 |
| 0005 | LLM provider interface (Gemini, Anthropic, Ollama) | planned (M1) | |
| 0006 | pgvector over a dedicated vector DB | planned (M5) | |

## Known tech debt

| Item | Introduced | Plan to fix |
|---|---|---|
| No `uv.lock` committed: package registries are blocked in the dev session, so the lockfile can't be generated there. CI resolves fresh on each run. | M0 PR1 | Generate and commit `uv.lock` once registry access is allowed, then switch CI to `uv sync --locked` |

## Measured metrics

Only real measurements. Every row needs method, environment and commit.

| Date | Metric | Value | How measured | Environment | Commit |
|---|---|---|---|---|---|
| 2026-11-01 | CI wall time on `main` (lint, typecheck, test, build in parallel) | 58 s (slowest job: `build`; test 30 s, typecheck 10 s, lint 9 s) | GitHub check run start/end timestamps | GitHub-hosted `ubuntu-latest` | f2fc9da |
| 2026-11-01 | Multi-arch image publish (amd64 + arm64) | 72 s | `publish` check run duration | GitHub-hosted `ubuntu-latest`, Docker layer cache warm | f2fc9da |
| 2026-11-01 | Render deploy, start to live | 35 s (build 14 s with cache) | Render events `deploy_started` to `deploy_ended` | Render free, Virginia | f2fc9da |
| 2026-11-01 | Container start to serving (incl. migrations against Neon) | 4 s (migrations 2 s) | App log timestamps `migrations_starting` to `Uvicorn running` | Render free, Neon free (us-east-1) | f2fc9da |

### Eval history (from M4)

| Date | Prompt version | Model | Scenarios / snapshots | Top-1 | Top-3 | Citation validity | Cost / incident | p50 latency | Commit |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

### Adoption (from M7, weekly)

| Week of | Stars | Image pulls | Active installs (opt-in) | External issues / PRs | Demo visitors |
|---|---|---|---|---|---|
| | | | | | |

## Session log

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
