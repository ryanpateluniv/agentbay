"""Agent sandboxes: one subprocess or Docker container per session, or an external (Mode B) endpoint.
Speaks the runtime contract: POST /invoke (NDJSON event stream), POST /cancel, GET /health."""
import asyncio
import hashlib
import hmac
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from ..config import settings
from . import nocode

PID_DIR = settings.data_dir / "pids"
LABEL = "mkt.session"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sign(secret: str, body: bytes, ts: str) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


class Sandbox:
    def __init__(self, base_url: str, hmac_secret: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.hmac_secret = hmac_secret
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=None))

    def _headers(self, body: bytes) -> dict:
        h = {"content-type": "application/json"}
        if self.hmac_secret:
            ts = str(int(time.time()))
            h |= {"x-marketplace-timestamp": ts, "x-marketplace-signature": sign(self.hmac_secret, body, ts)}
        return h

    async def wait_healthy(self, timeout: float = 20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exited():
                raise RuntimeError("agent process exited during startup")
            try:
                r = await self.client.get(self.base_url + "/health", timeout=2)
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
        raise TimeoutError("agent health check did not pass")

    def exited(self) -> bool:
        return False

    async def invoke_stream(self, body: dict):
        raw = json.dumps(body).encode()
        async with self.client.stream("POST", self.base_url + "/invoke", content=raw, headers=self._headers(raw)) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"/invoke returned {r.status_code}")
            async for line in r.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):  # tolerate SSE framing too
                    line = line[5:].strip()
                if not line or line.startswith(":"):
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"type": "error", "code": "malformed_event", "message": line[:200]}

    async def send(self, body: dict) -> int:
        raw = json.dumps(body).encode()
        r = await self.client.post(self.base_url + "/invoke", content=raw, headers=self._headers(raw))
        return r.status_code

    async def cancel(self):
        raw = b"{}"
        try:
            await self.client.post(self.base_url + "/cancel", content=raw, headers=self._headers(raw), timeout=3)
        except httpx.HTTPError:
            pass

    async def stop(self):
        await self.client.aclose()


class SubprocessSandbox(Sandbox):
    def __init__(self, session_id: str, source_path: str, entrypoint: str, env: dict):
        port = _free_port()
        super().__init__(f"http://127.0.0.1:{port}")
        self.session_id = session_id
        self.workspace = Path(tempfile.mkdtemp(prefix=f"ws_{session_id}_"))
        cmd = shlex.split(entrypoint)
        if cmd and cmd[0] in ("python", "python3"):
            cmd[0] = sys.executable
        full_env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.workspace), "LANG": "C.UTF-8"} | env | {
            "PORT": str(port), "MARKETPLACE_WORKSPACE": str(self.workspace),
            "PYTHONPATH": os.pathsep.join([str(settings.sdk_dir), source_path]), "PYTHONUNBUFFERED": "1"}
        log_path = settings.data_dir / "logs" / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(log_path, "ab")
        self.proc = subprocess.Popen(cmd, cwd=source_path, env=full_env, stdout=self.log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        PID_DIR.mkdir(parents=True, exist_ok=True)
        (PID_DIR / f"{session_id}.pid").write_text(str(self.proc.pid))

    def exited(self) -> bool:
        return self.proc.poll() is not None

    async def stop(self):
        await super().stop()
        _kill_group(self.proc.pid)
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        self.log.close()
        (PID_DIR / f"{self.session_id}.pid").unlink(missing_ok=True)
        shutil.rmtree(self.workspace, ignore_errors=True)  # workspace wiped after the session


class DockerSandbox(Sandbox):
    def __init__(self, session_id: str, image: str, port: int, env: dict):
        import docker
        self.dc = docker.from_env()
        self.container = self.dc.containers.run(
            image, detach=True, environment=env | {"PORT": str(port), "MARKETPLACE_WORKSPACE": "/workspace"},
            ports={f"{port}/tcp": ("127.0.0.1", None)}, labels={LABEL: session_id}, mem_limit="1g",
            nano_cpus=1_000_000_000, pids_limit=256, read_only=True, tmpfs={"/workspace": "size=500m", "/tmp": ""},
            user="65534:65534", extra_hosts={"host.docker.internal": "host-gateway"})
        self.container.reload()
        host_port = self.container.ports[f"{port}/tcp"][0]["HostPort"]
        super().__init__(f"http://127.0.0.1:{host_port}")

    def exited(self) -> bool:
        self.container.reload()
        return self.container.status == "exited"

    async def stop(self):
        await super().stop()
        try:
            self.container.remove(force=True)
        except Exception:
            pass


def _kill_group(pid: int):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.2)


def reap_orphans():
    """Kill sandboxes left behind by a previous server process (crash/restart)."""
    if PID_DIR.exists():
        for f in PID_DIR.glob("*.pid"):
            try:
                _kill_group(int(f.read_text()))
            except ValueError:
                pass
            f.unlink(missing_ok=True)
    if settings.runtime_mode == "docker":
        try:
            import docker
            for c in docker.from_env().containers.list(all=True, filters={"label": LABEL}):
                c.remove(force=True)
        except Exception:
            pass


def resolve_source(agent, version) -> str:
    """Seeded versions fall back to the SDK example agent until agents/<slug>/main.py exists."""
    teammate = settings.agents_dir / agent.slug
    if (teammate / "main.py").exists() and str(settings.sdk_dir) in (version.source_path or ""):
        return str(teammate)
    return version.source_path


async def start(session_id: str, agent, version, env: dict) -> Sandbox:
    m = version.manifest_json
    if agent.mode == "external" and agent.endpoint_url:
        sb = Sandbox(agent.endpoint_url, hmac_secret=settings.hmac_secret_external)
        await sb.wait_healthy(10)
        return sb
    if agent.mode == "external":  # no URL configured: run the external agent locally, still signed
        env = env | {"MARKETPLACE_HMAC_SECRET": settings.hmac_secret_external}
    runtime = m.get("runtime", {})
    env = env | {"MARKETPLACE_MANIFEST": json.dumps(m)}  # the no-code runner reads its config from here
    if settings.runtime_mode == "docker" and version.image_ref:
        sb = DockerSandbox(session_id, version.image_ref, int(runtime.get("port", 8080)), env)
    else:
        sb = SubprocessSandbox(session_id, resolve_source(agent, version), nocode.entrypoint(m), env)
    if agent.mode == "external":
        sb.hmac_secret = settings.hmac_secret_external
    try:
        await sb.wait_healthy()
    except Exception:
        await sb.stop()
        raise
    return sb
