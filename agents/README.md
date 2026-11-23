# Marketplace demo agents

| Agent | Role in the demo | Status |
|---|---|---|
| `invoice-auditor` | Main, high-quality agent (fast + strong, code checks) | 8/8 local cases |
| `trip-planner` | LangGraph workflow: question, A/B/C choice, approval | 7/7 local cases |
| `invoicegenius-pro` | Scam fixture, fails review (DEMO FIXTURE, NEVER DEPLOY) | fails by design |
| `research-briefer` | External endpoint with HMAC signature check | server tests pass |
| `budget-invoice-bot` | Cheaper honest rival (fast only, basic checks) | 6/6 local cases |

`_shared/demodata/invoices/` has the demo invoices and a README listing every planted error.

## Check everything locally (fake model, no keys)
```bash
python _shared/validate.py                                  # manifest + handoff lint
cd invoice-auditor && PYTHONPATH=../_shared/devstub LLM_MODE=fake python ../_shared/devstub/run_cases.py
cd ../research-briefer && LLM_MODE=fake python tests/test_server.py
```
`_shared/devstub/` is a stand-in for the backend SDK (same names as Guide Section 4). Delete it once `sdk/python` lands.

## Confirm with the backend owner (the guide leaves these open)
1. Manifest field names for `network.allowed_domains` and for external agents (`runtime.type`, `endpoint`, `max_messages`).
2. HMAC scheme for the external agent. I used `HMAC_SHA256(secret, "{X-Timestamp}.{body}")` with a 300 s clock window, and request/response JSON `{session_id,text}` / `{text,done}`.
3. Whether `ctx.tool(...)` exists. Trip Planner uses it if present and otherwise falls back to model knowledge and says so.
4. How `marketplace test` sets a tiny budget. My cases use `input.budget_credits`.
5. Whether `ctx.read_file` returns bytes or str (agents handle both).

## Still to do once the real runtime exists
Measure p50/p90 cost per agent and fill the READMEs, re-check prices (price >= p90 / 0.8), regenerate the hand-written Trip Planner and Research Briefer samples from real runs, and set the Research Briefer endpoint URL.
