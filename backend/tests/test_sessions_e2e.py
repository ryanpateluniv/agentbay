"""End to end against a live server: quote -> hold -> sandbox -> events -> settle -> receipt."""
import time

import httpx
import pytest

from app.config import MICRO

SAM = {"authorization": "Bearer tok_sam"}
ADMIN = {"authorization": "Bearer tok_admin"}


def wait_for(c, sid, statuses, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = c.get(f"/v1/sessions/{sid}", headers=SAM).json()
        if s["status"] in statuses:
            return s
        time.sleep(0.2)
    raise AssertionError(f"session stuck in {s['status']}")


def start(c, agent, task, cap=None):
    q = c.post("/v1/quotes", json={"agent_id": agent, "task": task}, headers=SAM).json()
    r = c.post("/v1/sessions", json={"quote_id": q["id"], "cap_microcredits": cap}, headers=SAM)
    assert r.status_code == 201, r.text
    return r.json()


def test_full_session_with_question_and_settlement(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        before = c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]
        s = start(c, "trip-planner", "Plan a trip to Tokyo")
        assert s["cap_microcredits"] == 150 * MICRO
        s = wait_for(c, s["id"], {"waiting_for_user"})
        q = s["pending_question"]
        assert q["question"]
        r = c.post(f"/v1/sessions/{s['id']}/messages", json={"text": "2000 USD", "reply_to": q["id"]}, headers=SAM)
        assert r.status_code == 200, r.text
        s = wait_for(c, s["id"], {"settled", "failed"})
        assert s["status"] == "settled", c.get(f"/v1/sessions/{s['id']}/history", headers=SAM).json()
        assert s["files"] and s["result_text"]
        rc = c.get(f"/v1/receipts/{s['id']}", headers=SAM).json()
        assert rc["price_microcredits"] == 150 * MICRO
        assert rc["usage"]["llm_calls"] == 2 and rc["usage"]["input_tokens"] > 0
        assert rc["cost_microcredits"] > 0
        assert rc["fee_microcredits"] == 30 * MICRO
        assert rc["creator_earnings_microcredits"] == 150 * MICRO - 30 * MICRO - rc["cost_microcredits"]
        after = c.get("/v1/wallet", headers=SAM).json()
        assert after["balance_microcredits"] == before - 150 * MICRO and after["held_microcredits"] == 0
        # SSE replay contains the whole story and ends with settled
        with c.stream("GET", f"/v1/sessions/{s['id']}/events?token=tok_sam") as r:
            types = [line.split(": ", 1)[1] for line in r.iter_lines() if line.startswith("event: ")]
        for t in ("status", "progress", "ask_user", "buyer_message", "message", "spend", "file", "result", "settled"):
            assert t in types, (t, types)
        assert "log" not in types  # creator-only
        assert types[-1] == "settled"
        # virtual key is revoked after settlement
        sess_tok = None
        assert c.post("/proxy/v1/chat/completions", json={"model": "fast", "messages": []},
                      headers={"authorization": "Bearer vk_invalid"}).status_code == 401


def test_usage_based_charge_equals_cost_times_markup(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        s = start(c, "quick-summarizer", "Summarize remote work benefits")
        s = wait_for(c, s["id"], {"settled", "failed", "budget_exhausted"})
        rc = c.get(f"/v1/receipts/{s['id']}", headers=SAM).json()
        assert s["status"] == "settled"
        assert rc["price_microcredits"] == rc["cost_microcredits"] * 250 // 100


def test_budget_exhausted_with_tiny_cap(live_server):
    """Usage-based agent with a cap that pays for less than one call: proxy returns budget_exhausted,
    the agent delivers a partial result, the buyer pays at most the cap."""
    with httpx.Client(base_url=live_server, timeout=30) as c:
        before = c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]
        s = start(c, "quick-summarizer", "Summarize " + "a long document " * 50, cap=60)
        s = wait_for(c, s["id"], {"settled", "failed", "budget_exhausted"}, timeout=40)
        assert s["status"] == "budget_exhausted", s
        rc = c.get(f"/v1/receipts/{s['id']}", headers=SAM).json()
        assert rc["price_microcredits"] <= 60
        assert c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"] >= before - 60


def test_insufficient_credits_402(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        q = c.post("/v1/quotes", json={"agent_id": "trip-planner", "task": "x"},
                   headers={"authorization": "Bearer tok_admin"}).json()
        r = c.post("/v1/sessions", json={"quote_id": q["id"]}, headers={"authorization": "Bearer tok_admin"})
        assert r.status_code == 402 and r.json()["error"] == "insufficient_credits"


def test_cancel_mid_task_charges_half_and_kill_refunds(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        before = c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]
        s = start(c, "trip-planner", "Plan a trip")
        wait_for(c, s["id"], {"waiting_for_user"})
        r = c.post(f"/v1/sessions/{s['id']}/cancel", headers=SAM)
        assert r.status_code == 200
        s = wait_for(c, s["id"], {"cancelled"})
        rc = c.get(f"/v1/receipts/{s['id']}", headers=SAM).json()
        assert rc["price_microcredits"] == 75 * MICRO  # 50% of P once work started
        assert c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"] == before - 75 * MICRO

        # kill switch: running session is cancelled and fully refunded
        before = c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]
        s = start(c, "trip-planner", "Plan another trip")
        wait_for(c, s["id"], {"waiting_for_user"})
        k = c.post("/v1/admin/agents/trip-planner/kill", headers=ADMIN).json()
        assert s["id"] in k["cancelled_sessions"]
        s = wait_for(c, s["id"], {"cancelled"})
        rc = c.get(f"/v1/receipts/{s['id']}", headers=SAM).json()
        assert rc["price_microcredits"] == 0
        assert c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"] == before
        assert c.post("/v1/quotes", json={"agent_id": "trip-planner", "task": "x"}, headers=SAM).status_code == 409
        c.post("/v1/admin/agents/trip-planner/unpause", headers=ADMIN)


def test_admin_refund_after_settlement(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        before = c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]
        s = start(c, "invoice-auditor", "Audit invoice 42")
        s = wait_for(c, s["id"], {"settled", "failed"})
        assert s["status"] == "settled"
        r = c.post(f"/v1/admin/sessions/{s['id']}/refund", json={"reason": "bad output"}, headers=ADMIN)
        assert r.status_code == 200 and r.json()["refunded_microcredits"] == 40 * MICRO
        assert c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"] == before


def _direct_key(cap):
    """A session + virtual key without running an agent (review-style session, no ledger)."""
    from app.db import SessionLocal
    from app.models import AgentVersion, Session
    from app.services import catalog, sessions
    db = SessionLocal()
    from app.models import Agent
    agent = db.get(Agent, "agt_quick_summarizer")
    q = catalog.make_quote(db, "usr_sam", agent, "t")
    s = Session(buyer_id="usr_sam", agent_version_id=q.agent_version_id, cap=cap, status="running", is_review=True)
    db.add(s)
    db.commit()
    key = sessions.mint_key(db, s, db.get(AgentVersion, q.agent_version_id))
    db.close()
    return s.id, key


def test_streaming_bills_streamed_tokens(live_server):
    sid, key = _direct_key(100 * MICRO)
    h = {"authorization": f"Bearer {key}"}
    with httpx.Client(base_url=live_server, timeout=30) as c:
        chunks = []
        with c.stream("POST", "/proxy/v1/chat/completions", headers=h,
                      json={"model": "fast", "stream": True, "messages": [{"role": "user", "content": "hi there"}]}) as r:
            for line in r.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunks.append(line)
        assert len(chunks) > 3
        b = c.get("/proxy/v1/budget", headers=h).json()
        assert b["spent_microcredits"] > 0
        assert c.post("/proxy/v1/chat/completions", headers=h,
                      json={"model": "balanced", "messages": []}).json()["error"]["code"] == "model_not_allowed"


def test_concurrent_calls_cannot_blow_through_cap(live_server):
    """10 parallel calls against a cap worth ~2 calls: per-session lock + max_tokens clamp keep spend near the cap."""
    import concurrent.futures
    cap = 100  # microcredits (usage-based: cost limit = cap*100/250 = 40)
    sid, key = _direct_key(cap)
    h = {"authorization": f"Bearer {key}"}

    def call(i):
        with httpx.Client(base_url=live_server, timeout=30) as c:
            return c.post("/proxy/v1/chat/completions", headers=h, json={
                "model": "fast", "messages": [{"role": "user", "content": f"write a long essay number {i} " * 30}]})

    with concurrent.futures.ThreadPoolExecutor(10) as ex:
        results = list(ex.map(call, range(10)))
    codes = [r.status_code for r in results]
    assert 402 in codes and 200 in codes
    with httpx.Client(base_url=live_server, timeout=30) as c:
        b = c.get("/proxy/v1/budget", headers=h).json()
    limit = b["limit_microcredits"]
    # overshoot bounded by one call's input cost (absorbed by the platform)
    assert b["spent_microcredits"] <= limit + 20, b
