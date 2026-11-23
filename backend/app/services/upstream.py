"""Real-mode upstream (OpenAI): request translation, retries with backoff, error mapping, hard safety caps,
and a raw usage log written straight from the provider's responses (used to verify our metering).

Billing never happens here. The proxy bills only after a call succeeds, once per idempotency key, so a
failed or retried upstream call never charges the buyer."""
import asyncio
import json
import logging
import math
import random
import threading
import time
from contextlib import asynccontextmanager

import httpx

from ..config import settings, usd_to_micro

log = logging.getLogger("upstream")
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
USAGE_LOG = settings.data_dir / "upstream_usage.jsonl"
MIN_OUTPUT_TOKENS = 16


class UpstreamError(Exception):
    """Mapped provider failure. Never billed."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---------- hard safety caps (real mode only) ----------

class SpendGuard:
    """Reserve a call's worst-case provider cost before sending it, settle to the actual cost after.
    Caps total real spend per server run and per session, so a bug (or a loop) can't burn the account."""

    def __init__(self):
        self.lock = threading.Lock()
        self.run_spent = 0  # micro-USD, actual
        self.reserved = 0
        self.session_spent: dict[str, int] = {}

    def limits(self) -> tuple[int, int]:
        return usd_to_micro(settings.real_max_usd_per_run), usd_to_micro(settings.real_max_usd_per_session)

    def reserve(self, session_id: str | None, est_input_uusd: int, price_out: int, want_out: int) -> tuple[int, int]:
        """Returns (allowed_output_tokens, reservation_micro_usd). Raises UpstreamError if nothing fits."""
        run_cap, ses_cap = self.limits()
        with self.lock:
            run_room = run_cap - self.run_spent - self.reserved
            ses_room = ses_cap - self.session_spent.get(session_id, 0) if session_id else run_room
            room = min(run_room, ses_room)
            max_out = (room - est_input_uusd) * 1_000_000 // price_out if price_out else want_out
            allowed = min(want_out, max_out)
            if allowed < MIN_OUTPUT_TOKENS:
                which = "session" if ses_room < run_room else "run"
                raise UpstreamError(503, "safety_limit", f"real-mode safety cap per {which} reached "
                                                         f"(REAL_MAX_USD_PER_{which.upper()})")
            reservation = est_input_uusd + math.ceil(allowed * price_out / 1_000_000)
            self.reserved += reservation
            return allowed, reservation

    def settle(self, session_id: str | None, reservation: int, actual_uusd: int):
        with self.lock:
            self.reserved -= reservation
            self.run_spent += actual_uusd
            if session_id:
                self.session_spent[session_id] = self.session_spent.get(session_id, 0) + actual_uusd

    def snapshot(self) -> dict:
        run_cap, ses_cap = self.limits()
        return {"run_spent_micro_usd": self.run_spent, "run_cap_micro_usd": run_cap,
                "session_cap_micro_usd": ses_cap, "reserved_micro_usd": self.reserved}


guard = SpendGuard()


def provider_cost_uusd(usage: dict, price) -> int:
    """Exact provider cost of one call in micro-USD (ceil), from the provider's own usage block."""
    t_in, t_out, cached = usage_tokens(usage)
    if price.price_cached_in is None:
        cached = 0
    raw = (t_in - cached) * price.price_in + cached * (price.price_cached_in or 0) + t_out * price.price_out
    return math.ceil(raw / 1_000_000)


def usage_tokens(usage: dict) -> tuple[int, int, int]:
    """(input_tokens, output_tokens, cached_input_tokens) from an OpenAI usage block. Reasoning tokens are
    already included in completion_tokens and billed as output."""
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0), int(cached)


