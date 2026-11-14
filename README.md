# LLM Gateway

An OpenAI-compatible gateway that routes each LLM call to the right (provider, model) pair for the task, records every decision and attempt, and shows cost, latency and errors in a dashboard. Built so multi-agent graphs (implementer, reviewer, planner...) get role-aware routing, e.g. a reviewer is routed to a different model family than the model that produced the work.

This repo is **phase 1**: the hot path, event logging and a dashboard. The control plane (evals, policy proposals, canary) is phase 2+.

```
gateway/     FastAPI + LangGraph hot path, providers, routing, event sinks, tests, benchmark
dashboard/   Next.js dashboard (reads the same Postgres)
docker-compose.yml   local Postgres on :5433
```

## Quickstart

```bash
cp .env.example .env
docker compose up -d --wait postgres
cd gateway && uv sync && uv run uvicorn app.main:app --port 8000
```

In another terminal:

```bash
curl -s localhost:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hello"}]}' -i | grep -i -E 'x-gateway|HTTP/'
```

Dashboard:

```bash
cd dashboard && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3100
```

With no `DATABASE_URL` the gateway runs with an in-memory event store (and `GET /internal/events` to inspect it).

The mock provider is on by default, so everything works without any API keys. To use real open models, set variables in `.env`:

| Provider | Variable | Notes |
|---|---|---|
| Ollama (local) | `OLLAMA_ENABLED=1` | needs `ollama serve`; not reachable from Vercel |
| Groq | `GROQ_API_KEY` | free tier, check current limits |
| OpenRouter | `OPENROUTER_API_KEY` | some `:free` models |
| hide mocks | `GATEWAY_DISABLE_MOCK=1` | once real providers are configured |

Model ids and list prices in [seed_policy.yaml](gateway/app/data/seed_policy.yaml) are examples; verify them against each provider before relying on them.

## Using it from your app

Point any OpenAI SDK at the gateway; only the base URL changes. Routing context goes in headers:

| Header | Meaning |
|---|---|
| `x-run-id` | groups the calls of one agent run |
| `x-node-role` | `implementer`, `reviewer`, `planner`, ... (reviewers get independence routing) |
| `x-parent-call-id` | the `x-gateway-call-id` of the call whose output this call works on |
| `x-task-type` | `chat`, `extraction`, `classification`, `summarization`, `code`, `agent_tool`, `review` |
| `x-quality-hint` | `low` or `high` |
| `Idempotency-Key` | non-streaming replay protection (in-memory, per instance) |

`model: "auto"` (default) routes; any id from `GET /v1/models` pins that model. Responses carry `x-gateway-call-id`, `x-gateway-model`, `x-gateway-route-reason`, `x-gateway-policy-version` and `x-gateway-attempts`.

```ts
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "http://localhost:8000/v1", apiKey: "unused" });
const { data, response } = await client.chat.completions
  .create({ model: "auto", messages: [{ role: "user", content: "write a parser" }] })
  .withResponse();
const callId = response.headers.get("x-gateway-call-id"); // pass as x-parent-call-id on the reviewer call
```

To see fallback behavior with the mock provider, inject failures: `x-mock-behavior: mock/small=error_500,mock/medium=timeout` (behaviors: `ok`, `error_500`, `error_429`, `timeout`, `malformed`, `bad_json`; `*` matches all). Disable with `GATEWAY_ALLOW_MOCK_CONTROL=false`.

## What is implemented (phase 1)

- Hot path as a LangGraph graph: `intake -> profile -> [llm_classify] -> route -> call -> evaluate -> respond`, with a bounded retry/fallback loop (`update_context -> route`) that excludes failed models, escalates the tier on validator failures, and stops at the retry budget or deadline.
- Routing: capability filter (tools, JSON mode, context window, streaming), task-type and complexity tiers, reviewer family independence (relaxed in a defined order, and recorded when relaxed), ranking by quality, list-price cost and latency, explainable `route_reason`.
- Streaming passthrough with fallback before the first token.
- Providers: one OpenAI-compatible adapter (Ollama, Groq, OpenRouter, ...) and a deterministic mock with failure injection.
- Events: runs, spans (including the gateway's own `origin=internal` calls) and attempts in Postgres, written after the response; free models report `shadow_cost` at list price.
- Versioned `routing_policy` table (one active row); the hot path caches it with a TTL and keeps the last known good policy if the database is unreachable. `uv run python scripts/push_policy.py file.yaml` publishes a new version.
- Dashboard: KPIs, by model / role / task, attempt outcomes, per-node time, internal LLM cost, recent requests with the routing reason.

## Tests and benchmark

```bash
cd gateway
uv run pytest                                   # 40 tests, no network or keys
TEST_DATABASE_URL=postgresql://gateway:gateway@localhost:5433/gateway uv run pytest   # + Postgres integration test
uv run python bench/overhead.py 2000            # LangGraph vs plain async overhead
```

Measured once on a dev laptop (Python 3.12, mock provider with zero latency, so this is gateway-only time per request):

| scenario | pipeline | mean | p50 | p95 |
|---|---|---:|---:|---:|
| happy path | LangGraph graph | 3.01 ms | 2.71 ms | 4.21 ms |
| happy path | plain async | 0.06 ms | 0.06 ms | 0.07 ms |
| one fallback | LangGraph graph | 5.80 ms | 4.50 ms | 11.42 ms |
| one fallback | plain async | 0.14 ms | 0.10 ms | 0.34 ms |

LangGraph adds roughly 3 ms on the happy path and about 1.5 ms per extra loop; the pipeline itself is about 0.1 ms. Against model latencies of hundreds of ms that is small, but it is the largest single cost in the gateway, so the choice to keep it is deliberate (the graph makes the retry loop explicit and traceable) and the benchmark is there to keep it honest. The same node functions run in both pipelines (`app/graph/plain.py`), so switching is cheap if the overhead ever matters.

## Not done / not verified

- **Real providers are untested.** The OpenAI-compatible adapter has not been run against Ollama, Groq or OpenRouter in this repo (no keys were available); only the mock path is covered by tests. Expect small fixes on first contact (e.g. a provider that rejects `stream_options`).
- **Vercel deployment is untested.** The code is shaped for it (exports `app` from `app/main.py`, stateless, no filesystem writes, env-based config, `prepare_threshold=None` for pooled Postgres). To verify when deploying: that the post-response event write completes (FastAPI background tasks and the detached stream-finalize task), that `app/data/*.yaml|sql` are bundled with the function, and LangGraph's import cost on cold starts. No `vercel.json` is included yet.
- Policy statuses `shadow` and `canary` exist in the schema but the hot path only serves `active`.
- API keys come from `GATEWAY_API_KEYS`; per-key budgets, rate limits, PII redaction, circuit breakers and OpenTelemetry are phase 4.
- `llm_classify` is implemented and tested with the mock, off by default in the seed policy.
- Idempotency replay is an in-memory cache, so it only dedupes within one instance.
