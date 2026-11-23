import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from .. import schemas as S
from ..auth import require_role
from ..db import get_db
from ..models import (Agent, AgentVersion, AuditLog, Dispute, ModelPrice, PlatformSetting, ReviewFinding, Session,
                      ToolPrice, UsageEvent, User)
from ..config import settings
from ..services import catalog, ledger, review, sessions, upstream
from ..services.sessions import SessionError

router = APIRouter(prefix="/v1/admin", tags=["admin"])
admin = require_role("admin")


def audit(db: DB, user: User, action: str, target: str, details: dict | None = None):
    db.add(AuditLog(actor_id=user.id, action=action, target=target, details=details or {}))
    db.commit()


@router.get("/review-queue")
def review_queue(db: DB = Depends(get_db), user: User = Depends(admin)):
    rows = db.scalars(select(AgentVersion).where(AgentVersion.status.in_(("submitted", "in_review")))
                      .order_by(AgentVersion.created_at))
    out = []
    for v in rows:
        run = catalog.latest_review(db, v.id)
        out.append({"version_id": v.id, "agent_id": v.agent_id, "semver": v.semver, "status": v.status,
                    "category": v.manifest_json.get("category"), "review_stage": run.stage if run else None,
                    "review_decision": run.decision if run else None})
    return out


@router.post("/versions/{v}/decision")
def decide(v: str, body: S.DecisionIn, db: DB = Depends(get_db), user: User = Depends(admin)):
    ver = db.get(AgentVersion, v)
    if ver is None:
        raise HTTPException(404, detail="version not found")
    ver.status = "approved" if body.decision == "approved" else "rejected"
    run = catalog.latest_review(db, v)
    if run:
        run.decision = body.decision
        run.report_json = dict(run.report_json or {}) | {"reviewer_notes": body.notes, "decision": body.decision,
                                                         "awaiting_admin": False}
        if body.decision != "approved":
            db.add(ReviewFinding(review_run_id=run.id, severity="high", category="admin_review",
                                 detail=body.notes or f"Admin decision: {body.decision}"))
    db.commit()
    if body.decision == "approved":
        review.publish_version(db, db.get(Agent, ver.agent_id), ver)  # live and listed
    audit(db, user, "version_decision", v, body.model_dump())
    return {"version_id": v, "status": ver.status}


@router.post("/agents/{agent_id}/kill", summary="Kill switch: pause the agent, cancel and refund running sessions")
async def kill(agent_id: str, db: DB = Depends(get_db), user: User = Depends(admin)):
    agent = db.get(Agent, agent_id) or db.scalar(select(Agent).where(Agent.slug == agent_id))
    if agent is None:
        raise HTTPException(404, detail="agent not found")
    ids = sessions.kill_agent(db, agent)
    for sid in ids:
        s = db.get(Session, sid)
        try:
            await sessions.cancel(db, s, kill_refund=True)
        except SessionError:
            pass
    await asyncio.sleep(0.5)
    audit(db, user, "kill_switch", agent.id, {"cancelled_sessions": ids})
    return {"agent_id": agent.id, "status": agent.status, "cancelled_sessions": ids, "refunded_sessions": ids}


@router.post("/agents/{agent_id}/unpause")
def unpause(agent_id: str, db: DB = Depends(get_db), user: User = Depends(admin)):
    agent = db.get(Agent, agent_id) or db.scalar(select(Agent).where(Agent.slug == agent_id))
    if agent is None:
        raise HTTPException(404, detail="agent not found")
    agent.status = "live"
    db.commit()
    audit(db, user, "unpause", agent.id)
    return {"agent_id": agent.id, "status": agent.status}


@router.post("/sessions/{session_id}/refund", summary="Refund button: return the charge P of a settled session")
def refund(session_id: str, body: S.RefundIn, db: DB = Depends(get_db), user: User = Depends(admin)):
    s = db.get(Session, session_id)
    if s is None:
        raise HTTPException(404, detail="session not found")
    try:
        amount = sessions.admin_refund(db, s, body.reason)
    except SessionError as e:
        raise HTTPException(e.status, detail={"error": e.code, "detail": e.detail})
    audit(db, user, "refund", s.id, {"amount_microcredits": amount, "reason": body.reason})
    return {"session_id": s.id, "refunded_microcredits": amount}


