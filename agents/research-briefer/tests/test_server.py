"""Run: LLM_MODE=fake MARKETPLACE_SHARED_SECRET=testsecret python tests/test_server.py"""
import os, sys, json, time, hmac, hashlib, threading, urllib.request, urllib.error
os.environ.setdefault("LLM_MODE", "fake"); os.environ["MARKETPLACE_SHARED_SECRET"] = "testsecret"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import main
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), main.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/invoke"
def call(body, ts=None, secret="testsecret", sig=None):
    raw = json.dumps(body).encode(); ts = str(ts or int(time.time()))
    sig = sig or hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    rq = urllib.request.Request(url, raw, {"X-Timestamp": ts, "X-Signature": sig, "Content-Type": "application/json"})
    try: r = urllib.request.urlopen(rq); return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, None
assert call({"session_id": "a", "text": "x"}, secret="wrong")[0] == 401, "bad secret must be rejected"
assert call({"session_id": "a", "text": "x"}, ts=int(time.time()) - 1000)[0] == 401, "stale timestamp must be rejected"
assert call({"session_id": "a", "text": "x"}, sig="00")[0] == 401
s1 = call({"session_id": "s", "text": "EU battery regulation"}); assert s1[0] == 200 and not s1[1]["done"] and "?" in s1[1]["text"]
s2 = call({"session_id": "s", "text": "regulation"}); assert "Brief" in s2[1]["text"] and not s2[1]["done"]
s3 = call({"session_id": "s", "text": "Which finding matters most?"}); assert s3[1]["done"] and "s" not in main._sessions
e = call({"session_id": "e", "text": ""}); assert "topic" in e[1]["text"].lower()
print("all research-briefer tests passed")
