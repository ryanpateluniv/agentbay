"""Session lifecycle (Sections 5, 7, 10): quote -> hold -> start sandbox -> route events -> settle -> receipt."""
import asyncio
import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from ..config import MICRO, settings
from ..db import SessionLocal
from ..models import (Agent, AgentVersion, Listing, LedgerEntry, Quote, Session, SessionFile, SessionToken,
                      UsageEvent, now)
from . import events, ledger, runtime
from .confidence import badges_for, compute_confidence
from .catalog import latest_review
from .pricing import compute_price, cost_limit

log = logging.getLogger("sessions")

ACTIVE = ("created", "holding", "starting", "running", "waiting_for_user", "completing")
TERMINAL_OUTCOMES = {"completed": "settled", "failed": "failed", "timed_out": "timed_out",
                     "cancelled": "cancelled", "budget_exhausted": "budget_exhausted"}
BUDGET_GRACE_SECONDS = 20


class SessionError(Exception):
    def __init__(self, status: int, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.status, self.code, self.detail = status, code, detail


@dataclass
class Live:
    """In-memory state of a running session (owned by its runner task)."""
    session_id: str
    sandbox: runtime.Sandbox | None = None
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # serialises proxy cap check + record
    pending: dict = field(default_factory=dict)  # question/confirm id -> event
    cancelled: bool = False
    kill_refund: bool = False
    budget_exhausted: bool = False
    warned_80: bool = False
    has_result: bool = False
    error: dict | None = None
    active_since: float | None = None
    auto_answer: Callable[[dict], str | bool] | None = None
    started_event: asyncio.Event = field(default_factory=asyncio.Event)


LIVE: dict[str, Live] = {}


def get_live(session_id: str) -> Live:
    if session_id not in LIVE:
        LIVE[session_id] = Live(session_id)
    return LIVE[session_id]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------- reads ----------

def spend(db: DB, s: Session) -> dict:
    row = db.execute(select(func.coalesce(func.sum(UsageEvent.cost_microcredits), 0),
                            func.count().filter(UsageEvent.type == "llm_call"),
                            func.count().filter(UsageEvent.type == "tool_call"),
                            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                            func.coalesce(func.sum(UsageEvent.output_tokens), 0))
                     .where(UsageEvent.session_id == s.id, UsageEvent.status == "ok")).one()
    cost = int(row[0])
    v = db.get(AgentVersion, s.agent_version_id)
    projected = s.price if s.settled_at else compute_price(
        v.manifest_json.get("pricing", {}), "completed", cost=cost, cap=s.cap, agent_messages=s.agent_message_count,
        active_ms=_active_ms(s), has_result=True, work_started=True)
    return {"spent_microcredits": cost, "projected_charge_microcredits": projected, "cap_microcredits": s.cap,
            "llm_calls": row[1], "tool_calls": row[2], "input_tokens": int(row[3]), "output_tokens": int(row[4])}


def _active_ms(s: Session) -> int:
    live = LIVE.get(s.id)
    extra = int((time.monotonic() - live.active_since) * 1000) if live and live.active_since else 0
    return s.active_ms + extra


def session_out(db: DB, s: Session) -> dict:
    v = db.get(AgentVersion, s.agent_version_id)
    files = db.scalars(select(SessionFile).where(SessionFile.session_id == s.id))
    live = LIVE.get(s.id)
    pending = next(iter(live.pending.values()), None) if live and live.pending else None
    return {"id": s.id, "status": s.status, "agent_id": v.agent_id, "agent_version_id": v.id, "buyer_id": s.buyer_id,
            "task": s.task_text, "cap_microcredits": s.cap, "spend": spend(db, s), "created_at": s.created_at,
            "started_at": s.started_at, "ended_at": s.ended_at, "settled_at": s.settled_at,
            "result_text": s.result_text, "pending_question": pending,
            "files": [file_out(f) for f in files]}


def file_out(f: SessionFile) -> dict:
    return {"id": f.id, "name": f.name, "direction": f.direction, "size_bytes": f.size_bytes,
            "url": f"/v1/sessions/{f.session_id}/files/{f.id}"}


def receipt(db: DB, s: Session) -> dict:
    v = db.get(AgentVersion, s.agent_version_id)
    title = db.get(Listing, v.agent_id).title if db.get(Listing, v.agent_id) else v.agent_id
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.ref_id == s.id, LedgerEntry.direction == "credit")
                         .order_by(LedgerEntry.id))
    from ..models import LedgerAccount
    ledger_rows = [{"memo": e.memo, "to": db.get(LedgerAccount, e.account_id).type,
                    "amount_microcredits": e.amount_microcredits, "txn_id": e.txn_id} for e in entries]
    released = sum(r["amount_microcredits"] for r in ledger_rows if r["memo"] == "release unused hold")
    sp = spend(db, s)
    return {"session_id": s.id, "agent_title": title, "status": s.status, "pricing_model": v.pricing_model,
            "cap_microcredits": s.cap, "price_microcredits": s.price, "cost_microcredits": s.cost,
            "fee_microcredits": s.fee, "creator_earnings_microcredits": s.creator_earnings,
            "released_microcredits": released, "refund_reason": s.refund_reason,
            "usage": {k: sp[k] for k in ("llm_calls", "tool_calls", "input_tokens", "output_tokens")}
            | {"agent_messages": s.agent_message_count, "active_seconds": _active_ms(s) // 1000},
            "lines": [{"label": "Agent price (P)", "amount_microcredits": s.price},
                      {"label": "of which model and tool cost (C)", "amount_microcredits": min(s.cost, s.price)},
                      {"label": "of which platform fee (F)", "amount_microcredits": s.fee},
                      {"label": "of which creator earnings (E)", "amount_microcredits": s.creator_earnings},
                      {"label": "Unused hold released", "amount_microcredits": released}],
            "ledger": ledger_rows}


# ---------- create ----------

def create_session(db: DB, buyer_id: str, quote_id: str, cap: int | None, files: list[dict] | None = None,
                   is_review: bool = False, task_override: str | None = None) -> Session:
    q = db.get(Quote, quote_id)
    if q is None or q.buyer_id != buyer_id:
        raise SessionError(404, "not_found", "quote not found")
    if q.expires_at < now() and not is_review:
        raise SessionError(409, "quote_expired", "quote expired, request a new one")
    v = db.get(AgentVersion, q.agent_version_id)
    agent = db.get(Agent, v.agent_id)
    if agent.status != "live" and not is_review:
        raise SessionError(409, "agent_unavailable", f"agent is {agent.status}")
    cap = q.high if cap is None else min(cap, q.max)  # never charged above the quoted maximum
    pricing = v.manifest_json.get("pricing", {})
    if pricing.get("model") in ("per_task", "outcome_based") and cap < q.low:
        raise SessionError(409, "cap_below_price", "cap must cover the flat price")
    if cap <= 0:
        raise SessionError(409, "invalid_cap", "cap must be positive")
    s = Session(buyer_id=buyer_id, agent_version_id=v.id, quote_id=q.id, cap=cap, status="holding",
                task_text=task_override or q.task_text, is_review=is_review)
    db.add(s)
    db.commit()
    if not is_review:
        try:
            ledger.hold(db, s)
        except ledger.InsufficientCredits:
            s.status = "failed"
            s.refund_reason = "insufficient credits"
            db.commit()
            raise SessionError(402, "insufficient_credits", "balance too low for this cap; top up or lower the cap")
    for f in files or []:
        store_file(db, s.id, f["name"], base64.b64decode(f.get("content_base64", "")), "in")
    events.publish(s.id, "status", {"status": "holding", "cap_microcredits": cap}, db=db)
    return s


def mint_key(db: DB, s: Session, v: AgentVersion) -> str:
    key = "vk_" + secrets.token_urlsafe(24)
    m = v.manifest_json
    limit = cost_limit(m.get("pricing", {}), s.cap, v.limits_json or m.get("limits", {}))
    minutes = min(int((v.limits_json or {}).get("max_session_minutes", 20)), settings.max_session_minutes_platform)
    db.add(SessionToken(session_id=s.id, token_hash=hash_key(key), cap=limit, models_allowed=m.get("models_allowed", []),
                        expires_at=now() + timedelta(minutes=minutes + 5)))
    db.commit()
    return key


def store_file(db: DB, session_id: str, name: str, data: bytes, direction: str) -> SessionFile:
    safe = name.replace("/", "_").replace("..", "_")[:200] or "file"
    d = settings.data_dir / "files" / session_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{direction}_{safe}"
    path.write_bytes(data)
    f = SessionFile(session_id=session_id, name=safe, storage_ref=str(path), direction=direction, size_bytes=len(data),
                    expires_at=now() + timedelta(days=30))
    db.add(f)
    db.commit()
    return f


# ---------- run ----------

def start(session_id: str, auto_answer=None) -> Live:
    live = get_live(session_id)
    live.auto_answer = auto_answer
    live.task = asyncio.get_running_loop().create_task(run_session(session_id))
    return live


def _set_status(db: DB, s: Session, status: str, **extra):
    live = LIVE.get(s.id)
    if live:  # per-minute meter: only running time counts
        if status == "running" and live.active_since is None:
            live.active_since = time.monotonic()
        elif status != "running" and live.active_since is not None:
            s.active_ms += int((time.monotonic() - live.active_since) * 1000)
            live.active_since = None
    s.status = status
    db.commit()
    events.publish(s.id, "status", {"status": status} | extra, db=db)


async def run_session(session_id: str):
    db = SessionLocal()
    live = get_live(session_id)
    outcome, reason = "failed", None
    try:
        s = db.get(Session, session_id)
        v = db.get(AgentVersion, s.agent_version_id)
        agent = db.get(Agent, v.agent_id)
        key = mint_key(db, s, v)
        base = settings.public_base_url.rstrip("/")
        env = {"MARKETPLACE_SESSION_ID": s.id, "MARKETPLACE_LLM_BASE_URL": f"{base}/proxy/v1",
               "MARKETPLACE_LLM_API_KEY": key, "MARKETPLACE_TOOLS_URL": f"{base}/tools"}
        _set_status(db, s, "starting")
        if live.cancelled:
            outcome = "cancelled"
            return
        try:
            live.sandbox = await runtime.start(s.id, agent, v, env)
        except Exception as e:  # noqa: BLE001
            reason = f"agent failed to start: {e}"
            events.publish(s.id, "error", {"code": "start_failed", "message": str(e)[:300]}, db=db)
            return
        s.started_at = now()
        _set_status(db, s, "running")
        live.started_event.set()
        inputs = db.scalars(select(SessionFile).where(SessionFile.session_id == s.id, SessionFile.direction == "in"))
        body = {"type": "task", "session_id": s.id, "task": {
            "text": s.task_text, "files": [{"name": f.name, "content_base64": base64.b64encode(
                open(f.storage_ref, "rb").read()).decode()} for f in inputs]}}
        if agent.mode == "external":  # Mode B: hand over a proxy key so it can route model calls through us
            body["proxy"] = {"base_url": env["MARKETPLACE_LLM_BASE_URL"], "api_key": key}
        minutes = min(int((v.limits_json or {}).get("max_session_minutes", 20)), settings.max_session_minutes_platform)
        try:
            async with asyncio.timeout(minutes * 60):
                async for ev in live.sandbox.invoke_stream(body):
                    await _handle_agent_event(db, s, live, ev)
                    if ev.get("type") in ("result", "error"):
                        break
        except TimeoutError:
            outcome, reason = "timed_out", "session time limit reached"
            return
        except Exception as e:  # noqa: BLE001  (connection dropped, agent crashed)
            if not live.cancelled:
                reason = f"agent stream failed: {e}"
                events.publish(s.id, "error", {"code": "agent_crashed", "message": str(e)[:300]}, db=db)
        db.refresh(s)
        if live.cancelled:
            outcome = "cancelled"
        elif live.budget_exhausted:
            outcome = "budget_exhausted"
        elif live.has_result:
            outcome = "completed"
        elif live.error and live.error.get("code") == "budget_exhausted":
            outcome = "budget_exhausted"
        else:
            reason = reason or (live.error or {}).get("message") or "agent ended without a result"
    except asyncio.CancelledError:
        pass  # cancel / kill / budget hard stop; outcome decided below
    finally:
        if outcome == "failed" and live.cancelled:
            outcome, reason = "cancelled", None
        elif outcome == "failed" and live.budget_exhausted:
            outcome, reason = "budget_exhausted", None
        if live.sandbox:
            await live.sandbox.stop()
        try:
            finalize(db, session_id, outcome, reason)
        except Exception:  # noqa: BLE001
            log.exception("finalize failed for %s", session_id)
        db.close()
        LIVE.pop(session_id, None)


async def _handle_agent_event(db: DB, s: Session, live: Live, ev: dict):
    t = ev.get("type")
    if not s.work_started:
        s.work_started = True
        db.commit()
    if t == "message":
        s.agent_message_count += 1
        db.commit()
        events.publish(s.id, "message", {"text": str(ev.get("text", ""))}, role="agent", db=db)
    elif t in ("ask_user", "confirm"):
        qid = ev.get("id") or secrets.token_hex(4)
        data = {"id": qid, "question": ev.get("question"), "options": ev.get("options"), "action": ev.get("action")}
        live.pending[qid] = {"type": t} | data
        _set_status(db, s, "waiting_for_user")
        events.publish(s.id, t, data, role="agent", db=db)
        if live.auto_answer:  # review pipeline answers automatically
            answer = live.auto_answer(live.pending[qid])
            if t == "confirm":
                await deliver_confirm(db, s, qid, bool(answer))
            else:
                await deliver_message(db, s, str(answer), qid)
    elif t == "progress":
        events.publish(s.id, "progress", {k: ev.get(k) for k in ("label", "step", "total")}, role="agent", db=db)
    elif t == "file":
        f = store_file(db, s.id, str(ev.get("name", "file")), base64.b64decode(ev.get("content_base64", "")), "out")
        events.publish(s.id, "file", file_out(f), role="agent", db=db)
    elif t == "log":
        events.publish(s.id, "log", {"text": str(ev.get("text", ""))[:2000]}, role="agent", db=db)
    elif t == "result":
        live.has_result = True
        s.result_text = str(ev.get("text", ""))
        db.commit()
        _set_status(db, s, "completing")
        files = db.scalars(select(SessionFile).where(SessionFile.session_id == s.id, SessionFile.direction == "out"))
        events.publish(s.id, "result", {"text": s.result_text, "files": [file_out(f) for f in files]},
                       role="agent", db=db)
    elif t == "error":
        live.error = {"code": ev.get("code", "agent_error"), "message": str(ev.get("message", ""))[:500]}
        events.publish(s.id, "error", live.error, role="agent", db=db)
    else:
        events.publish(s.id, "log", {"text": f"unknown event type {t!r}"}, role="system", db=db)


# ---------- buyer actions ----------

async def deliver_message(db: DB, s: Session, text: str, reply_to: str | None):
    live = LIVE.get(s.id)
    if s.status not in ("running", "waiting_for_user") or not live or not live.sandbox:
        raise SessionError(409, "not_running", f"session is {s.status}")
    if reply_to is None and live.pending:
        reply_to = next(iter(live.pending))
    live.pending.pop(reply_to, None)
    events.publish(s.id, "buyer_message", {"text": text, "reply_to": reply_to}, role="buyer", db=db)
    if s.status == "waiting_for_user" and not live.pending:
        _set_status(db, s, "running")
    await live.sandbox.send({"type": "message", "session_id": s.id, "text": text, "reply_to": reply_to})


async def deliver_confirm(db: DB, s: Session, confirm_id: str, approved: bool):
    live = LIVE.get(s.id)
    if not live or not live.sandbox or confirm_id not in live.pending:
        raise SessionError(409, "no_pending_confirm", "nothing to confirm")
    live.pending.pop(confirm_id, None)
    events.publish(s.id, "buyer_confirm", {"confirm_id": confirm_id, "approved": approved}, role="buyer", db=db)
    if not live.pending:
        _set_status(db, s, "running")
    await live.sandbox.send({"type": "confirm", "session_id": s.id, "reply_to": confirm_id, "approved": approved})


async def cancel(db: DB, s: Session, kill_refund: bool = False):
    if s.status not in ACTIVE:
        raise SessionError(409, "not_active", f"session is {s.status}")
    live = LIVE.get(s.id)
    if live is None or live.task is None or live.task.done():
        finalize(db, s.id, "cancelled", "cancelled", force_refund=kill_refund)  # orphaned (e.g. server restart)
        return
    live.cancelled = True
    live.kill_refund = live.kill_refund or kill_refund
    if live.sandbox:
        await live.sandbox.cancel()
        await asyncio.sleep(0.3)
        if live.task and not live.task.done():
            live.task.cancel()  # stream ends -> finally: stop sandbox + finalize


def extend(db: DB, s: Session, amount: int, idem_key: str):
    if s.status not in ACTIVE:
        raise SessionError(409, "not_active", f"session is {s.status}")
    try:
        ledger.extend(db, s, amount, idem_key)
    except ledger.InsufficientCredits:
        raise SessionError(402, "insufficient_credits", "balance too low to extend")
    db.refresh(s)
    v = db.get(AgentVersion, s.agent_version_id)
    tok = db.scalar(select(SessionToken).where(SessionToken.session_id == s.id))
    if tok:
        tok.cap = cost_limit(v.manifest_json.get("pricing", {}), s.cap, v.limits_json or {})
        db.commit()
    live = LIVE.get(s.id)
    if live:
        live.budget_exhausted = False
        live.warned_80 = False
    events.publish(s.id, "spend", spend(db, s), db=db)


# ---------- budget (called by the proxy / tool broker after each usage event) ----------

def on_usage(db: DB, s: Session, limit: int):
    sp = spend(db, s)
    events.publish(s.id, "spend", sp, db=db)
    live = LIVE.get(s.id)
    if not live:
        return
    if not live.warned_80 and sp["spent_microcredits"] * 100 >= limit * 80:
        live.warned_80 = True
        events.publish(s.id, "budget_warning", {"percent": 80} | sp, db=db)
    if sp["spent_microcredits"] >= limit and not live.budget_exhausted:
        live.budget_exhausted = True
        events.publish(s.id, "budget_exhausted", sp, db=db)
        asyncio.get_running_loop().call_later(BUDGET_GRACE_SECONDS, _grace_expired, s.id)


def _grace_expired(session_id: str):
    live = LIVE.get(session_id)
    if live and live.budget_exhausted and not live.has_result and live.task and not live.task.done():
        live.task.cancel()  # hard stop: agent had its chance to deliver what it has


# ---------- settle ----------

def finalize(db: DB, session_id: str, outcome: str, reason: str | None = None, force_refund: bool = False):
    s = db.get(Session, session_id)
    db.refresh(s)
    if s.settled_at:
        return s
    live = LIVE.get(session_id)
    if live and live.kill_refund:
        force_refund = True
    v = db.get(AgentVersion, s.agent_version_id)
    agent = db.get(Agent, v.agent_id)
    _set_status(db, s, s.status)  # flush active time
    cost = int(db.scalar(select(func.coalesce(func.sum(UsageEvent.cost_microcredits), 0))
                         .where(UsageEvent.session_id == s.id, UsageEvent.status == "ok")))
    price = 0 if force_refund else compute_price(
        v.manifest_json.get("pricing", {}), outcome, cost=cost, cap=s.cap, agent_messages=s.agent_message_count,
        active_ms=s.active_ms, has_result=bool(live and live.has_result) or s.result_text is not None,
        work_started=s.work_started)
    s.ended_at = s.ended_at or now()
    if s.is_review:
        s.price, s.cost, s.settled_at = price, cost, now()
        db.commit()
    else:
        ledger.settle(db, s, agent.creator_id, price, cost)
    if reason or force_refund:
        s.refund_reason = "killed by admin: refunded" if force_refund else reason
    tok = db.scalar(select(SessionToken).where(SessionToken.session_id == s.id))
    if tok:
        tok.revoked = True
    db.commit()
    _set_status(db, s, TERMINAL_OUTCOMES[outcome], outcome=outcome)
    if not s.is_review:
        _update_live_stats(db, agent, outcome, s)
    events.publish(s.id, "settled", receipt(db, s), db=db)
    return s


def _update_live_stats(db: DB, agent: Agent, outcome: str, s: Session):
    listing = db.get(Listing, agent.id)
    if not listing:
        return
    st = dict(listing.live_stats_json or {})
    n = int(st.get("n", 0))
    ok = 1.0 if outcome == "completed" else 0.0
    reliable = 0.0 if outcome in ("failed", "timed_out") else 1.0
    st["q_live"] = (st.get("q_live", ok) * n + ok) / (n + 1)
    st["r_live"] = (st.get("r_live", reliable) * n + reliable) / (n + 1)
    st["n"] = n + 1
    listing.live_stats_json = st
    review = latest_review(db, agent.current_version_id) if agent.current_version_id else None
    if review:
        listing.confidence_score, listing.confidence_n = compute_confidence(review.scores_json, st)
        listing.badges = badges_for(listing.confidence_score, listing.confidence_n, agent.mode)
    db.commit()


def kill_agent(db: DB, agent: Agent) -> list[str]:
    agent.status = "paused"
    db.commit()
    version_ids = [v.id for v in db.scalars(select(AgentVersion).where(AgentVersion.agent_id == agent.id))]
    running = list(db.scalars(select(Session).where(Session.agent_version_id.in_(version_ids),
                                                    Session.status.in_(ACTIVE))))
    return [r.id for r in running]


def admin_refund(db: DB, s: Session, reason: str) -> int:
    if not s.settled_at:
        raise SessionError(409, "not_settled", "session not settled yet")
    v = db.get(AgentVersion, s.agent_version_id)
    amount = ledger.refund(db, s, db.get(Agent, v.agent_id).creator_id, reason)
    events.publish(s.id, "refunded", {"amount_microcredits": amount, "reason": reason}, db=db)
    return amount


def balance_summary(db: DB, user_id: str) -> dict:
    held = 0
    for s in db.scalars(select(Session).where(Session.buyer_id == user_id, Session.status.in_(ACTIVE))):
        held += ledger.escrow_balance(db, s.id)
    return {"balance_microcredits": ledger.wallet_balance(db, user_id), "held_microcredits": held}
