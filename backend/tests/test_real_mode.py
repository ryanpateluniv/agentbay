"""Real-mode proxy against a local stub of the OpenAI API: retries, no billing on failure, exact metering
from the provider's usage block, parameter translation, streaming, and the hard safety caps."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import UsageEvent
from app.services import upstream
from tests.test_sessions_e2e import _direct_key

stub = FastAPI()
STATE = {"script": [], "calls": [], "hang": False}
USAGE = {"prompt_tokens": 1234, "completion_tokens": 321, "total_tokens": 1555,
         "prompt_tokens_details": {"cached_tokens": 200}, "completion_tokens_details": {"reasoning_tokens": 50}}


@stub.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    STATE["calls"].append(body)
    action = STATE["script"].pop(0) if STATE["script"] else "ok"
    if action == "hang":
        await asyncio.sleep(5)
    if isinstance(action, int):
        return JSONResponse({"error": {"message": f"stub {action}"}}, status_code=action,
                            headers={"retry-after": "0"} if action == 429 else {})
    if body.get("stream"):
        async def gen():
            for piece in ("Hel", "lo ", "world"):
                yield f"data: {json.dumps({'id': 'chatcmpl-stub', 'choices': [{'delta': {'content': piece}}]})}\n\n"
                if action == "drop":
                    raise RuntimeError("upstream died mid-stream")
            yield f"data: {json.dumps({'id': 'chatcmpl-stub', 'choices': [], 'usage': USAGE})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {"id": "chatcmpl-stub-1", "model": body["model"], "usage": USAGE,
            "choices": [{"message": {"role": "assistant", "content": "stub answer"}, "finish_reason": "stop"}]}


@pytest.fixture(scope="module")
def real_mode(live_server):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(stub, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(0.5)
    saved = (settings.llm_mode, settings.llm_upstream_base_url, settings.llm_upstream_api_key, settings.llm_timeout_s,
             settings.real_max_usd_per_session, settings.real_max_usd_per_run, upstream._backoff)
    settings.llm_mode, settings.llm_upstream_base_url = "real", f"http://127.0.0.1:{port}/v1"
    settings.llm_upstream_api_key, settings.llm_timeout_s = "stub-key", 1.0
    upstream._backoff = lambda attempt, ra: 0.01
    yield live_server
    (settings.llm_mode, settings.llm_upstream_base_url, settings.llm_upstream_api_key, settings.llm_timeout_s,
     settings.real_max_usd_per_session, settings.real_max_usd_per_run, upstream._backoff) = saved
    server.should_exit = True


def events(sid):
    db = SessionLocal()
    rows = list(db.scalars(select(UsageEvent).where(UsageEvent.session_id == sid)))
    db.close()
    return rows


def call(base, key, **body):
    return httpx.post(f"{base}/proxy/v1/chat/completions", headers={"authorization": f"Bearer {key}"}, timeout=30,
                      json={"model": "fast", "messages": [{"role": "user", "content": "hi"}]} | body)


def test_success_meters_exactly_the_provider_usage(real_mode):
    sid, key = _direct_key(10_000_000)
    STATE["calls"].clear()
    r = call(real_mode, key, temperature=0, max_tokens=500)
    assert r.status_code == 200, r.text
    sent = STATE["calls"][-1]
    assert sent["model"] == "gpt-6-luna" and "temperature" not in sent and "max_tokens" not in sent
    assert sent["max_completion_tokens"] == 500
    [ev] = events(sid)
    assert (ev.input_tokens, ev.output_tokens, ev.cached_tokens, ev.reasoning_tokens) == (1234, 321, 200, 50)
    assert ev.provider_request_id == "chatcmpl-stub-1" and not ev.estimated
    # fast: $0.10/M in, $0.01/M cached, $0.50/M out -> 1034*0.1 + 200*0.01 + 321*0.5 = 265.9 micro-USD
    assert ev.provider_cost_micro_usd == 266
    assert ev.cost_microcredits == 30  # 26.59 microcredits * 1.1 markup = 29.25 -> ceil 30
    assert r.json()["usage"]["prompt_tokens"] == 1234


def test_rate_limit_retried_then_billed_once(real_mode):
    sid, key = _direct_key(10_000_000)
    STATE["script"] = [429, 503]
    STATE["calls"].clear()
    r = call(real_mode, key)
    assert r.status_code == 200 and len(STATE["calls"]) == 3
    assert len(events(sid)) == 1


def test_persistent_failures_are_never_billed(real_mode):
    sid, key = _direct_key(10_000_000)
    STATE["script"] = [429] * 10
    r = call(real_mode, key)
    assert r.status_code == 429 and r.json()["error"]["code"] == "upstream_rate_limited"
    STATE["script"] = [500] * 10
    assert call(real_mode, key).json()["error"]["code"] == "provider_error"
    STATE["script"] = [400]
    STATE["calls"].clear()
    r = call(real_mode, key)
    assert r.status_code == 400 and len(STATE["calls"]) == 1  # client errors are not retried
    STATE["script"] = ["hang"] * 10
    r = call(real_mode, key)
    assert r.status_code == 504 and r.json()["error"]["code"] == "upstream_timeout"
    STATE["script"] = []
    assert events(sid) == []


def test_streaming_bills_provider_usage_and_not_on_upstream_failure(real_mode):
    sid, key = _direct_key(10_000_000)
    h = {"authorization": f"Bearer {key}"}
    with httpx.stream("POST", f"{real_mode}/proxy/v1/chat/completions", headers=h, timeout=30,
                      json={"model": "fast", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        text = "".join(json.loads(l[6:])["choices"][0]["delta"].get("content", "")
                       for l in r.iter_lines() if l.startswith("data: {") and "choices" in l and '"delta"' in l)
    assert text == "Hello world"
    [ev] = events(sid)
    assert (ev.input_tokens, ev.output_tokens, ev.cached_tokens) == (1234, 321, 200)
    sid2, key2 = _direct_key(10_000_000)
    STATE["script"] = ["drop"]
    with httpx.stream("POST", f"{real_mode}/proxy/v1/chat/completions", timeout=30,
                      headers={"authorization": f"Bearer {key2}"},
                      json={"model": "fast", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as r:
        body = "\n".join(r.iter_lines())
    assert "provider_error" in body
    assert events(sid2) == []  # provider failed mid-stream: buyer not charged


def test_safety_caps(real_mode):
    settings.real_max_usd_per_session = "0.000270"  # one stub call costs 266 micro-USD; leaves no room for a 2nd
    sid, key = _direct_key(10_000_000)
    assert call(real_mode, key, max_tokens=100).status_code == 200
    r = call(real_mode, key, max_tokens=100)
    assert r.status_code == 503 and r.json()["error"]["code"] == "safety_limit"
    settings.real_max_usd_per_session = "0.25"
    before = upstream.guard.run_spent
    settings.real_max_usd_per_run = f"{before / 1_000_000:.6f}"  # run cap already used up
    sid, key = _direct_key(10_000_000)
    r = call(real_mode, key)
    assert r.status_code == 503 and "per run" in r.json()["error"]["message"]
    assert events(sid) == []
    settings.real_max_usd_per_run = "2.00"


def test_guard_clamps_output_to_remaining_room():
    g = upstream.SpendGuard()
    settings_before = settings.real_max_usd_per_session
    settings.real_max_usd_per_session = "0.001000"  # 1000 micro-USD
    allowed, res = g.reserve("s1", 100, 500_000, 100_000)  # $0.50/M out -> 1800 tokens fit in 900 micro-USD
    assert allowed == 1800 and res == 1000
    g.settle("s1", res, 1000)
    with pytest.raises(upstream.UpstreamError):
        g.reserve("s1", 1, 500_000, 100)
    settings.real_max_usd_per_session = settings_before
