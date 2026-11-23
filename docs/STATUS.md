# Status

## Done
- Phase 1 review (`docs/REVIEW.md`)
- Step 1: scaffold, models (Section 12), seed (3 users, 3 model aliases, 2 tool prices, 4 listings), ledger service
- Step 2: contracts/openapi.yaml (47 paths), contracts/agent-manifest.schema.json, contracts/EVENTS.md; read endpoints live on seeded data; session, proxy and tool endpoints wired (untested end to end)
- Step 3: ledger tests (Section 7 examples, journal, idempotency, concurrent holds, extend, refund): 13 passing
- Step 4: marketplace_sdk (stdlib), dev runner, example agent, README
- Step 5: LLM proxy (virtual keys, aliases, integer cost formula, clamp, budget_exhausted, streaming, fake mode), tool broker (mock web_search)
- Step 6: session flow end to end on a live server: quote, hold, subprocess sandbox, ask_user round trip, SSE, settle, receipt, cancel 50%, kill switch refund, admin refund, 402, concurrent cap. 27 tests passing
- Step 7: review pipeline (lint, build or subprocess check, static scan, 3 injection/exfil probes, cases x2, cost/latency p50/p90/p95, judge, Q/R/S/K, confidence, report card); scam fixture rejected, example agent approved and auto-listed. 30 tests passing
- Step 8: TF-IDF search (embedding fallback) with filters and confidence lower-bound ranking, quotes, compare, creator (agents, versions, review, listing, publish, rollback, analytics, logs, earnings, payouts), admin (queue, decision, kill, unpause, refund, prices, fees, disputes, metrics). 36 tests passing

## In progress
- Step 9: hardening, make dev, README, demo script

## Blocked
- Nothing. LLM provider is still `[PROVIDER]`; running in `LLM_MODE=fake` until it's confirmed.

## Next
- Ledger tests, SDK, proxy, session flow, review, search, hardening
