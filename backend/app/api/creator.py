import asyncio
from datetime import timedelta
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from .. import schemas as S
from ..auth import require_role
from ..config import REPO_ROOT
from ..db import get_db
from ..models import (Agent, AgentVersion, LedgerEntry, Listing, Payout, Rating, ReviewFinding, ReviewRun, Session,
                      SessionMessage, TestResult, User, now)
from ..services import catalog, ledger

router = APIRouter(prefix="/v1/creator", tags=["creator"])
creator = require_role("creator")


def _own_agent(db: DB, agent_id: str, user: User) -> Agent:
    a = db.get(Agent, agent_id) or db.scalar(select(Agent).where(Agent.slug == agent_id))
    if a is None or (a.creator_id != user.id and "admin" not in user.roles):
        raise HTTPException(404, detail="agent not found")
    return a


def _version(db: DB, agent: Agent, v: str) -> AgentVersion:
    ver = db.get(AgentVersion, v) or db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent.id,
                                                                         AgentVersion.semver == v))
    if ver is None or ver.agent_id != agent.id:
        raise HTTPException(404, detail="version not found")
    return ver


@router.get("/agents", summary="The creator's agents with version statuses")
def my_agents(db: DB = Depends(get_db), user: User = Depends(creator)):
    out = []
    for a in db.scalars(select(Agent).where(Agent.creator_id == user.id)):
        versions = db.scalars(select(AgentVersion).where(AgentVersion.agent_id == a.id)
                              .order_by(AgentVersion.created_at.desc()))
        listing = db.get(Listing, a.id)
        out.append({"id": a.id, "slug": a.slug, "status": a.status, "mode": a.mode,
                    "current_version_id": a.current_version_id, "title": listing.title if listing else a.slug,
                    "versions": [{"id": v.id, "semver": v.semver, "status": v.status} for v in versions]})
    return out


@router.post("/agents", status_code=201)
def create_agent(body: S.AgentCreate, db: DB = Depends(get_db), user: User = Depends(creator)):
    if db.scalar(select(Agent).where(Agent.slug == body.slug)):
        raise HTTPException(409, detail={"error": "slug_taken", "detail": body.slug})
    a = Agent(creator_id=user.id, slug=body.slug, category=body.category, mode=body.mode, endpoint_url=body.endpoint_url)
    db.add(a)
    db.commit()
    return {"id": a.id, "slug": a.slug, "status": a.status}


@router.post("/agents/{agent_id}/versions", status_code=202,
             summary="Submit a version; the review pipeline starts in the background")
async def submit_version(agent_id: str, body: S.VersionSubmit, db: DB = Depends(get_db),
                         user: User = Depends(creator)):
    from ..services import review
    agent = _own_agent(db, agent_id, user)
    src = None
    if body.source_path:
        p = Path(body.source_path)
        src = p if p.is_absolute() else (REPO_ROOT / p)
        if not src.is_dir():
            raise HTTPException(422, detail={"error": "source_not_found", "detail": str(src)})
    manifest = body.manifest
    if manifest is None and src and (src / "agent.yaml").exists():
        manifest = yaml.safe_load((src / "agent.yaml").read_text())
    if not manifest:
        raise HTTPException(422, detail={"error": "manifest_missing", "detail": "no manifest and no agent.yaml"})
    pricing = manifest.get("pricing") or {}
    ver = AgentVersion(agent_id=agent.id, semver=str(manifest.get("version", "0.0.0")), source_path=str(src) if src else None,
                       image_ref=body.image_ref, manifest_json=manifest, status="submitted",
                       pricing_model=str(pricing.get("model", "per_task")),
                       price_credits=int(pricing.get("price_credits", 0) or 0), limits_json=manifest.get("limits") or {})
    db.add(ver)
    db.commit()
    run = review.start_review(ver.id)
    return {"version_id": ver.id, "review_run_id": run, "status": "in_review"}


