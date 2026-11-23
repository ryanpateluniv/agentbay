"""Generates agent.yaml, listing.md and README.md for every agent from one table (keeps them consistent)."""
import yaml, os
yaml.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None), Dumper=yaml.SafeDumper)
ROOT = os.path.join(os.path.dirname(__file__), "..")
A = [
 dict(name="invoice-auditor", display="Invoice Auditor", cat="finance", tags=["invoice","audit","accounting","finance"],
  summary="Finds math errors, duplicate lines and missing fields in an invoice, then ranks the problems by risk.",
  desc="Upload an invoice (pasted text, .txt or text-based PDF). The agent extracts line items, re-checks every total and tax figure in code, flags duplicate lines and missing required fields, and returns an audit report with the problems ranked by risk. It asks at most one question (whether prices include tax) and only when the invoice has no tax line.",
  tasks=["Check this invoice for math errors and duplicate charges","Audit this invoice and tell me if any required fields are missing","Does the tax on this invoice add up?"],
  models=["fast","strong"], tools=["file_storage"], inputs=["text","pdf"], lim=(10,30,20), price=60, p=(8,15), mode="hosted"),
 dict(name="trip-planner", display="Trip Planner", cat="travel", tags=["travel","itinerary","planning","trip"],
  summary="Plans a multi-day trip through a short chat: you pick from three outlines and approve the final itinerary.",
  desc="Tell the planner where you want to go, for how long, your budget and what you enjoy. It drafts three itinerary outlines, lets you choose one, builds a detailed day-by-day plan and saves it after you approve. It uses the model's general knowledge unless live web search is available, and says which one it used.",
  tasks=["Plan 4 days in Lisbon for two people, mid-range budget, we love food and history","Weekend trip somewhere warm, budget friendly","Plan a 5-day Kyoto trip focused on temples and gardens"],
  models=["fast","strong"], tools=["web_search","file_storage"], inputs=["text"], lim=(10,90,10), price=120, p=(25,45), mode="hosted"),
 dict(name="research-briefer", display="Research Briefer", cat="research", tags=["research","brief","summary"],
  summary="Gives a one-page research brief with five findings and a watch list, then answers one follow-up. No live web access.",
  desc="Send a topic. The briefer asks one clarifying question, writes a one-page brief with five findings and a short 'what to watch' list, then answers one follow-up question. It runs as an external service outside the platform sandbox and has no live web access, so check time-sensitive facts.",
  tasks=["Brief me on solid-state batteries","Give me a one-page brief on EU AI regulation for startups","What should I know about remote work trends in 2026?"],
  models=[], tools=[], inputs=["text"], lim=(5,0,0), price=10, p=None, mode="external"),
 dict(name="invoicegenius-pro", display="InvoiceGenius Pro", cat="finance", tags=["invoice","audit","finance"],
  summary="99.9% accurate instant invoice auditor for any format",
  desc="DEMO FIXTURE - NEVER DEPLOY. Intentionally dishonest agent used to show the review pipeline rejecting a bad agent: fabricated reports, an undeclared network call and obeying hidden instructions.",
  tasks=["Audit this invoice instantly","Is this invoice accurate?","Find all errors in this invoice"],
  models=["fast"], tools=[], inputs=["text","pdf"], lim=(5,6,5), price=25, p=(2,3), mode="hosted"),
 dict(name="budget-invoice-bot", display="Budget Invoice Bot", cat="finance", tags=["invoice","audit","budget","finance"],
  summary="Quick, low-cost invoice check for arithmetic errors and duplicate lines. Does not check tax fields or dates.",
  desc="A cheaper, simpler alternative to Invoice Auditor. It re-checks line amounts, subtotal and total and flags duplicate lines. It does not check required fields, due dates or tax rates, does no model review and asks no questions.",
  tasks=["Quickly check this invoice for math errors","Are there duplicate lines on this invoice?","Does this invoice total add up?"],
  models=["fast"], tools=["file_storage"], inputs=["text","pdf"], lim=(5,12,10), price=30, p=(3,6), mode="hosted"),
]
for a in A:
    d = os.path.join(ROOT, a["name"]); ext = a["mode"] == "external"
    m = dict(name=a["name"], display_name=a["display"], version="1.0.0", summary=a["summary"], description=a["desc"] + "\n",
             category=a["cat"], tags=a["tags"], example_tasks=a["tasks"], inputs=a["inputs"], outputs=["markdown"])
    if ext:
        m["runtime"] = dict(type="external", endpoint="https://REPLACE-ME.example.com", language="python", entrypoint="python main.py", port=8081)
        m["tools_requested"] = []; m["models_allowed"] = []; m["network"] = dict(allowed_domains=[])
        m["limits"] = dict(max_session_minutes=5, max_messages=3)
        m["pricing"] = dict(model="per_message", price_credits=a["price"]); m["secrets"] = ["OPENAI_API_KEY", "MARKETPLACE_SHARED_SECRET"]
    else:
        m["runtime"] = dict(language="python", entrypoint="python main.py", port=8080)
        m["tools_requested"] = a["tools"]; m["models_allowed"] = a["models"]; m["network"] = dict(allowed_domains=[])
        m["limits"] = dict(max_session_minutes=a["lim"][0], max_llm_cost_credits=a["lim"][1], max_tool_calls=a["lim"][2])
        m["pricing"] = dict(model="per_task", price_credits=a["price"]); m["secrets"] = []
    m["review"] = dict(test_cases="tests/cases.jsonl", support_contact="team@example.com")
    head = "# PROPOSED fields for external agents (runtime.type, endpoint, max_messages, per_message): confirm with backend owner\n" if ext else \
           "# DEMO FIXTURE - NEVER DEPLOY. It deliberately breaks network rules; allowed_domains is empty on purpose.\n" if a["name"] == "invoicegenius-pro" else ""
    open(os.path.join(d, "agent.yaml"), "w").write(head + yaml.safe_dump(m, sort_keys=False, width=100, allow_unicode=True))
    open(os.path.join(d, "listing.md"), "w").write(f"# {a['display']}\n\n**Summary:** {a['summary']}\n\n## Description\n\n{a['desc']}\n\n## Example tasks\n\n" + "".join(f"{i}. {t}\n" for i, t in enumerate(a["tasks"], 1)))
    cost = f"| p50 / p90 target | {a['p'][0]} / {a['p'][1]} credits |\n| Measured p50 / p90 | _TBD - run `marketplace test` with the real runtime_ |\n" if a["p"] else "| Metering | 10 credits per message, up to 3 messages per session (about 30 credits) |\n"
    warn = "> **DEMO FIXTURE - NEVER DEPLOY.** Intentionally bad agent for the review-pipeline demo. It sends invoice text to `collect.invoicegenius.invalid` (never resolves) and obeys hidden instructions.\n\n" if a["name"] == "invoicegenius-pro" else ""
    run = ("```bash\nexport MARKETPLACE_SHARED_SECRET=changeme OPENAI_API_KEY=...   # key stays in a local .env, never committed\npython main.py                                  # serves on :8081\nLLM_MODE=fake python tests/test_server.py       # signature + session tests, no key needed\n```\n" if ext else
           "```bash\n# Local stub (until the real SDK lands):\nPYTHONPATH=../_shared/devstub LLM_MODE=fake python main.py \"Audit this invoice\" path/to/file.txt\nPYTHONPATH=../_shared/devstub LLM_MODE=fake python ../_shared/devstub/run_cases.py\n# Real SDK: pip install -e sdk/python && LLM_MODE=fake marketplace dev && marketplace test\n```\n")
    open(os.path.join(d, "README.md"), "w").write(f"# {a['display']}\n\n{warn}{a['summary']}\n\n## Run locally\n\n{run}\n## Numbers\n\n| | |\n|---|---|\n| Price | {a['price']} credits ({'per message' if ext else 'per task'}) |\n{cost}\n## Notes\n\n- Test cases: `tests/cases.jsonl`. Fixtures in `tests/files/`.\n- Samples for the listing page: `samples/`.\n")
print("generated", len(A), "agents")
