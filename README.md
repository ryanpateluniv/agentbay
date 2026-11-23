# AgentBay: the app store for AI agents.

Buyers search and compare agents by price range, confidence and speed, set a hard spending cap before a run starts, and every model call is metered and billed in credits, so the receipt shows exactly where the money went. Creators publish an agent as a prompt, a manifest and a few test cases, with no code required. Every agent passes a 9-stage automated review (lint, build, static scan, security probes, benchmark, cost and latency, reliability, policy, decision) and gets a public report card; an admin approves it before it's listed. The spec is `docs/BLUEPRINT.md`.

| Folder | Owner | What |
| --- | --- | --- |
| `backend/` | backend | FastAPI gateway, LLM proxy, tool broker, ledger, sessions, review pipeline |
| `sdk/python/` | backend | `marketplace_sdk`, the agent runtime contract (see `sdk/python/README.md`) |
| `contracts/` | shared | `openapi.yaml`, `agent-manifest.schema.json`, `EVENTS.md` (SSE and runtime wire formats) |
| `frontend/` | frontend | UI |
| `agents/` | agents | Repo agents (code and no-code), the `_template` no-code example, and the InvoiceGenius Pro scam fixture |

## Run it

```bash
make dev          # creates .venv, installs, copies .env.example -> .env, RESETS the DB, reviews every repo agent, serves on :8000
make seed         # reset the DB and review every repo agent offline (fake LLM, heuristic judge), without serving
make test         # 73 tests (always fake mode): accounts, ledger, wallet, pricing, caps, settlement, sessions, review, no-code agents, publishing, real-mode proxy vs a stub OpenAI
make demo         # scripted end-to-end demo against the running server (second terminal)
```

You need Python 3.11+. Nothing else is required, because `LLM_MODE=fake` (the default) runs fully offline with deterministic replies and realistic token counts.

`make dev` starts from a clean database every time: it reseeds and re-reviews the repo agents offline (about 1–3 minutes), so the catalog is the same in fake and real mode. To keep your data between restarts, run uvicorn directly instead (see the real-mode section).

**The frontend is served by the backend at http://localhost:8000/** (one port, no CORS); see `docs/FRONTEND_REVIEW.md`. You can log in with the demo buttons (Sam, Riya, Admin) or sign up with email and password (new accounts get 500 trial credits and can switch on the creator role). Supabase (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, plus `ADMIN_EMAILS` for admins) is optional and adds Google sign-in. API docs are at http://localhost:8000/docs. The health check is `GET /health`.

**Demo accounts** (header `Authorization: Bearer <token>`):

| Token | User | Role | Credits |
| --- | --- | --- | --- |
| `tok_sam` | Sam | buyer | 5,000 |
| `tok_riya` | Riya | creator (owns the repo agents) | 200 |
| `tok_admin` | Admin | admin | 0 |

**Money:** every amount in the API is integer **microcredits** (1 credit = 1,000 microcredits = $0.01). Divide by 1000 to display.

### Real LLM mode (OpenAI)

Fake mode is the default, so the demo always works offline. To use OpenAI:

```bash
# .env must contain OPENAI_API_KEY=sk-...  (.env is gitignored; the key is never logged)
LLM_MODE=real make dev                    # fresh DB, then serve in real mode
# or keep the existing DB and its data (no reset, no re-review):
cd backend && LLM_MODE=real PUBLIC_BASE_URL=http://127.0.0.1:8000 ../.venv/bin/python -m uvicorn app.main:app --port 8000
python backend/scripts/verify_real.py     # one real Invoice Auditor session + metering check
```

Approved agents stay approved and listed when you switch modes; startup never re-reviews them. If OpenAI fails or rate-limits during the demo, restart the same uvicorn command without `LLM_MODE=real` to go back to fake mode.

- **Aliases and prices** live in `backend/config/models.yaml`: `fast` = gpt-6-luna, `balanced` = gpt-6.1-terra, `strong` = gpt-6.1-terra too (astra is disabled for cost; re-enable it by editing the commented entry). The platform's own LLM use (the review judge) is set under `platform:` and defaults to `fast`. Prices are USD per 1M tokens, including the cached-input price. Run `make seed` after editing the file. **Verify the prices** at https://developers.openai.com/api/docs/pricing.
- **Retries:** 429, 5xx and timeouts are retried with exponential backoff (honouring `Retry-After`) up to `LLM_MAX_RETRIES`. Then the call fails cleanly with 429 `upstream_rate_limited`, 504 `upstream_timeout` or 502 `provider_error`. Failed calls are never billed, and retries inside the proxy are billed once.
- **Safety caps:** `REAL_MAX_USD_PER_SESSION` (default $0.25) and `REAL_MAX_USD_PER_RUN` (default $2.00 per server process). Each call reserves its worst-case cost first and `max_completion_tokens` is clamped to fit, so a call that could break a cap is refused with 503 `safety_limit`. Current spend: `GET /v1/admin/metrics` → `real_mode_guard`.
- **Audit:** every OpenAI usage block is appended untouched to `backend/data/upstream_usage.jsonl`. Usage events store the provider request id, so the two can be joined.

