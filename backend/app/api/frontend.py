"""Routes the bundled frontend (frontend/index.html) calls that are not part of the Section 13 contract:
auth config, the discovery chat ("conversations"), an admin agent list and a synchronous review re-run.
Also serves the frontend itself at / so everything runs on one port."""
import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..auth import SUPABASE_ANON_KEY, SUPABASE_URL, current_user, require_role, supabase_configured
from ..config import MICRO, REPO_ROOT
from ..db import SessionLocal, get_db
from ..models import Agent, AgentVersion, Conversation, Listing, ReviewFinding, ReviewRun, User
from ..services import catalog, review

router = APIRouter(tags=["frontend"])
FRONTEND = REPO_ROOT / "frontend" / "index.html"


@router.get("/", include_in_schema=False)
def index():
    if not FRONTEND.exists():
        raise HTTPException(404, detail="frontend/index.html not found")
    return FileResponse(FRONTEND, media_type="text/html", headers={"cache-control": "no-cache"})


@router.get("/v1/auth/config", summary="Public auth config for the frontend (Supabase URL + anon key, if set)")
def auth_config():
    if not supabase_configured():
        return {"configured": False, "demo_tokens": ["tok_sam", "tok_riya", "tok_admin"]}
    return {"configured": True, "url": SUPABASE_URL, "anon_key": SUPABASE_ANON_KEY}


# ---------- discovery chat ----------

def frontend_card(card: dict) -> dict:
    p = card["price"]
    return {"id": card["id"], "slug": card["slug"], "title": card["title"], "summary": card["summary"],
            "badges": card["badges"], "price_low": p["low_microcredits"] / MICRO,
            "price_high": p["high_microcredits"] / MICRO, "pricing_model": p["pricing_model"],
            "confidence": card["confidence_score"], "sessions": card["confidence_n"],
            "latency_p50": card["duration_p50_s"], "relevance": card["relevance"]}


def conversation_out(c: Conversation) -> dict:
    return {"id": c.id, "title": c.title, "task": c.task, "messages": c.messages, "agents": c.agents,
            "ready": bool(c.task)}


@router.post("/v1/conversations", status_code=201)
def create_conversation(db: DB = Depends(get_db), user: User = Depends(current_user)):
    c = Conversation(user_id=user.id)
    db.add(c)
    db.commit()
    return {"id": c.id}


@router.get("/v1/conversations")
def list_conversations(db: DB = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Conversation).where(Conversation.user_id == user.id, Conversation.task != "")
                      .order_by(Conversation.created_at.desc()).limit(30))
    return {"conversations": [{"id": c.id, "title": c.title} for c in rows]}


@router.get("/v1/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: DB = Depends(get_db), user: User = Depends(current_user)):
    c = db.get(Conversation, conversation_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(404, detail="conversation not found")
    return conversation_out(c)


@router.post("/v1/conversations/{conversation_id}/stream",
             summary="Send a task message; SSE data frames {type: thinking|reply|done}. The unguessable "
                     "conversation id authorises the call (the frontend sends no auth header here).")
async def stream_conversation(conversation_id: str, body: dict):
    text = str(body.get("text", "")).strip()
    db = SessionLocal()
    c = db.get(Conversation, conversation_id)
    if c is None:
        db.close()
        raise HTTPException(404, detail="conversation not found")
    if not text:
        db.close()
        raise HTTPException(422, detail="text required")

    def frame(obj):
        return f"data: {json.dumps(obj, default=str)}\n\n"

    async def gen():
        try:
            c.task = (c.task + " " + text).strip() if c.task else text
            if c.title == "New chat":
                c.title = text[:48] + ("…" if len(text) > 48 else "")
            n_live = len(catalog.live_agents(db))
            yield frame({"type": "thinking", "text": f"Searching {n_live} reviewed agents for: {c.task[:120]}"})
            await asyncio.sleep(0.05)
            cards = [frontend_card(x) for x in catalog.search(db, c.task, {}, 3)]
            if cards:
                best = cards[0]
                reply = (f"I found {len(cards)} agent{'s' if len(cards) > 1 else ''} for this. {best['title']} fits best: "
                         f"confidence {best['confidence']} from {best['sessions']} sessions, "
                         f"{best['price_low']:g}–{best['price_high']:g} credits. Pick one to see a quote.")
            else:
                reply = "No listed agent fits that task yet. Try describing the outcome you want."
            for i in range(0, len(reply), 24):  # stream the reply like a chat model would
                yield frame({"type": "reply", "text": reply[i:i + 24]})
                await asyncio.sleep(0.01)
            c.messages = list(c.messages) + [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            c.agents = cards
            db.commit()
            yield frame({"type": "done", "payload": conversation_out(c)})
        finally:
            db.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


# ---------- admin conveniences ----------

admin = require_role("admin")
_REVIEW_RUNS: dict[tuple, str] = {}


def _display_name(db: DB, version_id: str | None) -> str:
    ver = db.get(AgentVersion, version_id) if version_id else None
    return (ver.manifest_json or {}).get("display_name", "") if ver else ""


@router.get("/v1/admin/agents", summary="All agents with their current version's review decision and findings")
def admin_agents(db: DB = Depends(get_db), user: User = Depends(admin)):
    out = []
    for a in db.scalars(select(Agent).order_by(Agent.created_at)):
        vid = a.current_version_id or db.scalar(select(AgentVersion.id).where(AgentVersion.agent_id == a.id)
                                                .order_by(AgentVersion.created_at.desc()).limit(1))
        run = catalog.latest_review(db, vid) if vid else None
        findings = list(db.scalars(select(ReviewFinding).where(ReviewFinding.review_run_id == run.id))) if run else []
        listing = db.get(Listing, a.id)
        out.append({"id": a.id, "slug": a.slug, "title": listing.title if listing else (_display_name(db, vid) or a.slug), "status": a.status,
                    "mode": a.mode, "version_id": vid, "decision": run.decision if run else None,
                    "findings": [f"[{f.severity}] {f.category}: {f.detail}" for f in findings
                                 if f.severity != "info"]})
    return {"agents": out}


@router.post("/v1/admin/versions/{version_id}/review", summary="Re-run the review pipeline and wait for the result")
async def rerun_review(version_id: str, db: DB = Depends(get_db), user: User = Depends(admin),
                       idempotency_key: str | None = Header(None)):
    ver = db.get(AgentVersion, version_id)
    if ver is None:
        raise HTTPException(404, detail="version not found")
    key = (user.id, version_id, idempotency_key) if idempotency_key else None
    run_id = _REVIEW_RUNS.get(key) if key else None
    if run_id is None:  # same Idempotency-Key -> wait on the same run instead of starting another
        run_id = review.start_review(version_id)
        if key:
            _REVIEW_RUNS[key] = run_id
    for _ in range(600):
        await asyncio.sleep(0.3)
        db.expire_all()
        run = db.get(ReviewRun, run_id)
        if run.status == "done":
            break
    else:
        raise HTTPException(504, detail="review still running; poll GET /v1/creator/agents/{id}/versions/{v}/review")
    findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_run_id == run_id))
    rep = run.report_json or {}
    return {"version_id": version_id, "decision": run.decision, "scores": run.scores_json,
            "confidence": rep.get("confidence_score"),
            "findings": [f"[{f.severity}] {f.category}: {f.detail}" for f in findings if f.severity != "info"],
            "report_card": rep}
