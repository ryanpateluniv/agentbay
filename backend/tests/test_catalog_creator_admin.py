from fastapi.testclient import TestClient

from app.config import MICRO
from app.main import app
from app.models import Session
from app.services import ledger

SAM = {"authorization": "Bearer tok_sam"}
RIYA = {"authorization": "Bearer tok_riya"}
ADMIN = {"authorization": "Bearer tok_admin"}


def test_search_ranks_relevant_agent_first(db):
    with TestClient(app) as c:
        top = lambda task: c.post("/v1/search", json={"task": task}, headers=SAM).json()["results"][0]["slug"]  # noqa
        assert top("Plan a 5-day trip to Tokyo") == "trip-planner"
        assert top("check my invoices for duplicate charges") == "invoice-auditor"
        assert top("summarize this meeting transcript") == "quick-summarizer"
        assert top("brief me on the EU AI act") == "research-briefer"
        r = c.post("/v1/search", json={"task": "anything", "filters": {"verified_only": True}}, headers=SAM).json()
        assert all("verified" in a["badges"] for a in r["results"])
        r = c.post("/v1/search", json={"task": "trip", "filters": {"max_price_microcredits": 50 * MICRO}},
                   headers=SAM).json()
        assert all(a["price"]["low_microcredits"] <= 50 * MICRO for a in r["results"])
        assert "trip-planner" not in [a["slug"] for a in r["results"]]


def test_quote_and_compare(db):
    with TestClient(app) as c:
        q = c.post("/v1/quotes", json={"agent_id": "trip-planner", "task": "Tokyo"}, headers=SAM).json()
        assert q["low_microcredits"] == q["high_microcredits"] == q["max_microcredits"] == 150 * MICRO  # flat price
        q = c.post("/v1/quotes", json={"agent_id": "research-briefer", "task": "x"}, headers=SAM).json()
        assert q["low_microcredits"] < q["high_microcredits"] < q["max_microcredits"]
        cmp = c.post("/v1/compare", json={"agent_ids": ["trip-planner", "quick-summarizer", "invoice-auditor"]},
                     headers=SAM).json()
        assert len(cmp["agents"]) == 3 and cmp["highlights"]["fastest"] == "agt_quick_summarizer"
        detail = c.get("/v1/agents/research-briefer").json()
        assert "external" in detail["badges"] and detail["confidence_n"] == 230


def test_confidence_lower_bound_prefers_large_samples():
    from app.services.confidence import lower_bound
    assert lower_bound(92, 300) > lower_bound(95, 3)


def test_creator_earnings_payout_and_analytics(db):
    s = Session(buyer_id="usr_sam", agent_version_id="agv_trip_planner_1", cap=200 * MICRO, status="settled")
    db.add(s)
    db.commit()
    ledger.hold(db, s)
    ledger.settle(db, s, "usr_riya", 150 * MICRO, 40 * MICRO)
    with TestClient(app) as c:
        e = c.get("/v1/creator/earnings", headers=RIYA).json()
        assert e["accrued_microcredits"] == 80 * MICRO
        r = c.post("/v1/creator/payouts", json={"amount_microcredits": 30 * MICRO}, headers=RIYA)
        assert r.status_code == 201
        assert c.post("/v1/creator/payouts", json={"amount_microcredits": 999 * MICRO}, headers=RIYA).status_code == 402
        e = c.get("/v1/creator/earnings", headers=RIYA).json()
        assert e["balance_microcredits"] == 50 * MICRO and e["paid_out_microcredits"] == 30 * MICRO
        a = {x["agent_id"]: x for x in c.get("/v1/creator/analytics", headers=RIYA).json()["agents"]}
        assert a["agt_trip_planner"]["sessions"] == 1 and a["agt_trip_planner"]["earnings_microcredits"] == 80 * MICRO
        assert c.get("/v1/creator/earnings", headers=SAM).status_code == 403


def test_admin_fees_prices_metrics(db):
    with TestClient(app) as c:
        assert c.put("/v1/admin/fees", json={"fee_bps": 1500}, headers=SAM).status_code == 403
        assert c.put("/v1/admin/fees", json={"fee_bps": 1500}, headers=ADMIN).json()["fee_bps"] == 1500
        assert ledger.compute_split(100, 0, 100, ledger.fee_bps(db)).F == 15
        c.put("/v1/admin/model-prices", headers=ADMIN, json={"models": [
            {"alias": "fast", "provider_model": "new-fast", "price_in_micro_usd_per_mtok": 1,
             "price_out_micro_usd_per_mtok": 2}]})
        prices = {p["alias"]: p for p in c.get("/v1/admin/model-prices", headers=ADMIN).json()}
        assert prices["fast"]["provider_model"] == "new-fast"
        assert "platform_revenue_microcredits" in c.get("/v1/admin/metrics", headers=ADMIN).json()


def test_topup_idempotent(db):
    with TestClient(app) as c:
        before = c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"]
        h = SAM | {"idempotency-key": "pack-1"}
        c.post("/v1/wallet/topup", json={"pack": "M"}, headers=h)
        c.post("/v1/wallet/topup", json={"pack": "M"}, headers=h)
        assert c.get("/v1/wallet", headers=SAM).json()["balance_microcredits"] == before + 2750 * MICRO
