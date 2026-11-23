# Agent Marketplace (hackathon MVP)

An app store for AI agents: every agent is tested before you pay, and every cent is metered. The spec is `docs/BLUEPRINT.md`.

| Folder | Owner | What |
| --- | --- | --- |
| `backend/` | backend | FastAPI gateway, LLM proxy, tool broker, ledger, sessions, review pipeline |
| `sdk/python/` | backend | `marketplace_sdk`, the agent runtime contract (see `sdk/python/README.md`) |
| `contracts/` | shared | `openapi.yaml`, `agent-manifest.schema.json`, `EVENTS.md` (SSE and runtime wire formats) |
| `frontend/` | frontend | UI |
| `agents/` | agents | Demo agents (`invoice-auditor`, `trip-planner`, `research-briefer`, `quick-summarizer`, scam agent) |

## Run it

```bash
make dev          # creates .venv, installs, copies .env.example -> .env, seeds a fresh DB, serves on :8000
make test         # 42 tests (always fake mode): ledger, cost formula, caps, settlement, sessions e2e, review, real-mode proxy vs a stub OpenAI
make demo         # scripted end-to-end demo against the running server (second terminal)
```

You need Python 3.11+. Nothing else is required, because `LLM_MODE=fake` (the default) runs fully offline with deterministic replies and realistic token counts.
API docs are at http://localhost:8000/docs. The health check is `GET /health`.

**Demo accounts** (header `Authorization: Bearer <token>`):

| Token | User | Role | Credits |
| --- | --- | --- | --- |
| `tok_sam` | Sam | buyer | 5,000 |
| `tok_riya` | Riya | creator (owns all seeded agents) | 200 |
| `tok_admin` | Admin | admin | 0 |

**Money:** every amount in the API is integer **microcredits** (1 credit = 1,000 microcredits = $0.01). Divide by 1000 to display.

### Real LLM mode (OpenAI)

Fake mode is the default, so the demo always works offline. To use OpenAI:

```bash
# .env must contain OPENAI_API_KEY=sk-...  (.env is gitignored; the key is never logged)
LLM_MODE=real make dev                    # this run only
python backend/scripts/verify_real.py     # one real Invoice Auditor session + metering check
```

If OpenAI fails or rate-limits during the demo, restart with plain `make dev` to go back to fake mode.

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
5. **Settlement (30 s).** Receipt: P 150, fee 30, model cost a few hundredths of a credit, creator gets the rest; unused hold released. Creator earnings update (`GET /v1/creator/earnings`).
6. **Trust (40 s).** Submit the scam agent. It's rejected live on critical findings (undeclared domain, followed an injected instruction, echoed a secret). Then start a session and hit the kill switch (`POST /v1/admin/agents/trip-planner/kill`): the session is cancelled and the receipt shows 0 charged. Unpause with `POST /v1/admin/agents/trip-planner/unpause`.
7. **Close (20 s).** This is the trust and billing layer agents need.

## Docs

- `docs/REVIEW.md`: blueprint review (gaps, risks, cuts)
- `docs/DECISIONS.md`: every deviation from the blueprint, with its reason
- `docs/STATUS.md`: done, in progress, blocked, next
