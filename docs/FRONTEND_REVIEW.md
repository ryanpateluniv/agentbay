# Frontend review: `frontend/index.html` vs `contracts/`

Reviewed 2026-11-22 against `contracts/openapi.yaml` and `contracts/EVENTS.md`. There's no separate Frontend Blueprint in the repo, so screens are checked against BLUEPRINT Sections 9–11 and 15 (web UI: search, compare, chat, wallet, creator dashboard).

**Summary.** The app was built against a different API shape: a chat/"conversations" model, Supabase auth, `*_credits` fields and flat SSE payloads. Rather than ask for a rewrite, I added a **compatibility layer on the backend**, so every call it makes now works as written. Tests in `backend/tests/test_frontend_compat.py` replay its exact calls. What's left for the frontend is auth (blocking), confirm and free-text answers, Idempotency-Key headers, and the missing screens.

## Update: second pull (ec71f51, 1c4a379), 2026-11-22 evening

**What he added.**
- ec71f51 added Compare, report card, Wallet (packs, plans, history), a Creator dashboard with a 5-step submit and review panel, and an Admin page (metrics, queue, kill, unpause, refund).
- It also added extend cap, file attach, rating and dispute, and a quieter session log.
- 1c4a379 then restored the "Cohort" home design: a named greeting, a shortcut row, a banner carousel, a Library page with agent pages, an Account overview, image paste and attach, deleting a chat, and hash routing.
- **But 1c4a379 was rebuilt from an older copy.** It silently removed his own ec71f51 screens and all of the 05b5d29 fixes: demo login, Idempotency-Key, Approve/Deny, free-text answers, the stream auth header, file links and the p90 cap. Login didn't work again, because `bootAuth()` was no longer called.

**What I did.** I kept the 1c4a379 design and ported the lost code back from ec71f51 as he wrote it, plus the small fixes listed in DECISIONS.md. It's verified by `backend/scripts/e2e_ui.py` (headless Chrome against `make dev`): **24/24 steps pass**.

| Area | Screen | Endpoints | Result |
| --- | --- | --- | --- |
| Buyer | Demo login (Sam, Riya, Admin), Google button kept | `/v1/auth/config`, `/v1/me`, `/v1/wallet` | ✅ |
| Buyer | Library page (4 live agents) | `GET /v1/agents` | ✅ (was 8 hard-coded agents, 5 fake) |
| Buyer | Search chat with Authorization | `/v1/conversations`, `/stream` | ✅ |
| Buyer | Compare 2–3 | `POST /v1/compare` | ✅ |
| Buyer | Report card | `GET /v1/agents/{id}` | ✅ |
| Buyer | Quote, cap = `high_credits`, attach file | `POST /v1/quotes` | ✅ |
| Buyer | Start (double-click → one session) | `POST /v1/sessions` + Idempotency-Key | ✅ |
| Buyer | Option answer, Approve, file link, receipt | SSE, `/messages`, `/confirm`, `/files/{id}`, `/receipts/{id}` | ✅ |
| Buyer | Rate | `POST /sessions/{id}/rating` | ✅ |
| Buyer | Wallet page, pack S, Upgrade double-click (one charge) | `GET /v1/wallet`, `POST /v1/wallet/topup` + key | ✅ |
| Creator | Dashboard | `/v1/creator/analytics`, `/earnings` | ✅ |
| Creator | 5-step submit → live 9-stage review → report card | `POST /v1/creator/agents`, `/versions`, `GET …/review` (polled) | ✅ |
| Admin | Metrics, queue, InvoiceGenius Pro rejected with findings | `/v1/admin/metrics`, `/review-queue`, `/agents` | ✅ |
| Admin | Kill switch refunds a running session; unpause; refund a settled session | `/kill`, `/unpause`, `/sessions/{id}/refund` | ✅ |
| Errors | Paused agent / insufficient credits / budget cap | 409 `agent_unavailable` / 402 / `budget_*` events | ✅ |

**Contract check of the new calls.**
- Request bodies all match the schemas: compare, extend `add_microcredits`, rating, dispute, refund, decision, subscriptions, topup `{pack}`, and session `files`.
- Fields the new screens read that were missing, now added on the backend: agent `description` and `creator_name`, detail `live_stats`, `report_card.stages` and `.tests`, and wallet `email`.
- Fixed `available_credits`, which used to include money still in clearing.
- The creator form's partial manifest is now laid over `agent.yaml` (before, every form submit failed lint).

**Not exercised by the run:** dispute, extend cap, subscription plans (backend stub), the queue's Approve/Reject buttons (the queue was empty), the creator's "Re-run review" (it needs admin), and the Supabase sign-in (no keys).

