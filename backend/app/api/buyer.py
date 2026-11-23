import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .. import schemas as S
from ..auth import current_user, user_for_token
from ..config import MICRO
from ..db import get_db
from ..models import (Agent, AgentVersion, CreditPurchase, Dispute, Listing, Quote, Rating, Session, SessionFile,
                      Subscription, User)
from ..services import catalog, events, ledger, sessions
from ..services.sessions import SessionError

router = APIRouter(prefix="/v1", tags=["buyer"])

_IDEM_SESSIONS: dict[tuple[str, str], str] = {}  # (user, Idempotency-Key) -> session id for quote-less creates
PACKS = {"S": (1000, 1000), "M": (2500, 2750), "L": (5000, 5750)}  # usd cents, credits


def _agent(db: DB, agent_id: str) -> Agent:
    a = db.get(Agent, agent_id) or db.scalar(select(Agent).where(Agent.slug == agent_id))
    if a is None or a.current_version_id is None:
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
    q = catalog.quote_out(db, catalog.make_quote(db, user.id, agent, body.task))
    return q | {"low_credits": q["low_microcredits"] / MICRO, "high_credits": q["high_microcredits"] / MICRO,
                "maximum": q["max_microcredits"] / MICRO}


@router.post("/sessions", response_model=S.SessionOut, status_code=201,
             responses={402: {"model": S.ErrorBody}, 409: {"model": S.ErrorBody}})
async def create_session(body: S.SessionCreate, db: DB = Depends(get_db), user: User = Depends(current_user),
                         idempotency_key: str | None = Header(None)):
    quote_id, cap = body.quote_id, body.cap_microcredits
    if cap is None and body.cap_credits is not None:
        cap = int(round(body.cap_credits * MICRO))
    if quote_id is None:
        if not body.agent_id:
            raise HTTPException(422, detail={"error": "quote_required", "detail": "give quote_id, or agent_id + task"})
        agent = _agent(db, body.agent_id)
        if agent.status != "live":
            raise HTTPException(409, detail={"error": "agent_unavailable", "detail": f"agent is {agent.status}"})
        if idempotency_key and (user.id, idempotency_key) in _IDEM_SESSIONS:
            return sessions.session_out(db, db.get(Session, _IDEM_SESSIONS[(user.id, idempotency_key)])) | {
                "session_id": _IDEM_SESSIONS[(user.id, idempotency_key)]}
        q = catalog.make_quote(db, user.id, agent, body.task or "")
        quote_id = q.id
    elif idempotency_key:
        existing = db.scalar(select(Session).where(Session.quote_id == quote_id, Session.buyer_id == user.id))
        if existing:
            return sessions.session_out(db, existing)
    try:
        s = sessions.create_session(db, user.id, quote_id, cap, body.files)
    except SessionError as e:
        raise_session_error(e)
    if idempotency_key:
        _IDEM_SESSIONS[(user.id, idempotency_key)] = s.id
    sessions.start(s.id)
    return sessions.session_out(db, s) | {"session_id": s.id}


@router.get("/sessions", summary="Buyer's session history: {sessions: [SessionOut + title, active, cap_credits]}")
def list_sessions(db: DB = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Session).where(Session.buyer_id == user.id, Session.is_review.is_(False))
                      .order_by(Session.created_at.desc()).limit(50))
    out = []
    for s in rows:
        o = sessions.session_out(db, s)
        listing = db.get(Listing, o["agent_id"])
        out.append(o | {"title": listing.title if listing else o["agent_id"], "active": s.status in sessions.ACTIVE,
                        "cap_credits": s.cap / MICRO})
    return {"sessions": out}


@router.get("/sessions/{session_id}", response_model=S.SessionOut)
def get_session(session_id: str, db: DB = Depends(get_db), user: User = Depends(current_user)):
    return sessions.session_out(db, own_session(db, session_id, user))


@router.get("/sessions/{session_id}/events", summary="SSE stream (see contracts/EVENTS.md)",
            response_description="text/event-stream; `?token=` accepted because EventSource cannot set headers")
