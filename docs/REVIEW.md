# Backend review of BLUEPRINT.md

## 1. Contradictions and gaps that affect the backend
- **Escrow math when C > P − F.** The journal only covers the normal case. For the "expensive run" (P=150, C=180) escrow only gets 150 from the buyer, so the shortfall (60) must be posted `platform_revenue → provider_cost`. Same for outcome-failed (P=0, C=25). Platform net then matches the table (−30, −25).
- **"Cap" means two things.** It is the escrow hold / max buyer charge *and* the proxy's model-spend limit. Assumption: buyer is charged `P ≤ cap`; the proxy stops when `C ≥ min(cap_for_cost, manifest.max_llm_cost_credits)`, where `cap_for_cost = cap` for flat pricing and `cap / (1+markup)` for usage-based. For per-task, a cap below the price is rejected (409).
- **"Cap reached with partial result: buyer pays the cap."** For per-task this would charge more than P. Assumption: charge `min(cap, P)`.
- **Usage-event example is off.** $0.0031 at $0.01/credit is 310 µc (341 with 10% markup), not 400. Also, 1 credit = 1,000 "micro"credits gives 1 µc = $0.00001, so cheap calls are a few µc. Assumption: keep 1,000 (the spec says so), and round each call **up** to an integer.
- **The runtime contract does not define how ask_user answers get back.** Assumption: the first `/invoke` (type `task`) returns a long-lived NDJSON event stream. Later `/invoke` calls (type `message` / `confirm`) return 202 and unblock the waiting handler. Events keep flowing on the first stream.
- **Auth mechanism is unspecified.** Assumption: seeded bearer tokens per demo user (`Authorization: Bearer tok_sam`), with roles on the user.
- **Terminal exception statuses (`failed`, `cancelled`, …) also need settlement entries.** They keep the exception status and get `settled_at`.
- **Gaps:** `/internal/settle` vs gateway-triggered settle (I'll implement one service function and expose both). Search needs ~6 listings vs 4 requested: I'll seed 4. Migrations: `create_all`, no Alembic.

## 2. Risky areas
- **Ledger / concurrent holds:** a balance check followed by a separate insert can overdraw under concurrency. Fix: compute the balance from entries inside one write transaction (SQLite `BEGIN IMMEDIATE`, plus a process-wide lock). Idempotency keys `hold:{sid}` and `settle:{sid}` are UNIQUE, so a retry returns the original txn. Every txn is asserted to sum to zero.
- **Cap races:** parallel LLM calls can all pass the check. Fix: a per-session asyncio lock around check and record, and `max_tokens` clamped to the remaining budget. The residual overshoot is absorbed, as the spec says.
- **Streaming accounting:** request `stream_options.include_usage`. On a drop, estimate output from the streamed chars (len/4). Bill in a `finally` block, and never bill provider errors.
- **Container lifecycle:** leaks on crash or restart. Label containers `mkt.session=<id>`, reap them at startup and on every terminal state, and run a wall-clock watchdog task. The subprocess fallback uses a process group plus killpg. Docker Desktop reaches the host via `host.docker.internal`.
- **Auth on runtime:** the virtual key is stored hashed, is scoped to one session, and is revoked at settle. The proxy rejects revoked or expired keys.
- **Idempotency on buyer money calls:** honour the `Idempotency-Key` header on `POST /sessions`, `/extend` and `/wallet/topup`.

## 3. Cut or simplify for the 3-hour budget
Egress proxy (static scan for blocked domains instead), Mode B HMAC (one external agent, plain HTTP; cut first if late), subscriptions, payouts (simulated row only), disputes (admin refund only), bids, preview runs, rollback, re-ranker, creator-secret encryption, the CLI (README with curl/run steps instead), real Stripe (mock top-up), Postgres. Fake LLM mode is the default and the demo-safe path. Embeddings fall back to a keyword/TF-IDF score.

## 4. What teammates need from me early
- **Frontend:** `contracts/openapi.yaml`, stub endpoints with seeded data, the SSE event schema, demo bearer tokens, CORS, and a port (8000).
- **Agents:** `contracts/agent-manifest.schema.json`, `marketplace_sdk` and its README, the env-var list, the event NDJSON format, fake LLM mode, and how to run an agent locally against the backend.
