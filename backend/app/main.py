import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import accounts, admin, buyer, creator, frontend, runtime as runtime_api
from .config import settings
from .db import SessionLocal, init_db
from .models import AgentVersion, Session
from .services import events, nocode, review, runtime
from .services.sessions import ACTIVE, finalize_safely

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    events.bind_loop(asyncio.get_running_loop())
    runtime.reap_orphans()
    # Sessions that were mid-flight when the server died: their sandboxes are gone, refund them.
    db = SessionLocal()
    try:
        orphans = [s.id for s in db.query(Session).filter(Session.status.in_(ACTIVE)).all()]
        try:
            nocode.discover(db)  # no-code folders under AGENTS_DIR (agent.yaml + prompt.md) become submitted versions
        except Exception:  # noqa: BLE001
            db.rollback()
            logging.exception("no-code discovery failed")
        # Versions submitted while no server was running (the seeded InvoiceGenius Pro) get their review now.
        pending = [v.id for v in db.query(AgentVersion).filter(AgentVersion.status == "submitted").all()]
    finally:
        db.close()
    for sid in orphans:
        finalize_safely(sid, "failed", "platform restarted during session")
    if settings.review_on_boot:
        # Each call only records the run as "queued" and returns; at most REVIEW_CONCURRENCY run at a time,
        # in the background, so the server takes requests right away.
        for vid in pending:
            review.start_review(vid)
    yield
    runtime.reap_orphans()


app = FastAPI(title="Agent Marketplace API", version="0.1.0", lifespan=lifespan,
              description="All money fields are integer microcredits (1 credit = 1000 microcredits). "
                          "Auth: `Authorization: Bearer <token>`; demo tokens tok_sam (buyer), tok_riya (creator), "
                          "tok_admin (admin). Session events: see contracts/EVENTS.md.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=False)


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    d = exc.detail
    body = d if isinstance(d, dict) else {"error": {401: "unauthorized", 403: "forbidden", 404: "not_found",
                                                    409: "conflict", 402: "insufficient_credits"}.get(exc.status_code,
                                                                                                      "error"),
                                          "detail": str(d)}
    return JSONResponse(body, status_code=exc.status_code)


for r in (accounts.router, buyer.router, creator.router, admin.router, runtime_api.router, frontend.router):
    app.include_router(r)


@app.get("/health")
def health():
    return {"ok": True, "llm_mode": settings.llm_mode, "runtime_mode": settings.runtime_mode}
