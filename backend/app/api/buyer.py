import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .. import schemas as S
from ..auth import current_user
from ..config import MICRO
from ..db import get_db
from ..models import Agent, AgentVersion, CreditPurchase, Dispute, Rating, Session, SessionFile, Subscription, User
from ..services import catalog, events, ledger, sessions
from ..services.sessions import SessionError

router = APIRouter(prefix="/v1", tags=["buyer"])

PACKS = {"S": (1000, 1000), "M": (2500, 2750), "L": (5000, 5750)}  # usd cents, credits


def _agent(db: DB, agent_id: str) -> Agent:
    a = db.get(Agent, agent_id) or db.scalar(select(Agent).where(Agent.slug == agent_id))
    if a is None:
        raise HTTPException(404, detail="agent not found")
    return a


def own_session(db: DB, session_id: str, user: User) -> Session:
    s = db.get(Session, session_id)
    if s is None or (s.buyer_id != user.id and "admin" not in user.roles):
        raise HTTPException(404, detail="session not found")
    return s


def raise_session_error(e: SessionError):
    raise HTTPException(e.status, detail={"error": e.code, "detail": e.detail})


@router.post("/search", response_model=S.SearchResponse)
def search(body: S.SearchRequest, db: DB = Depends(get_db), user: User = Depends(current_user)):
    return {"task": body.task, "results": catalog.search(db, body.task, body.filters.model_dump(), body.limit)}


@router.get("/agents", response_model=list[S.AgentCard], summary="List live agents (convenience for the UI)")
def list_agents(db: DB = Depends(get_db)):
    return [catalog.agent_card(db, a) for a in catalog.live_agents(db)]


@router.get("/agents/{agent_id}", response_model=S.AgentDetail)
def get_agent(agent_id: str, db: DB = Depends(get_db)):
    return catalog.agent_detail(db, _agent(db, agent_id))


@router.post("/compare", response_model=S.CompareResponse)
def compare(body: S.CompareRequest, db: DB = Depends(get_db), user: User = Depends(current_user)):
    return catalog.compare(db, [_agent(db, i) for i in body.agent_ids[:3]])


@router.post("/quotes", response_model=S.QuoteOut)
def create_quote(body: S.QuoteRequest, db: DB = Depends(get_db), user: User = Depends(current_user)):
    agent = _agent(db, body.agent_id)
    if agent.status != "live":
        raise HTTPException(409, detail={"error": "agent_unavailable", "detail": f"agent is {agent.status}"})
    return catalog.quote_out(db, catalog.make_quote(db, user.id, agent, body.task))


@router.post("/sessions", response_model=S.SessionOut, status_code=201,
             responses={402: {"model": S.ErrorBody}, 409: {"model": S.ErrorBody}})
async def create_session(body: S.SessionCreate, db: DB = Depends(get_db), user: User = Depends(current_user),
                         idempotency_key: str | None = Header(None)):
    if idempotency_key:
        existing = db.scalar(select(Session).where(Session.quote_id == body.quote_id, Session.buyer_id == user.id))
        if existing:
            return sessions.session_out(db, existing)
    try:
        s = sessions.create_session(db, user.id, body.quote_id, body.cap_microcredits, body.files)
    except SessionError as e:
        raise_session_error(e)
    sessions.start(s.id)
    return sessions.session_out(db, s)


@router.get("/sessions", response_model=list[S.SessionOut], summary="Buyer's session history")
def list_sessions(db: DB = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Session).where(Session.buyer_id == user.id, Session.is_review.is_(False))
                      .order_by(Session.created_at.desc()).limit(50))
    return [sessions.session_out(db, s) for s in rows]


@router.get("/sessions/{session_id}", response_model=S.SessionOut)
def get_session(session_id: str, db: DB = Depends(get_db), user: User = Depends(current_user)):
    return sessions.session_out(db, own_session(db, session_id, user))


@router.get("/sessions/{session_id}/events", summary="SSE stream (see contracts/EVENTS.md)",
            response_description="text/event-stream; `?token=` accepted because EventSource cannot set headers")
