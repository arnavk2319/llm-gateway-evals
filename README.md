# LLM Gateway

An OpenAI-compatible gateway that routes each LLM call to the right model for the task, with authentication, tracing, cost tracking, a unified log stream and a dashboard.

## Capabilities

- **Drop-in OpenAI-compatible API.** Chat completions with streaming; point any OpenAI SDK at the gateway by changing the base URL.
- **Automatic routing.** Picks a model per request from task type, complexity, context size, tool and JSON needs, cost and latency, or pin a specific model when you want one.
- **Role-aware routing for multi-agent workflows.** For example, a reviewer call is routed to a different model family than the one that produced the work.
- **Per-call authentication.** API keys (stored hashed, expiring, revocable, rotatable without downtime, scoped to specific models, with per-key rate limits) and short-lived signed tokens that clients refresh when they expire.
- **Retries, fallbacks and failover.** If a provider errors, times out or returns invalid output, the request moves to the next best model within a retry budget. A rejected provider key fails over to a secondary key, unhealthy providers are taken out of rotation by a circuit breaker, and `Retry-After` is honored.
- **Cost tracking.** Real cost plus a "shadow" cost at list price, so free-tier and local models still produce meaningful numbers.
- **One searchable log stream.** Every event is tagged, timestamped and tied to its agent run and call, so any request can be traced end to end. Filter by source, severity, kind or any tag, open a run to see its calls and events together, and export to CSV, JSON or JSONL.
- **Error reporting.** Failures are reported to Sentry (or a Sentry-compatible server) with the same tags and ids as the log stream, and each log row links back to its Sentry event.
- **Versioned routing policy.** Routing behavior lives in a policy you can update without redeploying.
- **Dashboard.** Cost, latency, errors, fallbacks and recent requests by model, node role and task type, behind a password login, with log search and exports.
- **Deploys to Vercel with Neon Postgres.** Serverless-ready: events are written before each response returns, concurrent cold starts migrate the schema safely, and Sentry events are flushed before the function is frozen.
- **Providers.** Ollama, Groq, OpenRouter and any other OpenAI-compatible API, plus a mock provider for local development and tests.

## Installation

Requirements: Docker, Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
git clone https://github.com/arnavk2319/llm-gateway-evals.git
cd llm-gateway-evals
cp .env.example .env
docker compose up -d --wait postgres
cd gateway && uv sync && uv run uvicorn app.main:app --port 8000
```

Send a request:

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}]}'
```

Run the dashboard in another terminal:

```bash
cd dashboard && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3100
```

Without `DATABASE_URL` the gateway runs with an in-memory event store.

### Authentication

Authentication is off for local development and turns on automatically when keys or a token secret are configured, and on Vercel. To force it, set `GATEWAY_AUTH=required`.

```bash
cd gateway
uv run python scripts/keys.py create --name my-app                  # shown once; only its hash is stored
uv run python scripts/keys.py create --name ci --expires-days 30 --models mock/small --rate-limit 300
uv run python scripts/keys.py list
uv run python scripts/keys.py rotate <key-id> --grace-hours 24      # new key now, old key works for 24h
uv run python scripts/keys.py revoke <key-id>
```

Callers send `Authorization: Bearer <key>`. For short-lived tokens, set `GATEWAY_JWT_SECRET` and mint HS256 tokens in your own backend; when a token expires the gateway answers `401` with `error_description="expired"`, and the client mints a fresh one and retries.

### Providers

The mock provider is on by default, so everything works without API keys. To use real models, set variables in `.env`:

| Provider | Variable | Notes |
|---|---|---|
| Ollama (local) | `OLLAMA_ENABLED=1` | requires a running `ollama serve` |
| Groq | `GROQ_API_KEY` | optional `GROQ_API_KEY_2` for rotation and failover |
| OpenRouter | `OPENROUTER_API_KEY` | optional `OPENROUTER_API_KEY_2` |
| Hide the mock models | `GATEWAY_DISABLE_MOCK=1` | once real providers are configured |

Model ids and prices are defined in `gateway/app/data/seed_policy.yaml`; verify them against each provider before relying on them.

### Error reporting

Set `SENTRY_DSN` (Sentry, or a Sentry-compatible server such as GlitchTip) to report failures. Nothing is sent without it, and prompts and credentials are never included.

### Deploy to Vercel with Neon and Sentry

Create two Vercel projects from this repository, one with root directory `gateway` and one with root directory `dashboard`, and a Neon project in the region closest to your Vercel functions.

| Project | Variable | Value |
|---|---|---|
| gateway | `DATABASE_URL` | Neon **pooled** connection string (`-pooler` host, `?sslmode=require`) |
| gateway | `SENTRY_DSN` | DSN of a Sentry project (optional) |
| gateway | `GROQ_API_KEY`, `OPENROUTER_API_KEY` | provider keys (optional; the mock provider works without them) |
| gateway | `GATEWAY_DISABLE_MOCK` | `1` once real providers are configured |
| dashboard | `DATABASE_URL` or `DATABASE_URL_READONLY` | Neon connection string; prefer a read-only role created with `gateway/scripts/dashboard_role.sql` |
| dashboard | `DASHBOARD_PASSWORD` | admin password, 12+ characters |
| dashboard | `DASHBOARD_SESSION_SECRET` | 32+ random characters (`openssl rand -hex 32`) |

Mark every value as Sensitive. On Vercel the gateway requires authentication automatically, so create its first key against the Neon database:

```bash
cd gateway && DATABASE_URL='<neon connection string>' uv run python scripts/keys.py create --name my-app
```

The dashboard returns `503` until both dashboard variables are set. The circuit breaker and rate limiter are kept per function instance, so on serverless they are approximate across instances.
