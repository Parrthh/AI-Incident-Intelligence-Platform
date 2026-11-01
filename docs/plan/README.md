# Project plan

These files describe what the AI Incident Intelligence Platform is, how it's built, and where it stands. They're the source of truth for every Claude session that works on the repo (see `CLAUDE.md` at the root) and for Parth when reviewing.

| File | What it covers |
|---|---|
| [01_PROJECT_OVERVIEW.md](01_PROJECT_OVERVIEW.md) | Problem, users, MVP scope, non-goals, success metrics, who does what |
| [02_ARCHITECTURE.md](02_ARCHITECTURE.md) | Components, data model, API, repo layout, tech stack, hosted demo |
| [03_SETUP_GUIDE.md](03_SETUP_GUIDE.md) | One-time tasks only Parth can do (accounts, keys, connecting Render) |
| [04_MILESTONES.md](04_MILESTONES.md) | M0 to M7: steps, acceptance criteria, metrics, interview questions |
| [05_ENGINEERING_STANDARDS.md](05_ENGINEERING_STANDARDS.md) | Git workflow, testing, security, observability, ADRs, writing style |
| [06_LEARNING_RESOURCES.md](06_LEARNING_RESOURCES.md) | Background reading per milestone |
| [07_PROGRESS_LOG.md](07_PROGRESS_LOG.md) | Current status, open questions, decisions, measured metrics, session log |

## Using these in a Claude Project (optional)

To ask questions about the project in claude.ai, create a Claude Project, paste `CLAUDE.md` into its instructions, and upload the files in this folder. They change as work progresses, so re-upload `07_PROGRESS_LOG.md` (and anything else that changed) every so often.
