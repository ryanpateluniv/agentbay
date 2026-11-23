"""Local dev runner: start your agent, send it a task, answer its questions from the terminal.

    python -m marketplace_sdk.dev path/to/agent "Plan a 5-day trip to Tokyo"

Without a platform, ctx.llm() uses a free local stub and ctx.tool() returns empty results.
To test against the real backend proxy, run a session through the backend instead (see README).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    agent_dir, task = Path(sys.argv[1]).resolve(), sys.argv[2]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = os.environ | {"PORT": str(port), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=agent_dir, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        req = urllib.request.Request(base + "/invoke", method="POST", headers={"content-type": "application/json"},
                                     data=json.dumps({"type": "task", "session_id": "dev", "task": {"text": task}}).encode())
        with urllib.request.urlopen(req) as r:
            for line in r:
                ev = json.loads(line)
                shown = {k: (v[:60] + "..." if k == "content_base64" else v) for k, v in ev.items()}
                print("event:", json.dumps(shown))
                if ev["type"] in ("ask_user", "confirm"):
                    answer = input(f"  {ev.get('question') or ev.get('action')} > ")
                    body = {"type": "message", "text": answer, "reply_to": ev["id"]} if ev["type"] == "ask_user" else \
                        {"type": "confirm", "approved": answer.lower().startswith("y"), "reply_to": ev["id"]}
                    urllib.request.urlopen(urllib.request.Request(
                        base + "/invoke", method="POST", data=json.dumps(body).encode(),
                        headers={"content-type": "application/json"}))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