async def session_events(session_id: str, request: Request, token: str | None = None,
                         access_token: str | None = None, flat: bool | None = None,
                         last_event_id: str | None = Header(None), db: DB = Depends(get_db)):
    """Named frames by default (`event: <type>`, data = {id, type, role, ts, data}). `flat=true` (implied by
    `?access_token=`, which the bundled frontend uses) sends unnamed frames with the payload flattened and
    `*_credits` conveniences, so `EventSource.onmessage` sees every event."""
    auth = request.headers.get("authorization", "")
    tok = token or access_token or auth.removeprefix("Bearer ").strip()
    user = user_for_token(db, tok)
    if user is None:
        raise HTTPException(401, detail="invalid or missing user token")
    s = own_session(db, session_id, user)
    after = int(last_event_id or 0)
    flat = bool(access_token) if flat is None else flat
    fmt = _sse_flat if flat else _sse
    if s.settled_at and not events.history(db, s.id, after):
        return Response(status_code=204)  # finished and fully delivered: tells EventSource to stop reconnecting

    async def stream():
        q = events.subscribe(s.id)
        try:
            last = after
            for ev in events.history(db, s.id, after):
                last = ev["id"]
                yield fmt(ev)
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
                yield fmt(ev)
                if ev["type"] == events.TERMINAL_EVENT:
                    return
        finally:
            events.unsubscribe(s.id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def _sse(ev: dict) -> str:
    return f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"


def flatten_event(ev: dict) -> dict:
    d = dict(ev.get("data") or {})
    out = {"id": ev["id"], "type": ev["type"], "role": ev["role"], "ts": ev["ts"]} | d
    if ev["type"] == "status":
        out["text"] = d.get("status", "").replace("_", " ")
    for k in list(d):
        if k.endswith("_microcredits") and isinstance(d[k], int):
            out[k.removesuffix("_microcredits") + "_credits"] = d[k] / MICRO
    if ev["type"] == "error":
        out["text"] = d.get("message") or d.get("code")
    if ev["type"] in ("ask_user", "confirm"):
        out["text"] = d.get("question") or d.get("action")
    if ev["type"] == "file":
        out["text"] = f"File: {d.get('name')}"
    if ev["type"] == "settled":
        out["text"] = "Settled"
        out["refunded"] = d.get("price_microcredits") == 0
    return out


def _sse_flat(ev: dict) -> str:
    return f"id: {ev['id']}\ndata: {json.dumps(flatten_event(ev), default=str)}\n\n"


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
def download(session_id: str, file_id: str, token: str | None = None, access_token: str | None = None,
             request: Request = None, db: DB = Depends(get_db)):
    tok = token or access_token or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    user = user_for_token(db, tok)
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
    r = sessions.receipt(db, s)
    return r | {"price_credits": r["price_microcredits"] / MICRO, "cost_credits": r["cost_microcredits"] / MICRO,
                "fee_credits": r["fee_microcredits"] / MICRO, "cap_credits": r["cap_microcredits"] / MICRO,
                "earnings_credits": r["creator_earnings_microcredits"] / MICRO,
                "released_credits": r["released_microcredits"] / MICRO}


@router.get("/wallet", response_model=S.WalletOut)
def wallet(db: DB = Depends(get_db), user: User = Depends(current_user)):
    acc = ledger.account(db, ledger.BUYER_WALLET, user.id)
    hist = [{"ts": e.ts, "direction": e.direction, "amount_microcredits": e.amount_microcredits, "memo": e.memo,
             "ref_type": e.ref_type, "ref_id": e.ref_id} for e in ledger.history(db, acc.id)]
    bal = sessions.balance_summary(db, user.id)
    granted = ledger.granted_total(db, acc.id)
    return {"user_id": user.id, **bal, "history": hist, "user": user.name or user.email,
            "balance_credits": bal["balance_microcredits"] / MICRO, "granted_credits": granted / MICRO,
            "used_credits": max(0, granted - bal["balance_microcredits"] - bal["held_microcredits"]) / MICRO}


@router.post("/wallet/topup", summary="Mock checkout: credits the pack immediately (Stripe test mode later)")
def topup(body: S.TopupIn, db: DB = Depends(get_db), user: User = Depends(current_user),
          idempotency_key: str | None = Header(None)):
    if body.pack:
        cents, credits = PACKS[body.pack]
    elif body.credits:
        cents, credits = body.credits, body.credits  # mock checkout: $0.01 per credit
    else:
        raise HTTPException(422, detail={"error": "pack_or_credits_required", "detail": ""})
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
