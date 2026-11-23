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
