# LLM Gateway

An OpenAI-compatible gateway that routes each LLM call to the right model for the task, with authentication, tracing, cost tracking, a unified log stream and a dashboard.

## Capabilities

- **Drop-in OpenAI-compatible API.** Chat completions with streaming; point any OpenAI SDK at the gateway by changing the base URL.
- **Automatic routing.** Picks a model per request from task type, complexity, context size, tool and JSON needs, cost and latency, or pin a specific model when you want one.
- **Role-aware routing for multi-agent workflows.** For example, a reviewer call is routed to a different model family than the one that produced the work.
- **Accounts and self-serve keys.** Sign up in the dashboard (email confirmation, password reset and password change included), create API keys there, and use them directly as the bearer token against the gateway, or as the `api_key` of any OpenAI SDK. Keys are stored hashed, shown once, and can expire, be scoped to specific models, rate-limited and revoked.
- **Per-call authentication.** API keys, plus short-lived signed tokens that clients refresh when they expire, and keys can be rotated without downtime from the command line.
- **Retries, fallbacks and failover.** If a provider errors, times out or returns invalid output, the request moves to the next best model within a retry budget. A rejected provider key fails over to a secondary key, unhealthy providers are taken out of rotation by a circuit breaker, and `Retry-After` is honored.
- **Cost tracking.** Real cost plus a "shadow" cost at list price, so free-tier and local models still produce meaningful numbers.
- **One searchable log stream.** Every event is tagged, timestamped and tied to its agent run and call, so any request can be traced end to end. Filter by source, severity, kind or any tag, open a run to see its calls and events together, and export to CSV, JSON or JSONL.
- **Error reporting.** Failures are reported to Sentry (or a Sentry-compatible server) with the same tags and ids as the log stream, and each log row links back to its Sentry event.
- **Versioned routing policy.** Routing behavior lives in a policy you can update without redeploying. A changed `seed_policy.yaml` is published as a new version on the next start, and every version records its gateway version, release, model count, notes and external references; a token-gated API lists, creates, promotes and rolls back versions.
- **Dashboard.** Cost, latency, errors, fallbacks and recent requests by model, node role and task type, with log search and exports. Each user sees only the calls made with their own keys; admins see everything and can disable users.
- **Deploys on Render or Vercel with Neon Postgres.** Runs as a long-lived web service or serverless: events are written before each response returns where needed, concurrent cold starts migrate the schema safely, and Sentry events are flushed before the function is frozen.
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

Create an account at `/signup` (open on localhost), then create a key under **API keys**.

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

Keys created in the dashboard work the same way as these. Callers send `Authorization: Bearer <key>`, and the OpenAI SDKs do this when you pass the key as `api_key` and the gateway's address plus `/v1` as `base_url`. For short-lived tokens, set `GATEWAY_JWT_SECRET` and mint HS256 tokens in your own backend; when a token expires the gateway answers `401` with `error_description="expired"`, and the client mints a fresh one and retries.

### Providers

The mock provider is on by default, so everything works without API keys. To use real models, set variables in `.env`:

| Provider | Variable | Notes |
|---|---|---|
| Ollama (local) | `OLLAMA_ENABLED=1` | requires a running `ollama serve` |
| Groq | `GROQ_API_KEY` | optional `GROQ_API_KEY_2` for rotation and failover |
| OpenRouter | `OPENROUTER_API_KEY` | optional `OPENROUTER_API_KEY_2` |
| Hide the mock models | `GATEWAY_DISABLE_MOCK=1` | once real providers are configured |

Model ids and prices are defined in `gateway/app/data/seed_policy.yaml`; verify them against each provider before relying on them. Edit the file and deploy: the gateway publishes the change as a new policy version when it starts (it never replaces a version published through the API).

### Error reporting

Set `SENTRY_DSN` (Sentry, or a Sentry-compatible server such as GlitchTip) to report failures. Nothing is sent without it, and prompts and credentials are never included.

### Deploy: gateway on Render, dashboard on Vercel, Neon, Sentry, Resend

Run the same setup per environment (production from `main`, another from `dev`), each with its own Neon branch, Sentry project and secrets.

- **Gateway** (Render web service, root directory `gateway`): build `pip install uv && uv sync --frozen --no-dev`, start `uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/healthz`.
- **Dashboard** (Vercel project, root directory `dashboard`).
- **Database**: create the schema on each Neon branch. Only the gateway connects to it; the dashboard reads and writes through the gateway's `/dashboard/v1` API.

```bash
cd gateway
DATABASE_URL='<neon owner connection string>' uv run python scripts/migrate.py
```

| Where | Variable | Value |
|---|---|---|
| gateway | `DATABASE_URL` | Neon **pooled** owner connection string (`-pooler` host, `?sslmode=require`) |
| gateway | `GATEWAY_AUTH` | `required` |
| gateway | `GATEWAY_DASHBOARD_TOKEN` | 32+ random characters; enables the dashboard API (`openssl rand -hex 32`) |
| gateway | `GATEWAY_ADMIN_HASH_KEY`, `GATEWAY_ADMIN_EMAIL_HASHES` | who may be an admin: a secret key (32+ characters) and the keyed hashes of the admin addresses, made with `scripts/admin.py hash <email>`; unset means nobody is an admin |
| gateway | `GATEWAY_ENVIRONMENT` | `production`, `dev`, ... (shown in Sentry) |
| gateway | `SENTRY_DSN` | DSN of a Sentry project (optional) |
| gateway | `GROQ_API_KEY`, `OPENROUTER_API_KEY` | provider keys (optional; the mock provider works without them) |
| gateway | `GATEWAY_DISABLE_MOCK` | `1` once real providers are configured |
| dashboard | `GATEWAY_URL` | the gateway's public address, e.g. `https://llm-gateway.onrender.com` |
| dashboard | `GATEWAY_DASHBOARD_TOKEN` | the same value as on the gateway |
| dashboard | `DASHBOARD_SESSION_SECRET` | 32+ random characters (`openssl rand -hex 32`) |
| dashboard | `SIGNUP_INVITE_CODE` | 12+ characters; sign-up requires it (`SIGNUP_MODE=open` removes the requirement) |
| dashboard | `APP_URL` | the dashboard's public address, used in emailed links |
| dashboard | `RESEND_API_KEY`, `EMAIL_FROM` | transactional email for address confirmation and password reset |
| dashboard | `GATEWAY_PUBLIC_URL` | the gateway's public address, shown in the key page's code samples |

Mark every value as secret. Open the dashboard, sign up with an admin address and the invite code, create a key under **API keys**, and call the gateway with it. Other users confirm their email address before they can create keys, and can reset a forgotten password by email.

The dashboard returns `503` until `DASHBOARD_SESSION_SECRET` is set, and sign-up stays closed until an invite code is set or `SIGNUP_MODE=open`. Without `RESEND_API_KEY`, set `REQUIRE_EMAIL_VERIFICATION=false` and note that password reset cannot deliver mail.