@router.get("/agents/{agent_id}/versions/{v}/review", summary="Pipeline status and report card")
def get_review(agent_id: str, v: str, db: DB = Depends(get_db), user: User = Depends(creator)):
    agent = _own_agent(db, agent_id, user)
    ver = _version(db, agent, v)
    run = catalog.latest_review(db, ver.id)
    if run is None:
        return {"version_id": ver.id, "version_status": ver.status, "review": None}
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_run_id == run.id))
    tests = db.scalars(select(TestResult).where(TestResult.review_run_id == run.id))
    return {"version_id": ver.id, "version_status": ver.status, "review": {
        "id": run.id, "status": run.status, "stage": run.stage, "decision": run.decision, "scores": run.scores_json,
        "cost_p50_microcredits": run.cost_p50, "cost_p90_microcredits": run.cost_p90,
        "latency_p50_ms": run.latency_p50, "latency_p95_ms": run.latency_p95, "started_at": run.started_at,
        "finished_at": run.finished_at, "report_card": run.report_json,
        "findings": [{"severity": f.severity, "category": f.category, "detail": f.detail, "status": f.status}
                     for f in findings],
        "tests": [{"case_id": t.case_id, "run_index": t.run_index, "passed": t.passed, "score": t.score,
                   "cost_microcredits": t.cost_microcredits, "latency_ms": t.latency_ms, "detail": t.detail}
                  for t in tests]}}


@router.patch("/agents/{agent_id}/listing")
def patch_listing(agent_id: str, body: S.ListingPatch, db: DB = Depends(get_db), user: User = Depends(creator)):
    agent = _own_agent(db, agent_id, user)
    listing = db.get(Listing, agent.id)
    if listing is None:
        raise HTTPException(409, detail={"error": "no_listing", "detail": "publish a version first"})
    for k in ("title", "summary", "description", "tags", "example_tasks"):
        if getattr(body, k) is not None:
            setattr(listing, k, getattr(body, k))
    if body.price_credits is not None and agent.current_version_id:
        # price changes apply to new sessions only (quotes read the current manifest)
        ver = db.get(AgentVersion, agent.current_version_id)
        m = dict(ver.manifest_json)
        m["pricing"] = dict(m.get("pricing", {})) | {"price_credits": body.price_credits}
        ver.manifest_json, ver.price_credits = m, body.price_credits
    db.commit()
    return catalog.agent_detail(db, agent)


@router.post("/agents/{agent_id}/versions/{v}/publish")
def publish(agent_id: str, v: str, db: DB = Depends(get_db), user: User = Depends(creator)):
    from ..services import review
    agent = _own_agent(db, agent_id, user)
    ver = _version(db, agent, v)
    if ver.status != "approved":
        raise HTTPException(409, detail={"error": "not_approved", "detail": f"version is {ver.status}"})
    review.publish_version(db, agent, ver)
    return catalog.agent_detail(db, agent)


@router.post("/agents/{agent_id}/rollback")
def rollback(agent_id: str, db: DB = Depends(get_db), user: User = Depends(creator)):
    agent = _own_agent(db, agent_id, user)
    approved = [v for v in db.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent.id,
                                                                AgentVersion.status == "approved")
                                      .order_by(AgentVersion.created_at.desc()))]
    prev = next((v for v in approved if v.id != agent.current_version_id), None)
    if prev is None:
        raise HTTPException(409, detail={"error": "no_previous_version", "detail": ""})
    agent.current_version_id = prev.id
    db.commit()
    return {"current_version_id": prev.id, "semver": prev.semver}


def _creator_sessions(db: DB, user: User):
    return (select(Session).join(AgentVersion, AgentVersion.id == Session.agent_version_id)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(Agent.creator_id == user.id, Session.is_review.is_(False), Session.settled_at.is_not(None)))


