"""Runtime-facing endpoints: LLM proxy, tool broker (session virtual key) and internal service calls."""
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..db import get_db
from ..models import ModelPrice, Session
from ..services import proxy, sessions
from ..services.proxy import ProxyError

router = APIRouter(tags=["runtime"])
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "dev-internal")


def _err(e: ProxyError):
    return JSONResponse(e.body(), status_code=e.status)


@router.post("/proxy/v1/chat/completions", summary="OpenAI-compatible chat completions (virtual key)",
             responses={402: {"description": "error.code = budget_exhausted"}})
async def chat_completions(request: Request, authorization: str = Header(""), idempotency_key: str | None = Header(None),
                           db: DB = Depends(get_db)):
    body = await request.json()
    try:
        if body.get("stream"):
            gen = await proxy.chat_completion_stream(db, authorization, body, idempotency_key)
            return StreamingResponse(gen, media_type="text/event-stream")
        return await proxy.chat_completion(db, authorization, body, idempotency_key)
    except ProxyError as e:
        return _err(e)


@router.get("/proxy/v1/models")
def models(db: DB = Depends(get_db)):
    aliases = sorted({a for a in db.scalars(select(ModelPrice.alias))})
    return {"object": "list", "data": [{"id": a, "object": "model", "owned_by": "marketplace"} for a in aliases]}


@router.get("/proxy/v1/budget", summary="Remaining budget for ctx.budget_remaining()")
async def get_budget(authorization: str = Header(""), db: DB = Depends(get_db)):
    try:
        return proxy.budget(db, authorization)
    except ProxyError as e:
        return _err(e)


@router.post("/tools/v1/tools/{name}", summary="Tool broker (web_search is mocked; outputs are marked untrusted)")
async def tools(name: str, request: Request, authorization: str = Header(""),
                idempotency_key: str | None = Header(None), db: DB = Depends(get_db)):
    try:
        args = await request.json()
    except ValueError:
        args = {}
    try:
        return proxy.tool_call(db, authorization, name, args, idempotency_key)
    except ProxyError as e:
        return _err(e)


def _service(x_service_token: str = Header("")):
    if x_service_token != INTERNAL_TOKEN:
        raise HTTPException(401, detail="service credentials required")


@router.post("/internal/settle", dependencies=[Depends(_service)], summary="Settle a session (normally automatic)")
def internal_settle(body: dict, db: DB = Depends(get_db)):
    s = db.get(Session, body.get("session_id"))
    if s is None:
        raise HTTPException(404, detail="session not found")
    s = sessions.finalize(db, s.id, body.get("outcome", "completed"), body.get("reason"))
    return sessions.receipt(db, s)
