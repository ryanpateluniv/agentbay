"""Listings, agent cards, search (Section 9), quotes, compare."""
import math
import re
from collections import Counter
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from ..config import MICRO, settings
from ..models import Agent, AgentVersion, Listing, Quote, Rating, ReviewFinding, ReviewRun, Session, now
from .confidence import lower_bound
from .pricing import FLAT_MODELS

_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("a an the and or of for to in on with my me i this that is are be it these those under".split())


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def listing_text(listing: Listing, agent: Agent) -> str:
    return " ".join([listing.title, listing.summary, listing.description, agent.category, " ".join(listing.tags),
                     " ".join(listing.example_tasks)])


def latest_review(db: DB, version_id: str) -> ReviewRun | None:
    return db.scalar(select(ReviewRun).where(ReviewRun.agent_version_id == version_id)
                     .order_by(ReviewRun.started_at.desc()).limit(1))


def price_range(version: AgentVersion, listing: Listing, review: ReviewRun | None) -> dict:
    """Platform estimate: p50/p90 of billed price from live data, else from review measurements."""
    pricing = version.manifest_json.get("pricing", {})
    model = pricing.get("model", "per_task")
    price = int(pricing.get("price_credits", 0)) * MICRO
    live = listing.live_stats_json or {}
    if model in FLAT_MODELS:
        low = high = mx = price
    elif live.get("price_p50") is not None and live.get("n", 0) > 0:
        low, high = int(live["price_p50"]), int(live["price_p90"])
        mx = high * 3 // 2
    else:
        mk = 100 + int(pricing.get("markup_pct", 0))
        c50, c90 = (review.cost_p50, review.cost_p90) if review else (0, 0)
        base = price if model == "hybrid" else (price * 3 if model == "per_message" else 0)
        low, high = base + c50 * mk // 100, base + c90 * mk // 100
        mx = max(high * 3 // 2, MICRO)
    return {"low_microcredits": low, "high_microcredits": high, "max_microcredits": mx, "pricing_model": model}


def agent_card(db: DB, agent: Agent, relevance: float = 0.0) -> dict:
    listing = db.get(Listing, agent.id)
    version = db.get(AgentVersion, agent.current_version_id)
    review = latest_review(db, version.id) if version else None
    live = listing.live_stats_json or {}
    m = version.manifest_json if version else {}
    return {
        "id": agent.id, "slug": agent.slug, "title": listing.title, "summary": listing.summary,
        "category": agent.category, "tags": listing.tags, "mode": agent.mode, "badges": listing.badges,
        "confidence_score": listing.confidence_score, "confidence_n": listing.confidence_n,
        "confidence_lower_bound": round(lower_bound(listing.confidence_score, listing.confidence_n), 1),
        "price": price_range(version, listing, review),
        "duration_p50_s": int(live.get("duration_p50_s") or (review.latency_p50 // 1000 if review else 0)),
        "duration_p95_s": int(live.get("duration_p95_s") or (review.latency_p95 // 1000 if review else 0)),
        "success_rate": live.get("q_live"), "rating_avg": live.get("rating_avg"),
        "rating_n": int(live.get("rating_n", 0)), "relevance": round(relevance, 3),
        "inputs": m.get("inputs", []), "outputs": m.get("outputs", []),
    }


def agent_detail(db: DB, agent: Agent) -> dict:
    card = agent_card(db, agent)
    listing = db.get(Listing, agent.id)
    version = db.get(AgentVersion, agent.current_version_id)
    review = latest_review(db, version.id)
    report = None
    if review:
        findings = db.scalars(select(ReviewFinding).where(ReviewFinding.review_run_id == review.id))
        report = {"decision": review.decision, "scores": review.scores_json, "cost_p50_microcredits": review.cost_p50,
                  "cost_p90_microcredits": review.cost_p90, "latency_p50_ms": review.latency_p50,
                  "latency_p95_ms": review.latency_p95, "reviewed_at": review.finished_at,
                  "findings": [{"severity": f.severity, "category": f.category, "detail": f.detail,
                                "status": f.status} for f in findings]}
    ratings = db.execute(select(Rating).join(Session, Session.id == Rating.session_id)
                         .join(AgentVersion, AgentVersion.id == Session.agent_version_id)
                         .where(AgentVersion.agent_id == agent.id).limit(20)).scalars()
    m = version.manifest_json
    return card | {
        "description": listing.description, "example_tasks": listing.example_tasks,
        "current_version": {"id": version.id, "semver": version.semver, "status": version.status,
                            "pricing": m.get("pricing", {}), "limits": version.limits_json,
                            "reviewed_at": review.finished_at if review else None},
        "report_card": report, "tools_requested": m.get("tools_requested", []),
        "models_allowed": m.get("models_allowed", []),
        "ratings": [{"stars": r.stars, "comment": r.comment, "creator_reply": r.creator_reply} for r in ratings],
    }


def live_agents(db: DB) -> list[Agent]:
    return list(db.scalars(select(Agent).where(Agent.status == "live", Agent.current_version_id.is_not(None))))


def search(db: DB, task: str, filters: dict, limit: int = 6) -> list[dict]:
    """TF-IDF cosine relevance (embedding fallback), then rank by relevance and quality-adjusted value."""
    agents = live_agents(db)
    docs = {a.id: Counter(tokens(listing_text(db.get(Listing, a.id), a))) for a in agents}
    df = Counter(w for d in docs.values() for w in d)
    n_docs = max(1, len(docs))

    def vec(counter):
        return {w: c * (math.log((1 + n_docs) / (1 + df.get(w, 0))) + 1) for w, c in counter.items()}

    q = vec(Counter(tokens(task)))
    qn = math.sqrt(sum(v * v for v in q.values())) or 1
    results = []
    for a in agents:
        d = vec(docs[a.id])
        dn = math.sqrt(sum(v * v for v in d.values())) or 1
        rel = sum(q[w] * d.get(w, 0) for w in q) / (qn * dn)
        card = agent_card(db, a, rel)
        if not _passes(card, filters):
            continue
        card["_rank"] = 0.65 * rel + 0.35 * card["confidence_lower_bound"] / 100
        results.append(card)
    results.sort(key=lambda c: c["_rank"], reverse=True)
    if task.strip() and any(c["relevance"] > 0 for c in results):
        top = [c for c in results if c["relevance"] > 0.02]
        # exploration slot: keep one relevant beta agent visible even if it ranks low
        results = top or results
    for c in results:
        c.pop("_rank", None)
    return results[:limit]


def _passes(card: dict, f: dict) -> bool:
    if f.get("category") and card["category"] != f["category"]:
        return False
    if f.get("input_type") and f["input_type"] not in card["inputs"]:
        return False
    if f.get("output_type") and f["output_type"] not in card["outputs"]:
        return False
    if f.get("max_price_microcredits") is not None and card["price"]["low_microcredits"] > f["max_price_microcredits"]:
        return False
    if f.get("max_minutes") is not None and card["duration_p50_s"] > f["max_minutes"] * 60:
        return False
    if f.get("verified_only") and "verified" not in card["badges"]:
        return False
    return True


def make_quote(db: DB, buyer_id: str, agent: Agent, task: str) -> Quote:
    version = db.get(AgentVersion, agent.current_version_id)
    pr = price_range(version, db.get(Listing, agent.id), latest_review(db, version.id))
    q = Quote(buyer_id=buyer_id, agent_version_id=version.id, task_text=task, low=pr["low_microcredits"],
              high=pr["high_microcredits"], max=pr["max_microcredits"],
              expires_at=now() + timedelta(seconds=settings.quote_lock_seconds))
    db.add(q)
    db.commit()
    return q


def quote_out(db: DB, q: Quote) -> dict:
    v = db.get(AgentVersion, q.agent_version_id)
    return {"id": q.id, "agent_id": v.agent_id, "agent_version_id": v.id, "low_microcredits": q.low,
            "high_microcredits": q.high, "max_microcredits": q.max, "suggested_cap_microcredits": q.high,
            "pricing_model": v.pricing_model, "expires_at": q.expires_at, "agent_notes": q.agent_notes}


def compare(db: DB, agents: list[Agent]) -> dict:
    details = [agent_detail(db, a) for a in agents]

    def best(key, reverse=True):
        if not details:
            return None
        return sorted(details, key=key, reverse=reverse)[0]["id"]
    return {"agents": details, "highlights": {
        "best_value": best(lambda d: d["confidence_score"] / max(1, d["price"]["high_microcredits"])),
        "highest_confidence": best(lambda d: d["confidence_lower_bound"]),
        "fastest": best(lambda d: d["duration_p50_s"] or 10**9, reverse=False),
    }}