### Docker sandbox (optional)

The default sandbox is one subprocess per session. With Docker Desktop running, set `RUNTIME_MODE=docker` and `PUBLIC_BASE_URL=http://host.docker.internal:8000`. Review then builds an image per version (a generated Dockerfile if the agent has none). Each session runs in a fresh container with 1 vCPU, 1 GB, a read-only root, a tmpfs workspace and a non-root user, and the container is removed at session end. Containers left over from a crash are reaped at startup.

## How a session works

1. `POST /v1/search` returns a ranked shortlist: relevance plus a conservative confidence bound, with price range and badges.
2. `POST /v1/quotes` returns low (p50), high (p90) and max, locked for 10 minutes.
3. `POST /v1/sessions {quote_id, cap_microcredits?}` holds the cap: `buyer_wallet → session_escrow` (402 if the balance is too low).
4. The runtime starts the agent with a session virtual key, sends the task to `/invoke`, and relays NDJSON events to SSE (`GET /v1/sessions/{id}/events?token=…`).
5. Every model call goes through `/proxy/v1/chat/completions` and writes a usage event. Each `spend` event shows the live meter; there's a warning at 80% and `budget_exhausted` at 100%.
6. The agent asks a question; the buyer answers with `POST /v1/sessions/{id}/messages {text, reply_to}`.
7. On result, error, cancel or timeout, settlement computes P, C, F and E (`E = max(0, P − F − C)`), empties the escrow, releases the unused hold and revokes the key. The `settled` event carries the receipt.

## Demo script (about 4 minutes, Section 16)

`make demo` runs steps 3–6 automatically. Use it to rehearse or as a backup recording.

1. **Problem (20 s).** Buyers can't tell which agent works, what it costs, or whether it's safe.
2. **Creator side (30 s).** Submit an agent (`POST /v1/creator/agents/{slug}/versions {source_path}`) and show the report card (`GET …/versions/{v}/review`): stages, test runs, cost p50/p90, probes, confidence.
3. **Buyer side (40 s).** Search "Plan a 5-day trip to Tokyo under $2,000" and compare 3 agents (`POST /v1/compare`). Show confidence with its sample size ("89, n=12, Beta").
4. **Live session (60 s).** Start Trip Planner and answer its budget question. The spend meter ticks with token counts from the proxy.
5. **Settlement (30 s).** Receipt: P 120, fee 24, model cost a few hundredths of a credit, creator gets the rest; unused hold released. Creator earnings update (`GET /v1/creator/earnings`).
6. **Trust (40 s).** Submit the scam agent. It's rejected live on critical findings (undeclared domain, followed an injected instruction, echoed a secret). Then start a session and hit the kill switch (`POST /v1/admin/agents/trip-planner/kill`): the session is cancelled and the receipt shows 0 charged. Unpause with `POST /v1/admin/agents/trip-planner/unpause`.
7. **Close (20 s).** This is the trust and billing layer agents need.

## Publish your own agent

An agent can be a folder with `agent.yaml`, `prompt.md` and `cases.jsonl`, and no Python. Copy `agents/_template/`, edit it, then either drop it into `agents/` (it's reviewed at startup) or upload it from the Creator page (form or zip). The field reference, how to write test cases, and pricing guidance are in [docs/AGENT_TEMPLATE.md](docs/AGENT_TEMPLATE.md).

## What's real vs simulated

| Real | Simulated |
| --- | --- |
| Metering: every model call goes through the proxy, with token counts and costs (matched against OpenAI's usage in real mode) | Human review: the automated pipeline is real; admin approval is a button, with no review team behind it |
| Ledger: double-entry, integer microcredits, holds, settlement, refunds, idempotency keys | Payouts: creator earnings clear for 14 days and payouts are recorded, but no money moves |
| Review: the 9 stages run the agent in a sandbox against its test cases and security probes | Top-ups and plans: mock checkout, no payment provider |
| | Web search: the `web_search` tool returns mocked results |

Search is keyword-based (TF-IDF over listings), not embeddings.

## Business model

- **Platform fee:** 20% of every charge (`PLATFORM_FEE_BPS`).
- **Model usage:** usage-based agents pass model cost through with a margin (`PLATFORM_MARKUP_BPS`, 10% by default).
- **Subscriptions:** monthly plans that bundle credits.

The main costs are model API spend, hosting the agent sandboxes, payment processing, and the compute that review runs use.

## Docs

- `docs/REVIEW.md`: blueprint review (gaps, risks, cuts)
- `docs/DECISIONS.md`: every deviation from the blueprint, with its reason
- `docs/STATUS.md`: done, in progress, blocked, next
