"""Many reviews at once: queued (at most REVIEW_CONCURRENCY running), no DB pool exhaustion, nothing left unsettled."""
import concurrent.futures
import logging
import time

import httpx
import yaml

from app.config import REPO_ROOT, settings

TEMPLATE = REPO_ROOT / "agents" / "_template"
ADMIN = {"authorization": "Bearer tok_admin"}
BAD = ("QueuePool limit", "PendingRollbackError", "finalize failed", "review crashed", "could not be finalized")


class Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage() + (self.format(record) if record.exc_info else ""))


def test_twenty_reviews_at_once_queue_without_pool_errors(live_server):
    from app.db import SessionLocal
    from app.models import ReviewRun, Session
    from app.services import review
    handler = Collect()
    logging.getLogger().addHandler(handler)
    review.STATS.update(peak=0, cases_peak=0)
    m = yaml.safe_load((TEMPLATE / "agent.yaml").read_text())
    body = {"prompt": (TEMPLATE / "prompt.md").read_text(), "cases": (TEMPLATE / "cases.jsonl").read_text()}
    try:
        with httpx.Client(base_url=live_server, timeout=60) as c:
            tok = c.post("/v1/auth/signup", json={"email": "load@example.com", "password": "long enough 1"}).json()["token"]
            h = {"authorization": "Bearer " + tok}
            c.post("/v1/me/creator", headers=h)
            drafts = [c.post("/v1/creator/template-agents", headers=h,
                             json=body | {"manifest": m | {"name": f"load-{i:02d}"}}).json() for i in range(20)]
            submit = lambda d: c.post(f"/v1/creator/agents/{d['agent_id']}/versions/{d['version_id']}/submit",  # noqa: E731
                                      headers=h).json()["review_run_id"]
            with concurrent.futures.ThreadPoolExecutor(20) as ex:
                run_ids = list(ex.map(submit, drafts))
            t0 = time.monotonic()
            assert c.get("/health").status_code == 200 and time.monotonic() - t0 < 2  # still serving requests
            db = SessionLocal()
            try:
                queued = db.query(ReviewRun).filter(ReviewRun.id.in_(run_ids), ReviewRun.stage == "queued").count()
                assert queued > 0  # the rest wait their turn, shown as in review
                for _ in range(1200):
                    db.expire_all()
                    runs = db.query(ReviewRun).filter(ReviewRun.id.in_(run_ids)).all()
                    if all(r.status == "done" for r in runs):
                        break
                    time.sleep(0.5)
                assert all(r.status == "done" for r in runs), [r.stage for r in runs]
                assert [r.decision for r in runs] == ["approved"] * 20, [(r.decision, r.report_json.get("error")) for r in runs]
                stuck = db.query(Session).filter(Session.is_review.is_(True), Session.settled_at.is_(None)).count()
                assert stuck == 0
            finally:
                db.close()
    finally:
        logging.getLogger().removeHandler(handler)
    assert review.STATS["peak"] <= settings.review_concurrency and review.STATS["peak"] >= 2
    assert review.STATS["cases_peak"] <= settings.review_case_concurrency
    assert not [line for line in handler.lines if any(b in line for b in BAD)]


def test_finalize_retries_once_on_a_fresh_session(db, monkeypatch):
    from app.models import Session
    from app.services import ledger, sessions
    s = Session(buyer_id="usr_sam", agent_version_id="agv_trip_planner_1", cap=50_000, status="running", task_text="x")
    db.add(s)
    db.commit()
    ledger.hold(db, s)
    calls, real = [], sessions.finalize

    def flaky(sess, *a, **k):
        calls.append(sess)
        if len(calls) == 1:
            raise RuntimeError("connection dropped")
        return real(sess, *a, **k)
    monkeypatch.setattr(sessions, "finalize", flaky)
    assert sessions.finalize_safely(s.id, "failed", "boom", db=db) is not None
    assert len(calls) == 2 and calls[1] is not db  # retried on its own session
    db.expire_all()
    s = db.get(Session, s.id)
    assert s.settled_at and s.status != "running" and ledger.escrow_balance(db, s.id) == 0
