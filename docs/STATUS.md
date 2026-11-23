# Status

_Updated 2026-11-22 ~19:30_

## Done
- Phase 1 review (`docs/REVIEW.md`)
- Step 1: scaffold, models (Section 12), seed (3 users, 3 model aliases, 2 tool prices, 4 listings with seeded review and live stats)
- Step 2: `contracts/openapi.yaml`, `contracts/agent-manifest.schema.json`, `contracts/EVENTS.md`
- Step 3: double-entry ledger with idempotency keys: hold, extend, settle (platform absorbs when C > P − F, E floored at 0), refund, payout
- Step 4: `marketplace_sdk` (stdlib), dev runner, example agent, README
- Step 5: LLM proxy (virtual keys, aliases, integer cost formula, max_tokens clamp, per-session lock, budget_exhausted, streaming billed on streamed tokens, fake mode); tool broker (mock web_search, untrusted outputs, call limit)
- Step 6: session flow end to end: subprocess sandbox (Docker optional), NDJSON → SSE with replay, ask_user/confirm, live spend, 80% warning, budget hard stop with 20 s grace, timeout, cancel rules, settle, receipt, orphan reaping, restart refunds
- Step 7: review pipeline (lint, build, static scan, 3 probes, cases x2, cost/latency, judge, Q/R/S/K, confidence, report card, auto-publish of the first version)
- Step 8: search (TF-IDF fallback), quotes, compare, creator endpoints, admin kill/refund/prices/fees/disputes/metrics
- Step 9: `make dev` / `make test` / `make demo`, root README with demo script
- **Real LLM mode (OpenAI)**: config-driven aliases and prices (`backend/config/models.yaml`), retries with backoff, clean unbilled failures, per-session and per-run USD safety caps, raw provider usage log. Verified with one real Invoice Auditor session (metering matched OpenAI's usage exactly) and a full `make demo` in real mode. Total real spend: about $0.044. 42 tests passing (fake mode).

## How to switch modes
- Fake (default, offline): `make dev`
- Real: put `OPENAI_API_KEY` in `.env`, then `LLM_MODE=real make dev`. Verify with `python backend/scripts/verify_real.py`. Caps: `REAL_MAX_USD_PER_SESSION` / `REAL_MAX_USD_PER_RUN`.

## In progress
- Nothing

## Blocked
- None. Open item for the agents teammate: Research Briefer uses a different external contract (`X-Timestamp`, `{session_id,text}` / `{text,done}`) than `contracts/EVENTS.md`, so it needs an adapter or alignment.

## Next (if time)
- Point at the real agents in `agents/` once they land (resolved automatically per session)
- Docker mode smoke test (Docker daemon is not running on this machine, so it's untested)
- Real embeddings for search; LLM re-ranker (cut)

## Cost update (2026-11-22, strong = terra)
- Real Invoice Auditor session: $0.0009 (was $0.0074 with astra). Metering matched OpenAI exactly.
- One full real `make demo`: about $0.0057 (was about $0.036). The Trip Planner's single `strong` (terra) call is about 90% of it; the luna judge costs about $0.0001.
- All real calls so far: about $0.050, plus about $0.0003 of earlier model probes.

## Frontend integration (2026-11-22)
- Reviewed `frontend/index.html` (`docs/FRONTEND_REVIEW.md`). Backend compatibility layer added and served at http://localhost:8000/. 44 tests passing (2 replay the frontend's exact calls).
- Blocked on the frontend side: login needs Supabase keys or a demo-token login; `confirm` events have no UI (the Trip Planner hangs at its approval step).
