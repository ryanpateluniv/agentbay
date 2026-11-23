"""Session event log + in-process pub/sub for SSE. Every buyer-visible event is persisted
(session_messages) so the SSE stream can replay from Last-Event-ID."""
import asyncio
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..db import SessionLocal
from ..models import SessionMessage

# Not forwarded to the buyer (creator-only debug logs)
PRIVATE_TYPES = {"log"}
TERMINAL_EVENT = "settled"

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop):
    global _loop
    _loop = loop


def to_wire(m: SessionMessage) -> dict:
    return {"id": m.id, "type": m.type, "role": m.role, "ts": m.ts.isoformat() + "Z", "data": m.content}


def publish(session_id: str, type_: str, data: dict, role: str = "system", db: DB | None = None) -> dict:
    own = db is None
    db = db or SessionLocal()
    try:
        m = SessionMessage(session_id=session_id, role=role, type=type_, content=data)
        db.add(m)
        db.commit()
        wire = to_wire(m)
    finally:
        if own:
            db.close()
    if type_ not in PRIVATE_TYPES:
        for q in list(_subscribers.get(session_id, ())):
            if _loop is not None and _loop.is_running():
                _loop.call_soon_threadsafe(q.put_nowait, wire)
            else:
                q.put_nowait(wire)
    return wire


def history(db: DB, session_id: str, after_id: int = 0, include_private: bool = False) -> list[dict]:
    stmt = select(SessionMessage).where(SessionMessage.session_id == session_id, SessionMessage.id > after_id)
    if not include_private:
        stmt = stmt.where(SessionMessage.type.not_in(PRIVATE_TYPES))
    return [to_wire(m) for m in db.scalars(stmt.order_by(SessionMessage.id))]


def subscribe(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[session_id].add(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue):
    _subscribers[session_id].discard(q)
