"""Local manifest + handoff linter (stand-in until contracts/agent-manifest.schema.json lands).
Run from agents/:  python _shared/validate.py"""
import os, re, sys, json, yaml
ROOT = os.path.join(os.path.dirname(__file__), "..")
P90 = {"invoice-auditor": 15, "trip-planner": 45, "invoicegenius-pro": 3, "budget-invoice-bot": 6}
bad = 0
for n in sorted(os.listdir(ROOT)):
    d = os.path.join(ROOT, n)
    if n.startswith("_") or not os.path.isdir(d): continue
    errs = []; m = yaml.safe_load(open(os.path.join(d, "agent.yaml")))
    if not re.fullmatch(r"[a-z0-9-]+", m["name"]) or m["name"] != n: errs.append("name")
    if len(m["summary"]) > 140: errs.append(f"summary {len(m['summary'])}>140")
    if m["category"] not in ("finance", "travel", "research", "other"): errs.append("category")
    if len(m["example_tasks"]) != 3: errs.append("example_tasks != 3")
    if not set(m["models_allowed"]) <= {"fast", "balanced", "strong"}: errs.append("models_allowed must be aliases")
    if n in P90:
        if m["pricing"]["price_credits"] < P90[n] / 0.8: errs.append("price < p90/0.8")
        if m["limits"]["max_llm_cost_credits"] < P90[n]: errs.append("cost cap below p90")
    for f in ("listing.md", "README.md", "main.py", "requirements.txt", "tests/cases.jsonl"):
        if not os.path.exists(os.path.join(d, f)): errs.append("missing " + f)
    cases = [json.loads(l) for l in open(os.path.join(d, "tests/cases.jsonl")) if l.strip()]
    if not 5 <= len(cases) <= 10: errs.append(f"{len(cases)} cases (need 5-10)")
    if len(os.listdir(os.path.join(d, "samples"))) < 2: errs.append("need 2-3 samples")
    code = open(os.path.join(d, "main.py")).read()
    for lab in re.findall(r"ctx\.progress\(\s*f?\"([^\"]+)\"", code):
        if len(lab) >= 40: errs.append("label too long: " + lab)
    if re.search(r"sk-[A-Za-z0-9]{20,}", code): errs.append("hard-coded key")
    if n not in ("invoicegenius-pro", "research-briefer") and re.search(r"https?://", code): errs.append("URL in code (undeclared domain?)")
    print(("OK   " if not errs else "FAIL ") + n + ("" if not errs else ": " + "; ".join(errs))); bad += bool(errs)
sys.exit(bad)