**Still for the frontend owner:**
1. Pull before editing, and don't rebuild from an older copy. That is how 1c4a379 lost his screens and the fixes.
2. The home shortcuts and banner prompts point at agents that don't exist (Inbox, Notes, Contract, Support, Sheet), so they match nothing. Point them at the 4 live agents, or generate them from `GET /v1/agents`.
3. The breadcrumb stays "New chat" on the Session, Wallet, Review and Creator pages.
4. The ported screens (`.cmp`, `.kv`, `.form-grid`) still use plain browser-style buttons; restyle them to the new design.
5. Small amounts round to "0 credits" in the receipt, report card and wallet (for example a 0.01 cap shows "Held 0 credits").
6. The composer's image attach only sends "Image attached: name" as text. Real files go only through the quote's file input.
7. Compare needs 2 search matches; many searches return only 1. Consider letting a Library card be added to Compare too.
8. The hard-coded "Jaineel" placeholders (greeting, avatar letter, Account fallback) flash before login.

_Everything below is the first review (05b5d29 and earlier), kept for history._

## 1. API calls

| Frontend call | Contract | Status |
| --- | --- | --- |
| `GET /v1/auth/config` | not in contract | **Added.** Returns `{configured, url, anon_key}` from `SUPABASE_URL` / `SUPABASE_ANON_KEY`. |
| `POST /v1/conversations`, `GET /v1/conversations[/{id}]`, `POST /v1/conversations/{id}/stream` | not in contract (the spec has `POST /v1/search`) | **Added** as a discovery chat on top of search. The stream sends `data:` frames `{type: thinking\|reply\|done, payload}`. The reply is templated (no LLM cost). |
| `GET /v1/wallet` reads `balance_credits`, `granted_credits`, `used_credits`, `user` | contract has `balance_microcredits` | **Added** as extra fields (credits, display only) |
| `POST /v1/wallet/topup {credits: 500}` | contract `{pack: S\|M\|L}` | **Accepted.** `credits` is a mock top-up of 1–10,000. No Idempotency-Key is sent. |
| `POST /v1/quotes` reads `q.maximum` | contract `max_microcredits` | **Added** `maximum`, `low_credits`, `high_credits` |
| `POST /v1/sessions {agent_id, task, cap_credits}` reads `session_id` | contract `{quote_id, cap_microcredits}` returns `id` | **Accepted.** The backend makes the quote implicitly, converts credits, and returns `session_id` too. No Idempotency-Key is sent. |
| `GET /v1/sessions` reads `{sessions: [{id, title, status, task, cap_credits, active}]}` | was a bare list | **Changed** to `{sessions: [...]}` with those fields. This was a backend-only extra endpoint, so nothing else depended on it. |
| `POST /v1/sessions/{id}/messages {text}` (no `reply_to`) | `reply_to` optional | OK. The backend answers the oldest pending question. |
| `POST /v1/sessions/{id}/cancel` | same | OK |
| `GET /v1/receipts/{id}` reads `price_credits, cap_credits, released_credits, earnings_credits, fee_credits, cost_credits` | contract `*_microcredits` | **Added** `*_credits` fields |
| `GET /v1/creator/analytics` reads `sessions, platform_revenue_credits, provider_cost_credits` | contract has per-agent rows | **Added** top-level totals. These are scoped to the creator's own sessions (fees paid, model cost), not platform-wide numbers, which creators must not see. |
| `GET /v1/creator/earnings` reads `available_credits, clearing_days` | `*_microcredits` | **Added** (`clearing_days: 14`) |
| `GET /v1/admin/agents` reads `{agents: [{id, title, status, decision, findings[], version_id}]}` | not in contract | **Added** |
| `POST /v1/admin/versions/{v}/review` reads `{decision, findings[], confidence}` | not in contract (review starts on submit) | **Added.** Re-runs the pipeline and waits for it (about 15 s in fake mode). |
| `POST /v1/admin/agents/{id}/kill` reads `refunded_sessions` | returned `cancelled_sessions` | **Added** alias `refunded_sessions` |

**Money units.** The frontend uses credits everywhere and never microcredits. That's fine for display now that `*_credits` exists, but `cap_credits` is sent as a JS number. The backend converts it with `round(x * 1000)`. Money of record stays integer microcredits.

**Errors.** It shows `data.detail || statusText`. Backend errors are `{error, detail}`, so messages show up.

## 2. SSE

- **Query parameter:** it uses `?access_token=` and the contract had `?token=`. **The backend now accepts both.**
- **Event names:** it only sets `es.onmessage`, but the backend sent named frames (`event: <type>`), which `onmessage` never receives. So **no live events would have shown**. Fix: `?access_token=` (or `?flat=true`) now gets **unnamed frames with a flat payload**: `{id, type, role, text, ...data, *_credits}`. `status` carries `text` and `spend` carries `spent_credits`. Named frames stay the default for other clients.
- **Event types used:** `ask_user` (renders option buttons), `error`, `settled`, and `spent_credits` from `spend`. `status` events now include `text`. **Not handled:** `confirm` (see the must-fix list), `file` (shown as a text bubble only), `progress` (shows its label as a bubble), `result`, `budget_warning` and `budget_exhausted` (the bar colours at 80/100% come from spend, which is OK). Every event, `spend` included, is appended as a bubble, which is noisy.
- **Reconnect:** EventSource reconnects automatically and sends `Last-Event-ID`. The backend resumes from that id, so there are no duplicates. After `settled`, the client calls `es.close()`. If it reconnects anyway (tab sleep), **the backend now answers 204**, which tells EventSource to stop. The discovery chat stream uses `fetch` plus a manual reader and has no reconnect, which is fine for short replies.

