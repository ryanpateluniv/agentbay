"""Seed demo data: users with credits, model aliases and prices, 4 listings with review + live stats.

Run: python -m app.seed  (from backend/). Idempotent: drops and recreates the SQLite DB with --reset.
"""
import os
import sys

from sqlalchemy import select

from .config import MICRO, settings
from .db import Base, SessionLocal, engine, init_db
from .models import (Agent, AgentVersion, CreatorProfile, Listing, ModelPrice, PlatformSetting, ReviewFinding,
                     ReviewRun, ToolPrice, User, now)
from .services import ledger

USERS = [
    # id, email, name, roles, token, promo credits, purchased credits
    ("usr_sam", "sam@demo.dev", "Sam (buyer)", ["buyer"], "tok_sam", 200, 4800),
    ("usr_riya", "riya@demo.dev", "Riya (creator)", ["buyer", "creator"], "tok_riya", 200, 0),
    ("usr_admin", "admin@demo.dev", "Admin", ["admin", "buyer"], "tok_admin", 0, 0),
]

MODEL_PRICES = [
    # alias, provider model (overridable), micro-USD per 1M input tokens, per 1M output tokens
    ("fast", os.environ.get("LLM_MODEL_FAST", "gpt-4o-mini"), 150_000, 600_000),
    ("balanced", os.environ.get("LLM_MODEL_BALANCED", "gpt-4.1-mini"), 400_000, 1_600_000),
    ("strong", os.environ.get("LLM_MODEL_STRONG", "gpt-4o"), 2_500_000, 10_000_000),
]

TOOL_PRICES = [("web_search", "call", 500), ("file_storage", "call", 0)]


def manifest(name, display, summary, category, tags, examples, pricing, *, inputs=("text",), outputs=("markdown",),
             models=("fast", "strong"), tools=("web_search",), limits=None, domains=()):
    return {
        "name": name, "display_name": display, "version": "1.0.0", "summary": summary,
        "description": summary, "category": category, "tags": list(tags), "example_tasks": list(examples),
        "inputs": list(inputs), "outputs": list(outputs),
        "runtime": {"language": "python", "entrypoint": "python main.py", "port": 8080},
        "tools_requested": list(tools), "models_allowed": list(models), "allowed_domains": list(domains),
        "limits": limits or {"max_session_minutes": 20, "max_llm_cost_credits": 200, "max_tool_calls": 100},
        "pricing": pricing, "secrets": [], "review": {"test_cases": "tests/cases.jsonl"},
        "support_contact": "riya@demo.dev",
    }


LISTINGS = [
    {
        "slug": "invoice-auditor", "mode": "hosted",
        "manifest": manifest("invoice-auditor", "Invoice Auditor",
                             "Checks invoices for duplicate charges, math errors, and policy violations, step by step.",
                             "business", ["invoices", "finance", "audit", "accounting"],
                             ["Audit this invoice for duplicate line items", "Check these 3 invoices against our expense policy"],
                             {"model": "per_task", "price_credits": 40}, inputs=("text", "pdf"), tools=()),
        "review": dict(q=0.9, r=0.95, s=1.0, k=0.9, cost_p50=6, cost_p90=11, lat_p50=4200, lat_p95=9100),
        "live": dict(n=48, q=0.88, r=0.96, rating=4.5, rating_n=21, price_p50=40, price_p90=40, sec_p50=38, sec_p95=95),
    },
    {
        "slug": "trip-planner", "mode": "hosted",
        "manifest": manifest("trip-planner", "Trip Planner",
                             "Plans multi-city trips with flights, hotels, and a day-by-day itinerary. Asks about your budget first.",
                             "travel", ["travel", "planning", "itinerary", "flights", "hotels"],
                             ["Plan a 5-day trip to Tokyo under $2,000", "Weekend in Lisbon for two, food focused"],
                             {"model": "per_task", "price_credits": 150}, inputs=("text", "pdf"), outputs=("markdown", "pdf"),
                             tools=("web_search", "file_storage")),
        "review": dict(q=0.85, r=0.9, s=1.0, k=0.85, cost_p50=40, cost_p90=95, lat_p50=21000, lat_p95=52000),
        "live": dict(n=12, q=0.83, r=0.92, rating=4.6, rating_n=7, price_p50=150, price_p90=150, sec_p50=95, sec_p95=240),
    },
    {
        "slug": "research-briefer", "mode": "external",
        "manifest": manifest("research-briefer", "Research Briefer",
                             "Turns a topic into a sourced one-page brief. Runs on the creator's own server (External).",
                             "research", ["research", "summary", "brief", "sources", "market"],
                             ["Brief me on the EU AI Act in one page", "Summarize recent research on solid-state batteries"],
                             {"model": "per_message", "price_credits": 10}, tools=()),
        "review": dict(q=0.8, r=0.88, s=0.95, k=0.8, cost_p50=0, cost_p90=0, lat_p50=15000, lat_p95=33000),
        "live": dict(n=230, q=0.86, r=0.93, rating=4.3, rating_n=88, price_p50=30, price_p90=60, sec_p50=60, sec_p95=150),
    },
    {
        "slug": "quick-summarizer", "mode": "hosted",
        "manifest": manifest("quick-summarizer", "Quick Summarizer",
                             "Summarizes any text or document into key points. Usage-based pricing: you pay measured cost x 2.5.",
                             "writing", ["summary", "notes", "documents", "tl;dr"],
                             ["Summarize this meeting transcript", "Give me the key points of this article"],
                             {"model": "usage_based", "price_credits": 0, "markup_pct": 150}, tools=()),
        "review": dict(q=0.92, r=0.97, s=1.0, k=0.95, cost_p50=2, cost_p90=5, lat_p50=2500, lat_p95=6000),
        "live": dict(n=310, q=0.91, r=0.98, rating=4.7, rating_n=140, price_p50=5, price_p90=12, sec_p50=12, sec_p95=30),
    },
]


