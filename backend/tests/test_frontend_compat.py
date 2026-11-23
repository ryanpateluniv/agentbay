"""Replays the calls frontend/index.html makes, exactly as it makes them."""
import json
import time

import httpx

SAM = {"authorization": "Bearer tok_sam"}
ADMIN = {"authorization": "Bearer tok_admin"}


def test_frontend_flow(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        r = c.get("/")
        assert r.status_code == 200 and "<html" in r.text.lower()
        assert c.get("/v1/auth/config").json()["configured"] is False

        w = c.get("/v1/wallet", headers=SAM).json()
        assert w["balance_credits"] == 5000 and w["granted_credits"] == 5000 and w["used_credits"] == 0 and w["user"]
        c.post("/v1/wallet/topup", headers=SAM, json={"credits": 500})
        assert c.get("/v1/wallet", headers=SAM).json()["balance_credits"] == 5500

        conv = c.post("/v1/conversations", headers=SAM).json()["id"]
        frames = []
        with c.stream("POST", f"/v1/conversations/{conv}/stream", json={"text": "Plan a 5 day trip to Tokyo"}) as r:
            for part in r.read().decode().split("\n\n"):
                line = next((x for x in part.split("\n") if x.startswith("data: ")), None)
                if line:
                    frames.append(json.loads(line[6:]))
        assert [f["type"] for f in frames][0] == "thinking" and frames[-1]["type"] == "done"
        payload = frames[-1]["payload"]
        assert payload["ready"] and payload["agents"][0]["title"] == "Trip Planner"
        agent = payload["agents"][0]
        assert {"price_low", "price_high", "pricing_model", "confidence", "sessions", "latency_p50"} <= set(agent)
        assert c.get("/v1/conversations", headers=SAM).json()["conversations"][0]["id"] == conv
        assert c.get(f"/v1/conversations/{conv}", headers=SAM).json()["messages"][0]["role"] == "user"

        q = c.post("/v1/quotes", headers=SAM, json={"agent_id": agent["id"], "task": payload["task"]}).json()
        assert q["maximum"] == 150
        ses = c.post("/v1/sessions", headers=SAM,
                     json={"agent_id": agent["id"], "task": payload["task"], "cap_credits": q["maximum"]}).json()
        sid = ses["session_id"]

        # EventSource-style: ?access_token=, unnamed frames, flat payloads
        seen = []
        with c.stream("GET", f"/v1/sessions/{sid}/events?access_token=tok_sam") as r:
            for line in r.iter_lines():
                assert not line.startswith("event:")
                if not line.startswith("data: "):
                    continue
                d = json.loads(line[6:])
                seen.append(d)
                if d["type"] == "ask_user":
                    assert d["text"]
                    c.post(f"/v1/sessions/{sid}/messages", headers=SAM, json={"text": "Continue"})
                if d["type"] == "settled":
                    break
        types = [d["type"] for d in seen]
        assert "spend" in types and any(d.get("spent_credits") is not None for d in seen)
        assert any(d["type"] == "status" and d["text"] for d in seen)
        # after settlement the stream answers 204 so EventSource stops reconnecting
        assert c.get(f"/v1/sessions/{sid}/events?access_token=tok_sam",
                     headers={"last-event-id": str(seen[-1]["id"])}).status_code == 204

        rc = c.get(f"/v1/receipts/{sid}", headers=SAM).json()
        assert rc["price_credits"] == 150 and rc["fee_credits"] == 30 and rc["cap_credits"] == 150
        assert rc["earnings_credits"] + rc["fee_credits"] + min(rc["cost_credits"], 120) == 150
        lst = c.get("/v1/sessions", headers=SAM).json()["sessions"]
        assert lst[0]["id"] == sid and lst[0]["title"] == "Trip Planner" and lst[0]["active"] is False

        a = c.get("/v1/creator/analytics", headers={"authorization": "Bearer tok_riya"}).json()
        e = c.get("/v1/creator/earnings", headers={"authorization": "Bearer tok_riya"}).json()
        assert a["sessions"] >= 1 and a["platform_revenue_credits"] >= 30 and e["clearing_days"] == 14
        assert e["clearing_credits"] > 0 and e["available_credits"] == e["available_microcredits"] / 1000

        ag = c.get("/v1/admin/agents", headers=ADMIN).json()["agents"]
        assert {"id", "title", "status", "decision", "findings", "version_id"} <= set(ag[0])
        k = c.post("/v1/admin/agents/research-briefer/kill", headers=ADMIN).json()
        assert k["refunded_sessions"] == []
        c.post("/v1/admin/agents/research-briefer/unpause", headers=ADMIN)


def test_admin_rerun_review_sync(live_server):
    with httpx.Client(base_url=live_server, timeout=120) as c:
        r = c.post("/v1/admin/versions/agv_quick_summarizer_1/review", headers=ADMIN)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["decision"] == "approved" and isinstance(body["findings"], list) and body["confidence"] > 0


def test_library_agents_have_example_tasks(live_server):
    agents = httpx.get(f"{live_server}/v1/agents").json()
    assert agents and all(a["example_tasks"] for a in agents)


def test_idempotency_keys_dedupe_double_clicks(live_server):
    import concurrent.futures
    with httpx.Client(base_url=live_server, timeout=120) as c:
        before = c.get("/v1/wallet", headers=SAM).json()["balance_credits"]
        h = SAM | {"idempotency-key": "click-1"}
        for _ in range(2):
            c.post("/v1/wallet/topup", headers=h, json={"credits": 500})
        assert c.get("/v1/wallet", headers=SAM).json()["balance_credits"] == before + 500
        body = {"agent_id": "invoice-auditor", "task": "Audit invoice 7", "cap_credits": 40}
        a = c.post("/v1/sessions", headers=SAM | {"idempotency-key": "start-1"}, json=body).json()
        b = c.post("/v1/sessions", headers=SAM | {"idempotency-key": "start-1"}, json=body).json()
        assert a["session_id"] == b["session_id"]
        h = ADMIN | {"idempotency-key": "review-1"}
        with concurrent.futures.ThreadPoolExecutor(2) as ex:
            r1, r2 = ex.map(lambda _: c.post("/v1/admin/versions/agv_quick_summarizer_1/review", headers=h), range(2))
        assert r1.json()["report_card"] == r2.json()["report_card"]


def test_flat_event_texts():
    from app.api.buyer import flatten_event
    base = {"id": 1, "role": "buyer", "ts": "t"}
    assert flatten_event(base | {"type": "buyer_confirm", "data": {"confirm_id": "c1", "approved": True}})["text"] == "Approved"
    flat = flatten_event(base | {"type": "confirm", "role": "agent", "data": {"id": "c_9", "action": "Book it?"}})
    assert flat["id"] == "c_9" and flat["text"] == "Book it?"  # confirm_id for POST /confirm
    assert flatten_event(base | {"type": "spend", "data": {"spent_microcredits": 1500}})["spent_credits"] == 1.5


def test_fields_read_by_merged_screens(live_server):
    """Compare, report card, wallet and creator screens read these fields (see docs/FRONTEND_REVIEW.md)."""
    with httpx.Client(base_url=live_server, timeout=30) as c:
        card = c.get("/v1/agents").json()[0]
        assert card["creator_name"] == "Riya" and card["description"]
        d = c.get("/v1/agents/trip-planner").json()
        assert {"stages", "tests"} <= set(d["report_card"]) and "success_rate" in d["live_stats"]
        assert c.get("/v1/wallet", headers=SAM).json()["email"] == "sam@demo.dev"
        e = c.get("/v1/creator/earnings", headers={"authorization": "Bearer tok_riya"}).json()
        assert e["available_credits"] == e["available_microcredits"] / 1000


def test_creator_form_manifest_is_laid_over_agent_yaml(live_server):
    riya = {"authorization": "Bearer tok_riya"}
    with httpx.Client(base_url=live_server, timeout=30) as c:
        aid = c.post("/v1/creator/agents", headers=riya, json={"slug": "echo-form", "category": "writing"}).json()["id"]
        form = {"name": "echo-form", "display_name": "Echo Form", "version": "1.0.1", "summary": "", "tags": [],
                "pricing": {"model": "per_task", "price_credits": 8}}
        r = c.post(f"/v1/creator/agents/{aid}/versions", headers=riya,
                   json={"manifest": form, "source_path": "sdk/python/examples/echo_agent"})
        assert r.status_code == 202, r.text
        vid = r.json()["version_id"]
        for _ in range(240):
            rv = c.get(f"/v1/creator/agents/{aid}/versions/{vid}/review", headers=riya).json()["review"]
            if rv["status"] == "done":
                break
            time.sleep(0.5)
        stages = [s["stage"] for s in rv["report_card"]["stages"]]
        assert rv["decision"] == "approved" and len(stages) == 9 and stages[0] == "manifest_lint"


def test_seed_submits_invoicegenius_pro_for_review(db, monkeypatch):
    from pathlib import Path

    from app.config import REPO_ROOT, settings
    from app.models import AgentVersion
    from app.seed import seed
    if not (REPO_ROOT / "agents" / "invoicegenius-pro" / "agent.yaml").exists():
        return
    monkeypatch.setattr(settings, "agents_dir", Path(REPO_ROOT / "agents"))
    seed(reset=True)
    db.expire_all()
    v = db.get(AgentVersion, "agv_invoicegenius_pro_1")
    assert v.status == "submitted" and v.manifest_json["support_contact"]
    monkeypatch.undo()
    seed(reset=True)