@router.get("/analytics", summary="Sessions, success, cost, margin, ratings per agent (no buyer identities)")
def analytics(db: DB = Depends(get_db), user: User = Depends(creator)):
    per = {}
    for s in db.scalars(_creator_sessions(db, user)):
        agent_id = db.get(AgentVersion, s.agent_version_id).agent_id
        a = per.setdefault(agent_id, {"agent_id": agent_id, "sessions": 0, "succeeded": 0, "revenue_microcredits": 0,
                                      "cost_microcredits": 0, "earnings_microcredits": 0, "negative_margin_sessions": 0,
                                      "failures": {}})
        a["sessions"] += 1
        a["succeeded"] += s.status == "settled"
        a["revenue_microcredits"] += s.price
        a["cost_microcredits"] += s.cost
        a["earnings_microcredits"] += s.creator_earnings
        a["negative_margin_sessions"] += s.price - s.fee - s.cost < 0
        if s.status != "settled":
            a["failures"][s.status] = a["failures"].get(s.status, 0) + 1
    for agent in db.scalars(select(Agent).where(Agent.creator_id == user.id)):
        listing = db.get(Listing, agent.id)
        a = per.setdefault(agent.id, {"agent_id": agent.id, "sessions": 0, "succeeded": 0, "revenue_microcredits": 0,
                                      "cost_microcredits": 0, "earnings_microcredits": 0,
                                      "negative_margin_sessions": 0, "failures": {}})
        a["title"] = listing.title if listing else agent.slug
        a["status"] = agent.status
        a["success_rate"] = round(a["succeeded"] / a["sessions"], 3) if a["sessions"] else None
        if listing:
            a |= {"confidence_score": listing.confidence_score, "confidence_n": listing.confidence_n,
                  "rating_avg": (listing.live_stats_json or {}).get("rating_avg"),
                  "rating_n": (listing.live_stats_json or {}).get("rating_n", 0),
                  "seeded_live_stats": listing.live_stats_json}
    return {"agents": list(per.values())}


@router.get("/agents/{agent_id}/logs", summary="The creator's own debug logs (ctx.log)")
def logs(agent_id: str, db: DB = Depends(get_db), user: User = Depends(creator)):
    agent = _own_agent(db, agent_id, user)
    rows = db.execute(select(SessionMessage).join(Session, Session.id == SessionMessage.session_id)
                      .join(AgentVersion, AgentVersion.id == Session.agent_version_id)
                      .where(AgentVersion.agent_id == agent.id, SessionMessage.type == "log")
                      .order_by(SessionMessage.id.desc()).limit(200)).scalars()
    return [{"session_id": m.session_id, "ts": m.ts, "text": m.content.get("text")} for m in rows]


@router.get("/earnings", summary="Accrued, in clearing (14 days), available, paid out")
def earnings(db: DB = Depends(get_db), user: User = Depends(creator)):
    acc = ledger.account(db, ledger.CREATOR_PAYABLE, user.id)
    balance = ledger.balance(db, acc.id)
    cutoff = now() - timedelta(days=14)
    clearing = int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_microcredits), 0))
                             .where(LedgerEntry.account_id == acc.id, LedgerEntry.direction == "credit",
                                    LedgerEntry.ts > cutoff)))
    paid = int(db.scalar(select(func.coalesce(func.sum(Payout.amount), 0)).where(Payout.creator_id == user.id)))
    return {"accrued_microcredits": balance + paid, "clearing_microcredits": min(clearing, balance),
            "available_microcredits": max(0, balance - clearing), "paid_out_microcredits": paid,
            "balance_microcredits": balance}


@router.post("/payouts", status_code=201, summary="Simulated payout (clearing window not enforced in the demo)")
def request_payout(body: S.PayoutIn, db: DB = Depends(get_db), user: User = Depends(creator)):
    p = Payout(creator_id=user.id, amount=body.amount_microcredits, status="simulated", paid_at=now())
    db.add(p)
    db.commit()
    try:
        ledger.payout(db, user.id, body.amount_microcredits, p.id)
    except ledger.InsufficientCredits:
        db.delete(p)
        db.commit()
        raise HTTPException(402, detail={"error": "insufficient_credits", "detail": "payable below payout"})
    return {"id": p.id, "status": p.status, "amount_microcredits": p.amount}