def source_path_for(slug: str) -> str:
    """Prefer the teammate's agent in agents/<slug>; fall back to the SDK example agent."""
    p = settings.agents_dir / slug
    return str(p if (p / "main.py").exists() else settings.sdk_dir / "examples" / "echo_agent")


def seed(db=None, reset: bool = False):
    if reset:
        Base.metadata.drop_all(engine)
    init_db()
    db = db or SessionLocal()
    if db.get(User, "usr_sam"):
        print("already seeded")
        return
    for key, value in (("fee_bps", settings.fee_bps), ("markup_bps", settings.markup_bps)):
        db.add(PlatformSetting(key=key, value=value))
    for uid, email, name, roles, tok, promo, bought in USERS:
        db.add(User(id=uid, email=email, name=name, roles=roles, api_token=tok))
        if "creator" in roles:
            db.add(CreatorProfile(user_id=uid, display_name=name.split(" ")[0], verification_level="established"))
    for alias, pm, pin, pout in MODEL_PRICES:
        db.add(ModelPrice(alias=alias, provider_model=pm, price_in=pin, price_out=pout))
    for tool, unit, price in TOOL_PRICES:
        db.add(ToolPrice(tool=tool, unit=unit, price=price))
    db.commit()
    for uid, _, _, _, _, promo, bought in USERS:
        if promo:
            ledger.grant_promo(db, uid, promo * MICRO, f"seed:promo:{uid}")
        if bought:
            ledger.topup(db, uid, bought * MICRO, f"seed:topup:{uid}", "seed")

    from .services.confidence import compute_confidence, badges_for
    for spec in LISTINGS:
        m = spec["manifest"]
        agent = Agent(id=f"agt_{spec['slug'].replace('-', '_')}", creator_id="usr_riya", slug=spec["slug"],
                      category=m["category"], status="live", mode=spec["mode"],
                      endpoint_url=os.environ.get(f"EXTERNAL_URL_{spec['slug'].upper().replace('-', '_')}"))
        db.add(agent)
        db.flush()
        ver = AgentVersion(id=f"agv_{spec['slug'].replace('-', '_')}_1", agent_id=agent.id, semver=m["version"],
                           source_path=source_path_for(spec["slug"]), manifest_json=m, status="approved",
                           pricing_model=m["pricing"]["model"], price_credits=m["pricing"].get("price_credits", 0),
                           limits_json=m["limits"])
        db.add(ver)
        agent.current_version_id = ver.id
        rv = spec["review"]
        live = spec["live"]
        scores = {"Q": rv["q"], "R": rv["r"], "S": rv["s"], "K": rv["k"]}
        run = ReviewRun(agent_version_id=ver.id, status="done", stage="decision", finished_at=now(), decision="approved",
                        scores_json=scores, cost_p50=rv["cost_p50"] * MICRO, cost_p90=rv["cost_p90"] * MICRO,
                        latency_p50=rv["lat_p50"], latency_p95=rv["lat_p95"],
                        report_json={"seeded": True, "decision": "approved", "scores": scores,
                                     "notes": "Pre-run review (seeded for the demo)."})
        db.add(run)
        db.flush()
        db.add(ReviewFinding(review_run_id=run.id, severity="info", category="static_scan",
                             detail="No provider domains or hard-coded secrets found.", status="resolved"))
        live_stats = {"n": live["n"], "q_live": live["q"], "r_live": live["r"], "rating_avg": live["rating"],
                      "rating_n": live["rating_n"], "price_p50": live["price_p50"] * MICRO,
                      "price_p90": live["price_p90"] * MICRO, "duration_p50_s": live["sec_p50"],
                      "duration_p95_s": live["sec_p95"], "seeded": True}
        score, n = compute_confidence(scores, live_stats)
        db.add(Listing(agent_id=agent.id, title=m["display_name"], summary=m["summary"], description=m["description"],
                       tags=m["tags"], example_tasks=m["example_tasks"], confidence_score=score, confidence_n=n,
                       badges=badges_for(score, n, agent.mode), live_stats_json=live_stats))
    db.commit()
    print("seeded: users sam/riya/admin (tokens tok_sam, tok_riya, tok_admin); 4 listings")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
