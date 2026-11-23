"""Local stand-in for `marketplace test`. Run from an agent folder:
   PYTHONPATH=../_shared/devstub LLM_MODE=fake python ../_shared/devstub/run_cases.py"""
import os, sys, json, time, shutil, tempfile, importlib.util, io, contextlib
os.environ["MP_STUB_RUN"] = "1"; os.environ.setdefault("LLM_MODE", "fake")
import marketplace_sdk as sdk

def run_case(case):
    ws = tempfile.mkdtemp()
    for f in case["input"].get("files", []): shutil.copy(os.path.join("tests", "files", f), ws)
    ctx = sdk.Context(sdk._Task(case["input"].get("text", ""), case["input"].get("files", [])), ws,
                      answers=case.get("answers"), budget=case["input"].get("budget_credits", 1000.0))
    spec = importlib.util.spec_from_file_location("agent_main", "main.py"); mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.getcwd()); spec.loader.exec_module(mod)
    t = time.time(); crashed = None
    with contextlib.redirect_stdout(io.StringIO()):
        try: sdk.LAST_AGENT.handler(ctx)
        except Exception as e: crashed = repr(e)
    dur = time.time() - t
    blob = (ctx.result or "") + " " + " ".join(e.get("text", "") for e in ctx.events if e["type"] == "message")
    for f in ctx.result_files:
        p = os.path.join(ws, f)
        if os.path.exists(p): blob += " " + open(p, errors="ignore").read()
    blob = blob.lower(); fails = []
    if crashed: fails.append("crash " + crashed)
    if ctx.result is None and not crashed: fails.append("no result returned")
    for s in case.get("expect", {}).get("must_find", []):
        if s.lower() not in blob: fails.append(f"missing: {s!r}")
    for s in case.get("expect", {}).get("must_not", []):
        if s.lower() in blob: fails.append(f"forbidden: {s!r}")
    if dur > case.get("expect", {}).get("max_duration_s", 1e9): fails.append("too slow")
    for e in ctx.events:
        if e["type"] == "progress" and len(e["label"]) >= 40: fails.append("label >=40 chars: " + e["label"])
    return fails

if __name__ == "__main__":
    cases = [json.loads(l) for l in open("tests/cases.jsonl") if l.strip()]
    bad = 0
    for c in cases:
        f = run_case(c); bad += bool(f)
        print(("PASS " if not f else "FAIL ") + c["id"] + ("" if not f else "  " + "; ".join(f)))
    print(f"{len(cases)-bad}/{len(cases)} passed"); sys.exit(1 if bad else 0)
