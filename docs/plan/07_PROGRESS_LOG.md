# AI Incident Intelligence Platform: Progress Log

Claude updates this at the end of every work session, in the same PR as the work or a follow-up `docs:` PR. Newest session entries on top.

## Current status

- **Active milestone:** M0 Foundations
- **Current step:** M0 steps 1 to 5 + CI (PR `m0/project-skeleton`)
- **Next up:** M0 step 6 (Docker, Compose, DB/Redis readiness), then steps 8 to 14
- **Blockers:** none for steps 1 to 8. Step 12 needs Parth to connect Render.
- **Repo:** https://github.com/Parrthh/AI-Incident-Intelligence-Platform
- **Live URL:** (after M0 step 12)

## Open questions for Parth

- Neon: which Postgres major version does the project show? (Local Docker assumes 17 until confirmed.)
- Can you allow the package registries for this session? (See setup guide status table.)

## Environment

- Development: Claude in a cloud session (Linux, Python 3.13 available; project targets Python 3.12)
- Hosting (demo): Render free web service, Neon free Postgres, Upstash free Redis
- LLM (demo): Gemini API free tier
- Chaos tests and evals: GitHub Actions standard runners
- Commit author: Parth Kharkar `<102367787+Parrthh@users.noreply.github.com>`, no AI attribution lines

## Milestone tracker

| Milestone | Status | Started | Finished | Notes |
|---|---|---|---|---|
| Setup (owner tasks) | in progress | 2026-10-31 | | All done except Render (waits for `render.yaml`) |
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
| 0001 | Python + FastAPI monolith package | planned (M0) | |
| 0002 | Redis Streams as queue | planned (M2) | |
| 0003 | Evidence IDs and citation validation | planned (M4) | |
| 0004 | Free-tier hosted demo, API+worker in one process for demo only | planned (M0) | |
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
| | | | | | |

### Eval history (from M4)

| Date | Prompt version | Model | Scenarios / snapshots | Top-1 | Top-3 | Citation validity | Cost / incident | p50 latency | Commit |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

### Adoption (from M7, weekly)

| Week of | Stars | Image pulls | Active installs (opt-in) | External issues / PRs | Demo visitors |
|---|---|---|---|---|---|
| | | | | | |

## Session log

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
