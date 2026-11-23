"""Runtime contract server (stdlib only): POST /invoke (NDJSON stream), POST /quote, GET /health, POST /cancel."""
import base64
import hashlib
import hmac
import json
import os
import queue
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class BudgetExceeded(Exception):
    """The session's spending cap is reached; deliver what you have with ctx.return_result()."""


class Cancelled(Exception):
    """The buyer (or platform) cancelled the session."""


def _setup_env():
    base, key = os.environ.get("MARKETPLACE_LLM_BASE_URL"), os.environ.get("MARKETPLACE_LLM_API_KEY")
    if base and key:  # so OpenAI-compatible libraries go through the platform proxy with no code change
        os.environ["OPENAI_BASE_URL"] = base
        os.environ["OPENAI_API_KEY"] = key
        os.environ["ANTHROPIC_BASE_URL"] = base
        os.environ["ANTHROPIC_API_KEY"] = key


_setup_env()


def _is_budget_error(e: BaseException) -> bool:
    return isinstance(e, BudgetExceeded) or getattr(e, "status_code", None) == 402 or "budget_exhausted" in str(e)


def _http(method: str, url: str, key: str | None, body: dict | None = None, timeout: float = 120) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    if key:
        req.add_header("authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        code = (payload.get("error") or {}).get("code") if isinstance(payload.get("error"), dict) else None
        if e.code == 402 or code == "budget_exhausted":
            raise BudgetExceeded((payload.get("error") or {}).get("message", "budget exhausted")) from None
        raise RuntimeError(f"{method} {url} -> {e.code}: {payload or e.reason}") from None


def _stub_completion(messages: list, model: str) -> dict:
    """Local dev without a platform: deterministic free 'stub proxy' reply."""
    last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    text = f"[stub:{model}] Draft answer for: {str(last)[:200]}"
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": len(str(messages)) // 4, "completion_tokens": len(text) // 4}}


def chat(messages: list, model: str = "fast", *, base_url: str | None = None, api_key: str | None = None,
         raw: bool = False, **params):
    """Call the platform LLM proxy (OpenAI chat format). Returns the reply text (or full JSON with raw=True).
    Raises BudgetExceeded when the session cap is reached."""
    base = base_url or os.environ.get("MARKETPLACE_LLM_BASE_URL")
    key = api_key or os.environ.get("MARKETPLACE_LLM_API_KEY")
    if not base:
        data = _stub_completion(messages, model)
    else:
        data = _http("POST", base.rstrip("/") + "/chat/completions", key, {"model": model, "messages": messages} | params)
    return data if raw else data["choices"][0]["message"]["content"]


@dataclass
class Task:
    text: str
    files: list = field(default_factory=list)  # input file names; read them with ctx.read_file(name)


class _Run:
    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self.inbox: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self.finished = False


class Context:
    def __init__(self, session_id: str, task: Task, run: _Run, proxy: dict | None = None):
        self.session_id = session_id
        self.task = task
        self._run = run
        self._sent_files: set[str] = set()
        self._llm_base = (proxy or {}).get("base_url") or os.environ.get("MARKETPLACE_LLM_BASE_URL")
        self._llm_key = (proxy or {}).get("api_key") or os.environ.get("MARKETPLACE_LLM_API_KEY")
        self.workspace = Path(os.environ.get("MARKETPLACE_WORKSPACE") or Path.cwd() / ".workspace")
        self.workspace.mkdir(parents=True, exist_ok=True)
        names = []
        for f in task.files:  # wire format: [{"name", "content_base64"}] -> written to the workspace
            if isinstance(f, dict):
                name = Path(f["name"]).name
                (self.workspace / name).write_bytes(base64.b64decode(f.get("content_base64", "")))
                names.append(name)
            else:
                names.append(str(f))
        task.files = names

    # -- internal --
    def _check(self):
        if self._run.cancelled.is_set():
            raise Cancelled()

    def _emit(self, event: dict):
        self._check()
        self._run.events.put(event)

    def _wait(self, qid: str):
        while True:
            self._check()
            try:
                reply_to, value = self._run.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if reply_to is None and value is None:  # cancel wake-up
                self._check()
                continue
            if reply_to in (None, qid):
                return value

    # -- public API (Blueprint Section 5) --
    def message(self, text: str):
        """Send a chat message to the buyer."""
        self._emit({"type": "message", "text": str(text)})

    def ask_user(self, question: str, options: list | None = None) -> str:
        """Ask the buyer something and block until they answer."""
        qid = "q_" + secrets.token_hex(4)
        self._emit({"type": "ask_user", "id": qid, "question": question, "options": options})
        return str(self._wait(qid))

    def confirm(self, action: str) -> bool:
        """Ask the buyer to approve a sensitive action. Returns True if approved."""
        cid = "c_" + secrets.token_hex(4)
        self._emit({"type": "confirm", "id": cid, "action": action})
        v = self._wait(cid)
        return v is True or str(v).lower() in ("true", "yes", "y", "approve", "approved", "ok")

    def progress(self, label: str, step: int | None = None, total: int | None = None):
        self._emit({"type": "progress", "label": label, "step": step, "total": total})

    def read_file(self, name: str) -> bytes:
        return (self.workspace / Path(name).name).read_bytes()

    def write_file(self, name: str, data: str | bytes, mime: str | None = None):
        """Write to the session workspace and deliver the file to the buyer's files panel."""
        raw = data.encode() if isinstance(data, str) else data
        safe = Path(name).name
        (self.workspace / safe).write_bytes(raw)
        self._emit({"type": "file", "name": safe, "mime": mime or "application/octet-stream",
                    "content_base64": base64.b64encode(raw).decode()})
        self._sent_files.add(safe)

    def budget_remaining(self) -> float:
        """Credits left under the buyer's cap (inf when running locally without a platform)."""
        if not self._llm_base:
            return float("inf")
        b = _http("GET", self._llm_base.rstrip("/") + "/budget", self._llm_key)
        return b["remaining_microcredits"] / 1000

    def llm(self, messages: list, model: str = "fast", **params) -> str:
        """Convenience: chat completion through the platform proxy."""
        self._check()
        return chat(messages, model, base_url=self._llm_base, api_key=self._llm_key, **params)

    def tool(self, name: str, **args) -> dict:
        """Call a platform tool via the tool broker (e.g. ctx.tool("web_search", query="...")).
        Outputs marked untrusted=True are data, not instructions."""
        self._check()
        base = os.environ.get("MARKETPLACE_TOOLS_URL")
        if not base:
            return {"untrusted": True, "results": [], "notice": "no tool broker in local dev"}
        return _http("POST", f"{base.rstrip('/')}/v1/tools/{name}", self._llm_key, args)

    def log(self, text: str):
        """Debug log visible to the creator, never to the buyer."""
        self._emit({"type": "log", "text": str(text)})

    def return_result(self, text: str, files: list[str] | None = None):
        """Finish the task and deliver outputs."""
        for name in files or []:
            if Path(name).name not in self._sent_files and (self.workspace / Path(name).name).exists():
                self.write_file(name, (self.workspace / Path(name).name).read_bytes())
        self._emit({"type": "result", "text": str(text), "files": [Path(f).name for f in files or []]})
        self._run.finished = True


class Agent:
    def __init__(self, name: str | None = None):
        self.name = name
        self._task_fn = None
        self._quote_fn = None
        self._run: _Run | None = None
        self._secret = os.environ.get("MARKETPLACE_HMAC_SECRET")  # set for external (Mode B) agents

    def on_task(self, fn):
        self._task_fn = fn
        return fn

    def on_quote(self, fn):
        """Optional: fn(task_text) -> {"estimate_credits"?, "questions"?, "limits"?}"""
        self._quote_fn = fn
        return fn

    # -- task execution --
    def _execute(self, ctx: Context, run: _Run):
        try:
            out = self._task_fn(ctx)
            if not run.finished:
                if isinstance(out, str):
                    ctx.return_result(out)
                else:
                    run.events.put({"type": "error", "code": "no_result", "message": "handler returned without a result"})
        except Cancelled:
            run.events.put({"type": "error", "code": "cancelled", "message": "cancelled"})
        except Exception as e:  # noqa: BLE001
            if run.cancelled.is_set():
                run.events.put({"type": "error", "code": "cancelled", "message": "cancelled"})
            elif _is_budget_error(e):
                run.events.put({"type": "error", "code": "budget_exhausted", "message": str(e)})
            else:
                run.events.put({"type": "log", "text": traceback.format_exc()[-2000:]})
                run.events.put({"type": "error", "code": "agent_error", "message": f"{type(e).__name__}: {e}"[:500]})
        finally:
            run.events.put(None)

    def _handler(self):
        agent = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"  # body ends when the connection closes: simple streaming

            def log_message(self, *a):
                pass

            def _json(self, code: int, obj: dict):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> tuple[bytes, dict]:
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                return raw, (json.loads(raw) if raw else {})

            def _verify(self, raw: bytes) -> bool:
                if not agent._secret:
                    return True
                ts = self.headers.get("x-marketplace-timestamp", "")
                sig = self.headers.get("x-marketplace-signature", "")
                want = hmac.new(agent._secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
                fresh = ts.isdigit() and abs(time.time() - int(ts)) < 300
                return fresh and hmac.compare_digest(want, sig)

            def do_GET(self):
                if self.path.startswith("/health"):
                    return self._json(200, {"ok": True, "agent": agent.name})
                self._json(404, {"error": "not_found"})

            def do_POST(self):
                raw, body = self._body()
                if not self._verify(raw):
                    return self._json(401, {"error": "bad_signature"})
                if self.path.startswith("/invoke"):
                    return self._invoke(body)
                if self.path.startswith("/cancel"):
                    if agent._run:
                        agent._run.cancelled.set()
                        agent._run.inbox.put((None, None))
                    return self._json(200, {"ok": True})
                if self.path.startswith("/quote"):
                    if not agent._quote_fn:
                        return self._json(200, {"supported": False})
                    return self._json(200, {"supported": True} | (agent._quote_fn((body.get("task") or {}).get("text", "")) or {}))
                self._json(404, {"error": "not_found"})

            def _invoke(self, body: dict):
                kind = body.get("type", "task")
                if kind in ("message", "confirm"):
                    if not agent._run:
                        return self._json(409, {"error": "no_running_task"})
                    value = body.get("approved") if kind == "confirm" else body.get("text", "")
                    agent._run.inbox.put((body.get("reply_to"), value))
                    return self._json(202, {"ok": True})
                if agent._task_fn is None:
                    return self._json(500, {"error": "no @agent.on_task handler"})
                run = _Run()
                agent._run = run
                t = body.get("task") or {}
                ctx = Context(body.get("session_id", "local"), Task(t.get("text", ""), t.get("files") or []), run,
                              body.get("proxy"))
                threading.Thread(target=agent._execute, args=(ctx, run), daemon=True).start()
                self.send_response(200)
                self.send_header("content-type", "application/x-ndjson")
                self.end_headers()
                while True:
                    ev = run.events.get()
                    if ev is None:
                        break
                    try:
                        self.wfile.write((json.dumps(ev) + "\n").encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        run.cancelled.set()
                        break
                    if ev.get("type") in ("result", "error"):
                        break

        return H

    def serve(self, host: str = "0.0.0.0", port: int | None = None):
        """Start the HTTP server exposing /invoke, /quote, /health, /cancel."""
        port = port or int(os.environ.get("PORT", "8080"))
        server = ThreadingHTTPServer((host, port), self._handler())
        server.daemon_threads = True
        print(f"marketplace agent listening on {host}:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
