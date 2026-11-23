"""Typed wallet history: GET /v1/wallet/history and the `entries` field on GET /v1/wallet."""
import httpx

from app.models import Session
from app.services import ledger

M = 1000


def test_typed_history_for_every_money_movement(db):
    ledger.topup(db, "usr_riya", 100 * M, "t:1", "pur_1")
    s = Session(buyer_id="usr_riya", agent_version_id="agv_trip_planner_1", cap=120 * M, status="running", task_text="x")
    db.add(s)
    db.commit()
    ledger.hold(db, s)
    ledger.settle(db, s, "usr_riya", price=90 * M, cost=2 * M)
    ledger.refund(db, s, "usr_riya", "admin refund")
    h = ledger.wallet_history(db, "usr_riya")
    assert [e["type"] for e in h] == ["refund", "settle", "hold", "topup", "bonus"]
    assert [e["signed_microcredits"] for e in h] == [90 * M, 30 * M, -120 * M, 100 * M, 200 * M]
    assert h[0]["balance_after_microcredits"] == ledger.wallet_balance(db, "usr_riya") == 300 * M
    assert h[-1]["trial"] and h[2]["session_id"] == s.id
    assert [e["type"] for e in ledger.wallet_history(db, "usr_riya", limit=2, before=h[1]["id"])] == ["hold", "topup"]


def test_history_endpoint_and_new_accounts_never_empty(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        tok = c.post("/v1/auth/signup", json={"email": "wallet@example.com", "password": "long enough"}).json()["token"]
        h = {"authorization": "Bearer " + tok}
        first = c.get("/v1/wallet/history", headers=h).json()
        assert first["next_before"] is None
        [e] = first["entries"]
        assert e["type"] == "bonus" and e["label"] == "Trial credits" and e["amount_credits"] == 500
        assert e["balance_after_credits"] == 500 and e["ts"]
        c.post("/v1/wallet/topup", headers=h, json={"credits": 50})
        page = c.get("/v1/wallet/history?limit=1", headers=h).json()
        assert page["entries"][0]["type"] == "topup" and page["entries"][0]["signed_credits"] == 50
        assert page["next_before"] == page["entries"][0]["id"]
        rest = c.get(f"/v1/wallet/history?limit=5&before={page['next_before']}", headers=h).json()
        assert [x["type"] for x in rest["entries"]] == ["bonus"]
        w = c.get("/v1/wallet", headers=h).json()
        assert [x["type"] for x in w["entries"]] == ["topup", "bonus"] and w["history"]
