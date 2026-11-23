"""Scripted demo against a running backend (make dev). Mirrors the Section 16 demo script.

    python scripts/demo.py [base_url]
"""
import json
import sys
import threading
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SAM, RIYA, ADMIN = ({"authorization": f"Bearer {t}"} for t in ("tok_sam", "tok_riya", "tok_admin"))
c = httpx.Client(base_url=BASE, timeout=60)
cr = lambda m: f"{m / 1000:,.3f} cr"  # noqa: E731


def step(title):
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


step("1. Buyer searches")
res = c.post("/v1/search", json={"task": "Plan a 5-day trip to Tokyo under $2,000"}, headers=SAM).json()["results"]
for a in res:
    p = a["price"]
    print(f"  {a['title']:<18} conf {a['confidence_score']} (n={a['confidence_n']}) {a['badges']}  "
          f"price {cr(p['low_microcredits'])}-{cr(p['high_microcredits'])}  p50 {a['duration_p50_s']}s")

step("2. Quote and start a session (hold credits)")
print("  wallet before:", cr(c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]))
q = c.post("/v1/quotes", json={"agent_id": "trip-planner", "task": "Plan a 5-day trip to Tokyo under $2,000"},
           headers=SAM).json()
s = c.post("/v1/sessions", json={"quote_id": q["id"]}, headers=SAM).json()
print(f"  session {s['id']}  cap {cr(s['cap_microcredits'])}")

step("3. Live event stream (SSE)")
with c.stream("GET", f"/v1/sessions/{s['id']}/events?token=tok_sam", timeout=None) as r:
    event = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            d = json.loads(line[6:])["data"]
            if event == "spend":
                print(f"  [spend]   {cr(d['spent_microcredits'])} of {cr(d['cap_microcredits'])} "
                      f"({d['input_tokens']} in / {d['output_tokens']} out tokens)")
            elif event == "ask_user":
                answer = (d.get("options") or [None])[0] or "2000 USD total, I love food and museums"
                print(f"  [ask]     {d['question']}  options={d.get('options')}")
                print(f"  [buyer]   {answer}")
                threading.Thread(target=lambda d=d, a=answer: c.post(f"/v1/sessions/{s['id']}/messages", headers=SAM,
                                 json={"text": a, "reply_to": d["id"]})).start()
            elif event == "confirm":
                print(f"  [confirm] {d['action']}  -> approved")
                threading.Thread(target=lambda d=d: c.post(f"/v1/sessions/{s['id']}/confirm", headers=SAM,
                                 json={"confirm_id": d["id"], "approved": True})).start()
            elif event == "settled":
                rc = d
                break
            elif event in ("status", "progress", "message", "file", "result", "error"):
                short = {k: (str(v)[:80]) for k, v in d.items() if k not in ("files",)}
                print(f"  [{event:<8}] {short}")

step("4. Receipt and split")
for line in rc["lines"]:
    print(f"  {line['label']:<36} {cr(line['amount_microcredits'])}")
print("  usage:", rc["usage"])
print("  wallet after:", cr(c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]))
print("  creator earnings:", cr(c.get("/v1/creator/earnings", headers=RIYA).json()["accrued_microcredits"]))

step("5. Trust: submit a scam agent")
scam = sys.argv[2] if len(sys.argv) > 2 else "tests/fixtures/scam_agent"
c.post("/v1/creator/agents", json={"slug": f"scam-{int(time.time())}", "category": "research"}, headers=RIYA)
slug = c.get("/v1/creator/agents", headers=RIYA).json()[-1]["slug"]
import os  # noqa: E402
v = c.post(f"/v1/creator/agents/{slug}/versions", json={"source_path": os.path.abspath(scam)}, headers=RIYA).json()
while True:
    rv = c.get(f"/v1/creator/agents/{slug}/versions/{v['version_id']}/review", headers=RIYA).json()["review"]
    if rv["status"] == "done":
        break
    print(f"  review stage: {rv['stage']}")
    time.sleep(1.5)
print(f"  decision: {rv['decision'].upper()}  scores {rv['scores']}")
for f in rv["findings"]:
    print(f"   - [{f['severity']}] {f['category']}: {f['detail']}")

step("6. Kill switch on a running session")
q = c.post("/v1/quotes", json={"agent_id": "trip-planner", "task": "Plan Paris"}, headers=SAM).json()
s2 = c.post("/v1/sessions", json={"quote_id": q["id"]}, headers=SAM).json()
time.sleep(2)
k = c.post("/v1/admin/agents/trip-planner/kill", headers=ADMIN).json()
print("  cancelled:", k["cancelled_sessions"])
time.sleep(1)
print("  refunded receipt price:", cr(c.get(f"/v1/receipts/{s2['id']}", headers=SAM).json()["price_microcredits"]))
c.post("/v1/admin/agents/trip-planner/unpause", headers=ADMIN)
print("\nDone.")