def estimate_input_uusd(messages: list, price) -> int:
    chars = sum(len(json.dumps(m.get("content", ""))) for m in messages) + 16 * len(messages)
    return math.ceil((chars // 3 + 1) * price.price_in / 1_000_000)  # conservative: ~3 chars per token


# ---------- request translation ----------

def build_payload(body: dict, price, max_out: int, stream: bool) -> dict:
    drop = set(price.drop_params or []) | {"max_tokens", "max_completion_tokens", "stream", "stream_options", "model"}
    payload = {k: v for k, v in body.items() if k not in drop}
    payload |= {"model": price.provider_model, "max_completion_tokens": max_out}
    if stream:
        payload |= {"stream": True, "stream_options": {"include_usage": True}}
    return payload


def _headers() -> dict:
    if not settings.llm_upstream_api_key:
        raise UpstreamError(503, "provider_not_configured", "LLM_MODE=real but OPENAI_API_KEY is not set")
    return {"authorization": f"Bearer {settings.llm_upstream_api_key}"}


def _url() -> str:
    return settings.llm_upstream_base_url.rstrip("/") + "/chat/completions"


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(10.0, float(retry_after))
        except ValueError:
            pass
    return min(8.0, 0.5 * 2 ** attempt) * (0.75 + random.random() / 2)


def _final_error(status: int | None, text: str, timed_out: bool) -> UpstreamError:
    if timed_out:
        return UpstreamError(504, "upstream_timeout", "provider timed out after retries")
    if status == 429:
        return UpstreamError(429, "upstream_rate_limited", "provider rate limit; retried with backoff")
    if status is not None and 400 <= status < 500:
        return UpstreamError(400, "invalid_request", f"provider rejected the request: {text[:300]}")
    return UpstreamError(502, "provider_error", f"provider error {status}: {text[:300]}")


def _error_message(r: httpx.Response) -> str:
    try:
        return (r.json().get("error") or {}).get("message", "") or r.text
    except ValueError:
        return r.text


def log_usage(session_id: str | None, data: dict, alias: str):
    """Append the provider's own usage block, untouched, for later verification."""
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(USAGE_LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), "session_id": session_id, "alias": alias, "id": data.get("id"),
                            "model": data.get("model"), "usage": data.get("usage")}) + "\n")


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.llm_timeout_s, connect=10)


async def complete(payload: dict) -> dict:
    """Non-streaming call with retries. Returns the provider JSON or raises UpstreamError."""
    status, text, timed_out = None, "", False
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        for attempt in range(settings.llm_max_retries + 1):
            timed_out = False
            try:
                r = await c.post(_url(), json=payload, headers=_headers())
            except httpx.TimeoutException:
                timed_out, status, ra = True, None, None
            except httpx.TransportError as e:
                status, text, ra = None, str(e), None
            else:
                if r.status_code < 400:
                    return r.json()
                status, text, ra = r.status_code, _error_message(r), r.headers.get("retry-after")
                if status not in RETRYABLE:
                    break
            if attempt < settings.llm_max_retries:
                log.warning("upstream attempt %d failed (%s); retrying", attempt + 1, status or "timeout/transport")
                await asyncio.sleep(_backoff(attempt, ra))
    raise _final_error(status, text, timed_out)


@asynccontextmanager
async def open_stream(payload: dict):
    """Open a streaming call, retrying only before any byte is received. Yields the httpx response."""
    status, text, timed_out = None, "", False
    async with httpx.AsyncClient(timeout=_timeout()) as c:
        for attempt in range(settings.llm_max_retries + 1):
            timed_out = False
            try:
                req = c.build_request("POST", _url(), json=payload, headers=_headers())
                r = await c.send(req, stream=True)
            except httpx.TimeoutException:
                timed_out, status, ra = True, None, None
            except httpx.TransportError as e:
                status, text, ra = None, str(e), None
            else:
                if r.status_code < 400:
                    try:
                        yield r
                    finally:
                        await r.aclose()
                    return
                await r.aread()
                status, text, ra = r.status_code, _error_message(r), r.headers.get("retry-after")
                await r.aclose()
                if status not in RETRYABLE:
                    break
            if attempt < settings.llm_max_retries:
                await asyncio.sleep(_backoff(attempt, ra))
    raise _final_error(status, text, timed_out)
