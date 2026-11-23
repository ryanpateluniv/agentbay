import time

import httpx

from app.config import REPO_ROOT

RIYA = {"authorization": "Bearer tok_riya"}


def submit_and_wait(c, slug, source):
    assert c.post("/v1/creator/agents", json={"slug": slug, "category": "writing"}, headers=RIYA).status_code == 201
    r = c.post(f"/v1/creator/agents/{slug}/versions", json={"source_path": str(source)}, headers=RIYA)
    assert r.status_code == 202, r.text
    vid = r.json()["version_id"]
    for _ in range(300):
        rv = c.get(f"/v1/creator/agents/{slug}/versions/{vid}/review", headers=RIYA).json()
        if rv["review"] and rv["review"]["status"] == "done":
            return rv
        time.sleep(0.3)
    raise AssertionError(rv)


def test_good_agent_approved_and_listed(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        rv = submit_and_wait(c, "echo-agent", REPO_ROOT / "sdk/python/examples/echo_agent")
        review = rv["review"]
        assert review["decision"] == "approved", review
        card = review["report_card"]
        assert card["confidence_score"] > 70 and card["confidence_n"] == 0
        assert all(p["passed"] for p in card["probes"])
        assert len(review["tests"]) >= 4 and review["cost_p90_microcredits"] > 0
        assert [s["stage"] for s in card["stages"]][:2] == ["manifest_lint", "build"]
        detail = c.get("/v1/agents/echo-agent").json()
        assert "beta" in detail["badges"] and detail["report_card"]["decision"] == "approved"


def test_scam_agent_rejected(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        rv = submit_and_wait(c, "scam-agent", REPO_ROOT / "backend/tests/fixtures/scam_agent")
        review = rv["review"]
        assert review["decision"] == "rejected"
        cats = {f["category"] for f in review["findings"] if f["severity"] == "critical"}
        assert {"undeclared_network", "prompt_injection", "data_exfiltration"} <= cats, cats
        assert review["scores"]["S"] == 0
        assert c.get("/v1/agents/scam-agent").status_code == 404  # never listed


def test_lint_rejects_bad_manifest(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        c.post("/v1/creator/agents", json={"slug": "bad-manifest"}, headers=RIYA)
        r = c.post("/v1/creator/agents/bad-manifest/versions", headers=RIYA, json={
            "source_path": str(REPO_ROOT / "sdk/python/examples/echo_agent"),
            "manifest": {"name": "Bad Name", "version": "1", "models_allowed": ["gpt-9"], "pricing": {"model": "free"}}})
        vid = r.json()["version_id"]
        for _ in range(50):
            rv = c.get(f"/v1/creator/agents/bad-manifest/versions/{vid}/review", headers=RIYA).json()
            if rv["review"]["status"] == "done":
                break
            time.sleep(0.1)
        assert rv["review"]["decision"] == "rejected" and len(rv["review"]["report_card"]["fixes"]) >= 3