async def session_events(session_id: str, request: Request, token: str | None = None,
                         last_event_id: str | None = Header(None), db: DB = Depends(get_db)):
    auth = request.headers.get("authorization", "")
    tok = token or auth.removeprefix("Bearer ").strip()
    user = db.scalar(select(User).where(User.api_token == tok)) if tok else None
    if user is None:
        raise HTTPException(401, detail="invalid or missing user token")
    s = own_session(db, session_id, user)
    after = int(last_event_id or 0)

    async def stream():
        q = events.subscribe(s.id)
        try:
            last = after
            for ev in events.history(db, s.id, after):
                last = ev["id"]
                yield _sse(ev)
                if ev["type"] == events.TERMINAL_EVENT:
                    return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev["id"] <= last:
                    continue
                last = ev["id"]
                yield _sse(ev)
                if ev["type"] == events.TERMINAL_EVENT:
                    return
        finally:
            events.unsubscribe(s.id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def _sse(ev: dict) -> str:
    return f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"


@router.get("/sessions/{session_id}/history", summary="All events so far as JSON (polling fallback to SSE)")
def session_history(session_id: str, after: int = 0, db: DB = Depends(get_db), user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    return events.history(db, s.id, after)


@router.post("/sessions/{session_id}/messages", response_model=S.SessionOut)
async def post_message(session_id: str, body: S.BuyerMessage, db: DB = Depends(get_db),
                       user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    try:
        await sessions.deliver_message(db, s, body.text, body.reply_to)
    except SessionError as e:
        raise_session_error(e)
    return sessions.session_out(db, s)


@router.post("/sessions/{session_id}/confirm", response_model=S.SessionOut)
async def confirm(session_id: str, body: S.ConfirmIn, db: DB = Depends(get_db), user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    try:
        await sessions.deliver_confirm(db, s, body.confirm_id, body.approved)
    except SessionError as e:
        raise_session_error(e)
    return sessions.session_out(db, s)


@router.post("/sessions/{session_id}/extend", response_model=S.SessionOut, responses={402: {"model": S.ErrorBody}})
def extend(session_id: str, body: S.ExtendIn, db: DB = Depends(get_db), user: User = Depends(current_user),
           idempotency_key: str | None = Header(None)):
    s = own_session(db, session_id, user)
    try:
        sessions.extend(db, s, body.add_microcredits, idempotency_key or secrets.token_hex(8))
    except SessionError as e:
        raise_session_error(e)
    return sessions.session_out(db, s)


@router.post("/sessions/{session_id}/cancel", response_model=S.SessionOut)
async def cancel(session_id: str, db: DB = Depends(get_db), user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    try:
        await sessions.cancel(db, s)
    except SessionError as e:
        raise_session_error(e)
    for _ in range(30):  # wait briefly for settlement so the UI gets the final state
        db.refresh(s)
        if s.settled_at:
            break
        await asyncio.sleep(0.1)
    return sessions.session_out(db, s)


@router.get("/sessions/{session_id}/files/{file_id}")
def download(session_id: str, file_id: str, token: str | None = None, request: Request = None,
             db: DB = Depends(get_db)):
    tok = token or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    user = db.scalar(select(User).where(User.api_token == tok)) if tok else None
    if user is None:
        raise HTTPException(401, detail="invalid or missing user token")
    s = own_session(db, session_id, user)
    f = db.get(SessionFile, file_id)
    if f is None or f.session_id != s.id:
        raise HTTPException(404, detail="file not found")
    return FileResponse(f.storage_ref, filename=f.name)


@router.post("/sessions/{session_id}/rating", status_code=201)
def rate(session_id: str, body: S.RatingIn, db: DB = Depends(get_db), user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    if not s.settled_at:
        raise HTTPException(409, detail={"error": "not_settled", "detail": "only settled sessions can be rated"})
    # MVP: the 24h rating delay is not enforced so the demo can rate immediately
    if db.scalar(select(Rating).where(Rating.session_id == s.id)):
        raise HTTPException(409, detail={"error": "already_rated", "detail": ""})
    r = Rating(session_id=s.id, buyer_id=user.id, stars=body.stars, comment=body.comment)
    db.add(r)
    v = db.get(AgentVersion, s.agent_version_id)
    from ..models import Listing
    listing = db.get(Listing, v.agent_id)
    st = dict(listing.live_stats_json or {})
    n = int(st.get("rating_n", 0))
    st["rating_avg"] = round((float(st.get("rating_avg") or 0) * n + body.stars) / (n + 1), 2)
    st["rating_n"] = n + 1
    listing.live_stats_json = st
    db.commit()
    return {"id": r.id, "stars": r.stars}


@router.post("/sessions/{session_id}/dispute", status_code=201)
def dispute(session_id: str, body: S.DisputeIn, db: DB = Depends(get_db), user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    d = Dispute(session_id=s.id, reason=body.reason)
    db.add(d)
    db.commit()
    return {"id": d.id, "status": d.status}


@router.get("/receipts/{session_id}", response_model=S.Receipt)
def get_receipt(session_id: str, db: DB = Depends(get_db), user: User = Depends(current_user)):
    s = own_session(db, session_id, user)
    if not s.settled_at:
        raise HTTPException(409, detail={"error": "not_settled", "detail": f"session is {s.status}"})
    return sessions.receipt(db, s)


@router.get("/wallet", response_model=S.WalletOut)
def wallet(db: DB = Depends(get_db), user: User = Depends(current_user)):
    acc = ledger.account(db, ledger.BUYER_WALLET, user.id)
    hist = [{"ts": e.ts, "direction": e.direction, "amount_microcredits": e.amount_microcredits, "memo": e.memo,
             "ref_type": e.ref_type, "ref_id": e.ref_id} for e in ledger.history(db, acc.id)]
    return {"user_id": user.id, **sessions.balance_summary(db, user.id), "history": hist}


@router.post("/wallet/topup", summary="Mock checkout: credits the pack immediately (Stripe test mode later)")
def topup(body: S.TopupIn, db: DB = Depends(get_db), user: User = Depends(current_user),
          idempotency_key: str | None = Header(None)):
    cents, credits = PACKS[body.pack]
    key = f"topup:{user.id}:{idempotency_key or secrets.token_hex(8)}"
    p = CreditPurchase(user_id=user.id, provider_ref="mock_" + secrets.token_hex(6), amount_usd_cents=cents,
                       credits=credits)
    db.add(p)
    db.commit()
    ledger.topup(db, user.id, credits * MICRO, key, p.id)
    return {"purchase_id": p.id, "credits": credits, **sessions.balance_summary(db, user.id)}


@router.post("/subscriptions", summary="Stub: records the plan; no credit grants in the MVP")
def subscribe(body: S.SubscriptionIn, db: DB = Depends(get_db), user: User = Depends(current_user)):
    sub = Subscription(user_id=user.id, plan=body.plan, status="active" if body.plan != "none" else "cancelled")
    db.add(sub)
    db.commit()
    return {"id": sub.id, "plan": sub.plan, "status": sub.status}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "roles": user.roles}
