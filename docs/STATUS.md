# Status

## Done
- Phase 1 review (`docs/REVIEW.md`)
- Step 1: scaffold, models (Section 12), seed (3 users, 3 model aliases, 2 tool prices, 4 listings), ledger service
- Step 2: contracts/openapi.yaml (47 paths), contracts/agent-manifest.schema.json, contracts/EVENTS.md; read endpoints live on seeded data; session, proxy and tool endpoints wired (untested end to end)
- Step 3: ledger tests (Section 7 examples, journal, idempotency, concurrent holds, extend, refund): 13 passing
- Step 4: marketplace_sdk (stdlib), dev runner, example agent, README

## In progress
- Step 5: LLM proxy tests + session end to end

## Blocked
- Nothing. LLM provider is still `[PROVIDER]`; running in `LLM_MODE=fake` until it's confirmed.

## Next
- Ledger tests, SDK, proxy, session flow, review, search, hardening
