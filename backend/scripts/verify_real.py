"""Run ONE real Invoice Auditor session and verify metering against OpenAI's own usage numbers.

    LLM_MODE=real make dev            # terminal 1
    python scripts/verify_real.py     # terminal 2 (from backend/)
"""
import base64
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import case, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import REPO_ROOT, settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import LedgerEntry, LedgerTxn, ModelPrice, UsageEvent  # noqa: E402
from app.services import ledger  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
INVOICE = REPO_ROOT / "agents/invoice-auditor/tests/files/inv001.txt"
SAM = {"authorization": "Bearer tok_sam"}
c = httpx.Client(base_url=BASE, timeout=120)

health = c.get("/health").json()
print("server:", health)
assert health["llm_mode"] == "real", "start the server with LLM_MODE=real"

# 1. one real session
q = c.post("/v1/quotes", json={"agent_id": "invoice-auditor", "task": "Audit this invoice"}, headers=SAM).json()
s = c.post("/v1/sessions", headers=SAM, json={"quote_id": q["id"], "files": [
    {"name": INVOICE.name, "content_base64": base64.b64encode(INVOICE.read_bytes()).decode()}]}).json()
sid = s["id"]
print(f"\nsession {sid}: invoice {INVOICE.name}, cap {s['cap_microcredits']} µc")
answered = set()
while True:
    s = c.get(f"/v1/sessions/{sid}", headers=SAM).json()
    pq = s.get("pending_question")
    if pq and pq["id"] not in answered:
        print("  agent asks:", pq["question"], "-> answering 'No'")
        c.post(f"/v1/sessions/{sid}/messages", json={"text": "No", "reply_to": pq["id"]}, headers=SAM)
        answered.add(pq["id"])
    if s["settled_at"]:
        break
    time.sleep(0.5)
print("  status:", s["status"], "| result:", (s["result_text"] or "")[:150])
for ev in c.get(f"/v1/sessions/{sid}/history", headers=SAM).json():
    if ev["type"] == "error":
        print("  error event:", ev["data"])

# 2. our usage events vs OpenAI's usage blocks (raw log written straight from the provider responses)
db = SessionLocal()
ours = list(db.scalars(select(UsageEvent).where(UsageEvent.session_id == sid).order_by(UsageEvent.ts)))
raw = {}
for line in (settings.data_dir / "upstream_usage.jsonl").read_text().splitlines():
    r = json.loads(line)
    if r["session_id"] == sid:
        raw[r["id"]] = r
prices = {}
for p in db.scalars(select(ModelPrice).order_by(ModelPrice.id)):
    prices[p.alias] = p
print(f"\n{'alias':<7} {'provider model':<15} {'request id':<38} {'in':>6} {'cached':>6} {'out':>6} "
      f"{'reason':>6} | {'openai in':>9} {'cached':>6} {'out':>6} | match  {'cost µc':>7}")
real_usd = Decimal(0)
all_match = True
for e in ours:
    r = raw.get(e.provider_request_id, {})
    u = r.get("usage") or {}
    o_in, o_out = u.get("prompt_tokens"), u.get("completion_tokens")
    o_cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    match = (o_in, o_out, o_cached) == (e.input_tokens, e.output_tokens, e.cached_tokens)
    all_match &= match
    p = prices[e.model_alias]
    usd = (Decimal(o_in - o_cached) * p.price_in + Decimal(o_cached) * (p.price_cached_in or 0)
           + Decimal(o_out) * p.price_out) / Decimal(10**12)
    real_usd += usd
    print(f"{e.model_alias:<7} {e.provider_model:<15} {str(e.provider_request_id):<38} {e.input_tokens:>6} "
          f"{e.cached_tokens:>6} {e.output_tokens:>6} {e.reasoning_tokens:>6} | {o_in:>9} {o_cached:>6} {o_out:>6} | "
          f"{'OK' if match else 'DIFF':<5} {e.cost_microcredits:>7}")
print(f"usage events: {len(ours)}, provider responses: {len(raw)}, all match: {all_match and len(ours) == len(raw)}")

# 3. receipt and ledger
rc = c.get(f"/v1/receipts/{sid}", headers=SAM).json()
print(f"\nreceipt: P={rc['price_microcredits']} C={rc['cost_microcredits']} F={rc['fee_microcredits']} "
      f"E={rc['creator_earnings_microcredits']} released={rc['released_microcredits']} (µc)")
print(f"  check E = max(0, P-F-C): {rc['creator_earnings_microcredits'] == max(0, rc['price_microcredits'] - rc['fee_microcredits'] - rc['cost_microcredits'])}")
print(f"  check C = sum(usage events): {rc['cost_microcredits'] == sum(e.cost_microcredits for e in ours)}")
escrow = ledger.escrow_balance(db, sid)
signed = func.sum(case((LedgerEntry.direction == "credit", LedgerEntry.amount_microcredits),
                       else_=-LedgerEntry.amount_microcredits))
txns = list(db.scalars(select(LedgerTxn).where(LedgerTxn.ref_id == sid)))
per_txn = [db.scalar(select(signed).where(LedgerEntry.txn_id == t.id)) for t in txns]
print(f"  escrow balance after settle: {escrow}")
print(f"  session txns {[t.kind for t in txns]} each sum to: {per_txn}")
print(f"  whole ledger sums to: {db.scalar(select(signed))}")

# 4. dollars
print(f"\nreal provider spend for this session: ${real_usd:.6f} (at the prices in config/models.yaml)")
print(f"server-side guard (whole run so far): {c.get('/v1/admin/metrics', headers={'authorization': 'Bearer tok_admin'}).json()['real_mode_guard']}")