@router.put("/model-prices")
def put_prices(body: S.PricesIn, db: DB = Depends(get_db), user: User = Depends(admin)):
    for m in body.models:  # new rows; the proxy uses the newest effective price per alias
        db.add(ModelPrice(alias=m.alias, provider_model=m.provider_model, price_in=m.price_in_micro_usd_per_mtok,
                          price_out=m.price_out_micro_usd_per_mtok))
    for t in body.tools:
        db.add(ToolPrice(tool=t.tool, unit=t.unit, price=t.price_microcredits))
    db.commit()
    audit(db, user, "prices", "model_prices", body.model_dump())
    return {"ok": True}


@router.get("/model-prices")
def get_prices(db: DB = Depends(get_db), user: User = Depends(admin)):
    from ..services.proxy import current_price
    aliases = {r for r in db.scalars(select(ModelPrice.alias))}
    return [{"alias": a, "provider_model": p.provider_model, "price_in_micro_usd_per_mtok": p.price_in,
             "price_out_micro_usd_per_mtok": p.price_out} for a in sorted(aliases) if (p := current_price(db, a))]


@router.put("/fees")
def put_fees(body: S.FeesIn, db: DB = Depends(get_db), user: User = Depends(admin)):
    for key in ("fee_bps", "markup_bps"):
        val = getattr(body, key)
        if val is not None:
            row = db.get(PlatformSetting, key) or PlatformSetting(key=key, value=val)
            row.value = val
            db.merge(row)
    db.commit()
    audit(db, user, "fees", "platform_settings", body.model_dump())
    return {k: (db.get(PlatformSetting, k).value if db.get(PlatformSetting, k) else None)
            for k in ("fee_bps", "markup_bps")}


@router.get("/disputes")
def disputes(db: DB = Depends(get_db), user: User = Depends(admin)):
    return [{"id": d.id, "session_id": d.session_id, "reason": d.reason, "status": d.status,
             "resolution": d.resolution, "refund_amount_microcredits": d.refund_amount}
            for d in db.scalars(select(Dispute))]


@router.post("/disputes/{dispute_id}/resolve")
def resolve(dispute_id: str, body: S.ResolveIn, db: DB = Depends(get_db), user: User = Depends(admin)):
    d = db.get(Dispute, dispute_id)
    if d is None:
        raise HTTPException(404, detail="dispute not found")
    if body.resolution == "refund":
        d.refund_amount = sessions.admin_refund(db, db.get(Session, d.session_id), f"dispute {d.id}")
    d.status, d.resolution = "resolved", body.resolution
    db.commit()
    audit(db, user, "dispute_resolve", d.id, body.model_dump())
    return {"id": d.id, "status": d.status, "resolution": d.resolution, "refund_amount_microcredits": d.refund_amount}


@router.get("/metrics")
def metrics(db: DB = Depends(get_db), user: User = Depends(admin)):
    settled = select(Session).where(Session.settled_at.is_not(None), Session.is_review.is_(False))
    rows = list(db.scalars(settled))
    by_status = {}
    for s in rows:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    return {
        "sessions_settled": len(rows), "sessions_by_status": by_status,
        "gross_charged_microcredits": sum(s.price for s in rows),
        "platform_revenue_microcredits": ledger.account_balance(db, ledger.PLATFORM_REVENUE),
        "provider_cost_microcredits": ledger.account_balance(db, ledger.PROVIDER_COST),
        "creator_payable_microcredits": sum(s.creator_earnings for s in rows),
        "usage_events": int(db.scalar(select(func.count()).select_from(UsageEvent))),
        "llm_mode": settings.llm_mode, "real_mode_guard": upstream.guard.snapshot(),
        "failure_rate": round(sum(1 for s in rows if s.status in ("failed", "timed_out")) / len(rows), 3) if rows else 0,
    }
