# Decisions and deviations from BLUEPRINT.md

One line each, with the reason.

- Blueprint path is `docs/BLUEPRINT.md`, not a PDF. The `.md` is the source of truth.
- No Alembic: `Base.metadata.create_all` plus `python -m app.seed --reset`. One SQLite file, 3-hour budget.
- SQLite FK enforcement is off. FKs are declared, but there are no ORM relationships to order inserts.
- Added a `ledger_txns` table with a UNIQUE idempotency key. Entries still carry the key, and this makes retries safe at the DB level.
- Added a `payments_clearing` ledger account. Purchased credits and payouts need a counter-account the spec doesn't list.
- Ledger sign convention: a leg debits the source and credits the destination; balance = credits − debits. This matches the blueprint journal table.
- Settlement when C > P − F: escrow pays `provider_cost` up to P − F, and `platform_revenue → provider_cost` covers the rest. This reproduces the blueprint's platform-net column.
- Platform fee F is rounded half up to an integer microcredit. LLM call cost is rounded up (ceil).
- 1 credit = 1,000 microcredits, as in the spec, even though "micro" suggests 10^6.
- Model prices are stored as integer micro-USD per 1M tokens, so the cost formula is all-integer.
- The API exposes money as `*_microcredits` integers only. The UI divides by 1000 for display.
- Auth: seeded static bearer tokens (`tok_sam`, `tok_riya`, `tok_admin`), with roles on the user.
- Live stats (n, live Q/R, ratings, price p50/p90) are seeded into `listings.live_stats_json` and updated on each settled session, instead of seeding hundreds of fake session rows.
- Default sandbox is a subprocess (Docker daemon not running on the dev box); `RUNTIME_MODE=docker` is optional.
- Seeded agent versions point at `agents/<slug>/main.py` when it exists, otherwise at `sdk/python/examples/echo_agent`, so the demo runs before the agents are delivered.
- Review: runs per case default to 2 (`REVIEW_RUNS_PER_CASE`, spec says 5) so the live demo finishes in about 15 s.
- Review: there's no egress proxy in subprocess mode, so undeclared or provider domains are caught by a static scan of the source (critical finding = automatic rejection).
- Review: the fake LLM never parrots canaries, URLs or `sk-` secrets ("a well-behaved model"), so injection and exfiltration probes are deterministic and only the agent's own code can fail them.
- Review: the first approved version of a new agent is published automatically (Beta badge). Later versions need `POST .../publish`.
- Review: sensitive categories (finance, health, legal, children) end in `pending_human_review` until `POST /v1/admin/versions/{v}/decision`.
- Proxy: calls within one session are serialised by a per-session lock. Simplest correct cap enforcement; parallel calls inside one session queue up.
- SDK additions outside the Section 5 list: `ctx.llm()`, `ctx.tool()`, `chat()`, `@agent.on_quote`, `python -m marketplace_sdk.dev`. Thin helpers so plain-Python agents need no extra libraries.
- API additions outside Section 13: `GET /v1/agents`, `GET /v1/sessions`, `GET /v1/sessions/{id}/history`, `GET /v1/me`, `GET /v1/creator/agents`, `POST /v1/admin/sessions/{id}/refund` (the MVP refund button), `POST /v1/admin/agents/{id}/unpause`, `GET /v1/admin/model-prices`.
- Ratings: the 24 h delay before rating is not enforced (demo).
- Session time limit is wall clock and does not pause while waiting for the user. Per-minute billing does exclude waiting time.
- Budget cap reached: the buyer pays min(cap, full charge) if a result was delivered, else 0. The agent gets 20 s of grace before a hard stop.
