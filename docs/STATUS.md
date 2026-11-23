# Status

_Updated 2026-11-22 ~18:45_

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
- Step 9: `make dev` / `make test` / `make demo`, root README with demo script. 36 tests passing.

## In progress
- Nothing

## Blocked
- LLM provider still `[PROVIDER]`. Fake mode is the default; real mode forwards to any OpenAI-compatible upstream.

## Next (if time)
- Point at the real agents in `agents/` once they land (resolved automatically per session)
- Docker mode smoke test (Docker daemon is not running on this machine, so it's untested)
- Real embeddings for search; LLM re-ranker (cut)
