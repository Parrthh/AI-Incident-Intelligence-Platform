# AI Incident Intelligence Platform: Setup Guide (owner tasks)

Claude does the development. These are the one-time tasks only Parth can do, because they need his identity, sign-ins or approval. Each one is marked with when it's needed. Dashboards change over time; if a screen doesn't match, look for the equivalent setting.

Rule for every key below: copy it from the provider straight into Render or GitHub secrets (and a password manager). Never paste keys into a chat, an issue, or a commit.

## Status

| Task | Needed by | Status |
|---|---|---|
| GitHub repo created and connected to the Claude session | M0 | done |
| GitHub private noreply email set for commits | M0 | done |
| Repo settings (squash only, delete branches, security features) | M0 | check (see 1) |
| Neon project created (pooled connection string saved) | M0 deploy | done, per Parth (confirm region and Postgres version) |
| Google AI Studio API key created | M0 deploy | to confirm |
| `GEMINI_API_KEY` GitHub Actions secret | M1 | to do |
| Upstash Redis database created | M0 deploy | to do |
| Render account + Blueprint connected | M0 deploy | to do (Claude will ask when `render.yaml` is merged) |
| Branch ruleset on `main` with required checks | after first CI run | to do (Claude will list the check names) |
| Slack workspace + incoming webhook | M1 (optional) | to do |
| Reddit / Hacker News accounts in good standing | M7 | to do |

## 1. GitHub repository settings

Repo → Settings:
- **General → Pull Requests:** allow squash merging only; enable "Automatically delete head branches". (Already set.)
- **Advanced Security / Code security:** enable Dependabot alerts, Dependabot security updates, secret scanning and push protection.
- **Actions → General:** keep Actions enabled. Workflow permissions: "Read repository contents". Workflows request extra permissions individually.
- **Rules → Rulesets** (after the first CI run): a ruleset for `main` that requires a pull request, requires the CI status checks Claude lists, and blocks force pushes and deletion. Leave required approvals at 0 so Claude can merge green PRs.

## 2. GitHub Actions secrets

Repo → Settings → Secrets and variables → Actions → New repository secret.

| Name | Value | Used by |
|---|---|---|
| `GEMINI_API_KEY` | Google AI Studio key | Eval workflow (M4) and optional LLM smoke tests |

## 3. Neon (Postgres)

Project created in the Parrthh organization (suggested name `ai-incident-intelligence-platform`, region AWS US East N. Virginia).
- Use the **pooled** connection string (hostname contains `-pooler`), from the project dashboard → **Connect** → enable "Connection pooling".
- It goes into Render as `DATABASE_URL`.
- pgvector does not need enabling by hand; the first migration runs `CREATE EXTENSION IF NOT EXISTS vector`.
- Tell Claude which Postgres version the project uses so local Docker matches it.

## 4. Google AI Studio (Gemini)

1. aistudio.google.com → **Get API key** → **Create API key** (create a new Google Cloud project if asked).
2. Save it in a password manager; it goes into Render and GitHub secrets as `GEMINI_API_KEY`.
- Keep the project on the free tier (no billing account) so it can't incur charges.
- Optional hardening: Google Cloud Console → APIs & Services → Credentials → the key → API restrictions → restrict to "Generative Language API".
- Free-tier prompts may be used by Google to improve its products. The demo only sends synthetic chaos-test data, so this is acceptable. Self-hosters are told to use a paid key or Ollama for real data.

## 5. Upstash (Redis)

1. Sign in at upstash.com with GitHub or Google.
2. Create a **Redis** database. Name: `ai-incident-intelligence-platform`. Region: **AWS us-east-1** (close to Neon and Render's Virginia region). Free plan.
3. Copy the connection URL that starts with `rediss://` (two s's: it's TLS). It goes into Render as `REDIS_URL`.

## 6. Render (hosting)

Claude will tell you when `render.yaml` is on `main`. Then:
1. Sign in at render.com with GitHub. Give Render access to **only** this repository.
2. Dashboard → **New → Blueprint** → pick `AI-Incident-Intelligence-Platform`.
3. Render shows the service from `render.yaml` and asks for the secret values: paste `DATABASE_URL` (Neon pooled string), `REDIS_URL` (Upstash) and `GEMINI_API_KEY`.
4. Apply. Render builds and deploys; future merges to `main` deploy automatically.
5. Send Claude the service URL (`https://<name>.onrender.com`) so it can be added to the README and the post-deploy smoke test.

No payment method is required for the free plan. If Render ever asks for one, stop and tell Claude so the plan can be revisited.

## 7. Slack (optional, M1)

1. Create a free workspace (for example `iip-dev`) and a channel `#incidents`.
2. api.slack.com/apps → Create New App → From scratch → enable **Incoming Webhooks** → add a webhook to `#incidents`.
3. Put the webhook URL in Render as `IIP_SLACK_WEBHOOK_URL`. Anyone holding the URL can post to the channel, so treat it as a secret.

## 8. Launch accounts (M7)

- Reddit account with some posting history (r/devops, r/sre and r/selfhosted dislike brand-new accounts posting links).
- Hacker News account.
- A link to the repo and demo from your personal website.

## Optional: running it on your Mac

Not needed for development. If you want to try it locally later: install Docker Desktop (or OrbStack), clone the repo, copy `.env.example` to `.env`, and run `make dev`. The README will have exact steps once M0 is merged.