## 3. Auth

- **Supabase is the only login.** `bootAuth()` disables every button and leaves the sign-in overlay up unless `/v1/auth/config` says `configured: true`. With no Supabase keys in `.env` (the current state), **the UI can't be used, and the demo tokens can't be entered anywhere.**
- **Backend now:** with `SUPABASE_URL` and `SUPABASE_ANON_KEY` set, any Supabase access token is verified by calling Supabase `/auth/v1/user`, cached for 5 minutes. On first sight the email becomes a platform user (buyer + creator, 200 free-trial credits). `ADMIN_EMAILS` grants admin. Demo tokens keep working for curl and tests. *I couldn't test this against a real Supabase project; it's covered only by code review.*
- `POST /v1/conversations/{id}/stream` is sent **without** the Authorization header. The backend authorises it by the unguessable conversation id, but the header should be added.
- **Idempotency-Key** is sent nowhere: not on session start (the hold), top-ups, or review re-runs. A double-click on Start creates two sessions and two holds; a double-click on "Add 500 credits" adds 1,000. The backend honours the header when it's present.

## 4. Screens vs the blueprint

| Blueprint screen | State |
| --- | --- |
| Describe task → shortlist (Section 9) | ✅ Discovery chat with cards (price, badges, confidence with sample size, p50 time) |
| Quote + cap + top-up when short (Section 10, steps 5–6) | ✅ Quote sheet. The cap defaults to the quote **max**, not the p90 the spec asks for. Use `high_credits`. |
| Live session: messages, questions, live spend vs cap, cancel | ✅ Partly. ❌ No `confirm` approve/deny, ❌ no free-text answers (only option buttons or "Continue"), ❌ no extend cap, ❌ no files panel or download, progress shown as bubbles only |
| Receipt | ✅ |
| Session history | ✅ |
| Compare up to 3 side by side (Section 9) | ❌ Missing (`POST /v1/compare` is ready) |
| Agent detail / report card (Section 8) | ❌ Missing (`GET /v1/agents/{id}` has `report_card`) |
| Wallet page with history | ❌ Only a ring plus "Add credits" (`GET /v1/wallet` has `history`) |
| File upload for tasks | ❌ The paperclip only focuses the textbox. `POST /v1/sessions` accepts `files: [{name, content_base64}]`. |
| Ratings / disputes | ❌ Missing (endpoints exist) |
| Creator dashboard (Section 11 panels) | ⚠️ Four numbers only. ❌ No submit-version flow, review progress, report card, versions, cost and margin, failures, logs or payouts. |
| Admin | ✅ Agent list with Run review and Kill switch. ❌ No refund button (`POST /v1/admin/sessions/{id}/refund`). |
| **Mock / hard-coded data** | The sidebar "Library" lists 6 fixed agents, and **3 don't exist** (Inbox Cleaner, Meeting Notes, Contract Reader); Quick Summarizer is missing from it. The banner carousel copy is static. |

## 5. Base URL, mode, running it

- **Base URL:** every call is a relative path (`/v1/...`), so the page must be served **from the backend's origin**. There's no mock mode and no base-URL setting; it's always live.
- **Run it:** `make dev`, then open **http://localhost:8000/**. The backend now serves `frontend/index.html` at `/`, so everything is on one port and there are **no CORS issues**.
- **Separate static server** (e.g. `python -m http.server 5173` in `frontend/`): this does not work as-is, because relative paths would hit the static server. It would need a base-URL constant. CORS is already `*` without credentials, so cross-origin calls with a bearer header would pass.
- Supabase-js loads from the jsDelivr CDN, so it needs internet. For Google OAuth, add `http://localhost:8000/` to the Supabase redirect allowlist.

## What the frontend must change

_Update 2026-11-22: items 1–8 are done (see DECISIONS.md) and verified by a headless-Chrome replay. Item 9 is still open._

1. **Add a demo login** (blocking without Supabase). For example, when `/v1/auth/config` returns `configured: false`, offer the `demo_tokens` it now lists (`tok_sam`, `tok_riya`, `tok_admin`) and set `state.token`. Or add Supabase keys to `.env`.
2. **Handle `confirm` events:** Approve/Deny buttons that `POST /v1/sessions/{id}/confirm {confirm_id: data.id, approved}`. **The real Trip Planner asks for confirmation; without this the session hangs until it times out** (then it's refunded).
3. **Free-text answers:** when `ask_user` has no `options`, show a text box and send `{text, reply_to: data.id}`.
4. **Send `Idempotency-Key`** (one UUID per user action) on Start session, Add credits and Run review.
5. Send the Authorization header on `POST /v1/conversations/{id}/stream`.
6. Don't render `spend` / `status` events as chat bubbles. Show a file link (`url?access_token=`) for `file` events.
7. Default the cap to `q.high_credits` (p90), not `q.maximum`.
8. Replace the hard-coded Library with `GET /v1/agents`.
9. Nice to have for the demo: Compare view, agent report card, file upload, extend cap, admin refund button.
