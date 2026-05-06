# Shadow-Infra

Shadow-Infra mirrors real traffic to temporary "shadow" pods when a GitHub PR is opened, compares responses with Claude, and shows a **Drift Report** UI so you can spot regressions before merging.

## Architecture

```
GitHub PR opened
    → GitHub Webhook → pr-watcher (FastAPI :8000)
    → Parses docker-compose.yaml from the PR branch
    → Spins up a shadow container via docker-compose
    → Registers the deployment in Supabase

Incoming HTTP traffic
    → traffic-splitter (Go tee proxy :8080)
    → 100% → Production upstream (blocking, returned to client)
    → 1%  → Shadow pod (non-blocking goroutine)
    → Both responses stored in Supabase via comparison-agent

comparison-agent (FastAPI :8001)
    → POST /compare receives (prod, shadow) response pair
    → Calls Claude claude-sonnet-4-6 to classify: Safe / Warning / Critical
    → Stores verdict + diff in Supabase

frontend (React + Vite :5173)
    → Lists active PRs and their shadow status
    → Click a PR → Drift Report with side-by-side diff + verdict badges
```

## Services

| Service | Language | Port | Description |
|---|---|---|---|
| `traffic-splitter` | Go 1.21 | 8080 | Tee proxy — 1% mirror to shadow |
| `pr-watcher` | Python / FastAPI | 8000 | GitHub webhook handler |
| `comparison-agent` | Python / FastAPI | 8001 | LLM comparison via Claude API |
| `frontend` | React + Vite | 5173 | Drift Report UI |

## Quick Start

### Prerequisites

- Docker + Docker Compose
- A Supabase project
- A GitHub Personal Access Token (or GitHub App)
- An Anthropic API key

### 1. Clone and configure

```bash
git clone https://github.com/itsgeorgema/shadow-infra
cd shadow-infra
cp .env.example .env
# Edit .env with your real credentials
```

### 2. Start all services

```bash
docker compose up --build
```

Open [http://localhost:5173](http://localhost:5173) for the Drift Report UI.

---

## Action Items (manual steps required)

The following steps cannot be automated and must be completed by you:

### 1. Create a Supabase project and run the schema

1. Go to [supabase.com](https://supabase.com) and create a new project.
2. Open the **SQL Editor** in your project dashboard.
3. Paste and run the contents of `supabase/schema.sql`.
4. Copy your **Project URL**, **anon key**, and **service_role key** into `.env`.

### 2. Configure a GitHub webhook

**Option A — GitHub App (recommended for production):**

1. Create a GitHub App at `https://github.com/settings/apps/new`.
2. Enable **Pull request** events.
3. Set the webhook URL to `https://<your-public-url>/webhook`.
4. Generate a webhook secret and set it as `GITHUB_WEBHOOK_SECRET` in `.env`.
5. Install the app on your target repository.
6. Generate an installation access token and set it as `GITHUB_TOKEN` in `.env`.

**Option B — Repository webhook (simplest for testing):**

1. Go to your repo → Settings → Webhooks → Add webhook.
2. Set Payload URL to `https://<your-public-url>/webhook`.
3. Set Content type to `application/json`.
4. Enter a secret and copy it to `GITHUB_WEBHOOK_SECRET` in `.env`.
5. Select **Pull request** events only.

### 3. Fill in all .env values

Open `.env` (copied from `.env.example`) and populate every variable:

```
SUPABASE_URL          — from Supabase project settings
SUPABASE_ANON_KEY     — from Supabase project settings → API
SUPABASE_SERVICE_KEY  — from Supabase project settings → API (service_role)
GITHUB_WEBHOOK_SECRET — secret you chose when creating the webhook
GITHUB_TOKEN          — GitHub PAT with repo read access (or App installation token)
ANTHROPIC_API_KEY     — from console.anthropic.com
PROD_URL              — URL of your production service (e.g. http://prod-app:8080)
SHADOW_SAMPLE_RATE    — fraction of requests to mirror (default 0.01 = 1%)
VITE_SUPABASE_URL     — same as SUPABASE_URL (exposed to browser)
VITE_SUPABASE_ANON_KEY — same as SUPABASE_ANON_KEY (exposed to browser)
```

### 4. Run `go mod tidy` in traffic-splitter/

The Go service uses only the standard library, so `go.sum` is intentionally empty. Run:

```bash
cd traffic-splitter
go mod tidy
```

This will verify dependencies and populate `go.sum` before building.

### 5. Expose pr-watcher publicly for GitHub webhooks (local dev)

GitHub cannot reach `localhost`. Use [ngrok](https://ngrok.com) or a similar tunnel:

```bash
ngrok http 8000
# Copy the https://xxxx.ngrok.io URL → set as webhook URL on GitHub
```

### 6. Deploy to production

Suggested platforms (all support Docker):

- **[Railway](https://railway.app)** — simplest, import repo and deploy each service
- **[Render](https://render.com)** — Docker support, free tier available
- **[Fly.io](https://fly.io)** — `fly launch` in each service directory

For the traffic-splitter, you will need to configure your DNS / load balancer to route production traffic through it on port 8080.

---

## Development

### Run a single service locally

```bash
# Go traffic splitter
cd traffic-splitter
go run .

# Python services
cd pr-watcher
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

cd comparison-agent
pip install -r requirements.txt
uvicorn main:app --reload --port 8001

# Frontend
cd frontend
npm install
npm run dev
```

### Environment variable reference

| Variable | Used by | Description |
|---|---|---|
| `PROD_URL` | traffic-splitter | URL of the production upstream |
| `SHADOW_URL` | traffic-splitter | URL of the active shadow pod |
| `SHADOW_SAMPLE_RATE` | traffic-splitter | Fraction of requests to mirror (0–1) |
| `COMPARISON_API_URL` | traffic-splitter | URL of comparison-agent |
| `DEPLOYMENT_ID` | traffic-splitter | Supabase deployment ID for this PR |
| `SUPABASE_URL` | all services | Supabase project REST URL |
| `SUPABASE_ANON_KEY` | traffic-splitter, frontend | Public anon key |
| `SUPABASE_SERVICE_KEY` | pr-watcher, comparison-agent | Service-role key (full access) |
| `GITHUB_WEBHOOK_SECRET` | pr-watcher | HMAC secret for signature verification |
| `GITHUB_TOKEN` | pr-watcher | GitHub token for GitHub API calls |
| `ANTHROPIC_API_KEY` | comparison-agent | Anthropic API key for Claude |
| `VITE_SUPABASE_URL` | frontend | Supabase URL (Vite public env var) |
| `VITE_SUPABASE_ANON_KEY` | frontend | Supabase anon key (Vite public env var) |

## Project Structure

```
shadow-infra/
├── supabase/schema.sql          — DB tables: shadow_deployments, response_pairs, verdicts
├── traffic-splitter/            — Go tee proxy
│   ├── main.go
│   ├── splitter/proxy.go        — httputil.ReverseProxy + shadow goroutine
│   ├── splitter/config.go       — env-based configuration
│   └── store/supabase.go        — HTTP client for Supabase REST API
├── pr-watcher/                  — GitHub webhook handler
│   ├── main.py                  — FastAPI app, webhook verification, lifecycle
│   ├── manifest_parser.py       — Fetch + parse docker-compose.yaml from GitHub
│   └── shadow_manager.py        — docker-compose up/down for shadow pods
├── comparison-agent/            — LLM verdict service
│   ├── main.py                  — POST /compare endpoint
│   └── agent.py                 — Claude API call with prompt caching
└── frontend/                    — React + Vite + Tailwind
    └── src/
        ├── api.ts               — Supabase JS client + query functions
        ├── types.ts             — TypeScript interfaces
        ├── App.tsx              — Router
        └── components/
            ├── PRList.tsx       — Table of active deployments
            ├── DriftReport.tsx  — Per-PR diff + verdict view
            ├── ResponseDiff.tsx — react-diff-viewer-continued wrapper
            └── VerdictBadge.tsx — Safe/Warning/Critical badge
```
