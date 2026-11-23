"""Confidence score (Blueprint Section 8).

Confidence = 100 * (0.40 Q* + 0.20 R* + 0.20 S + 0.10 K + 0.10 U),
X* = (1 - lambda) X_review + lambda X_live,  lambda = n / (n + 30)
"""
import math

CATEGORY_AVG_RATING = 0.8  # U prior (4.0 stars) used for shrinkage
RATING_PRIOR_WEIGHT = 10


def blend(review: float, live: float | None, n: int) -> float:
    if live is None or n <= 0:
        return review
    lam = n / (n + 30)
    return (1 - lam) * review + lam * live


def user_rating_score(avg_stars: float | None, rating_n: int) -> float:
    if not avg_stars or rating_n <= 0:
        return CATEGORY_AVG_RATING
    scaled = (avg_stars - 1) / 4  # 1..5 stars -> 0..1
    return (scaled * rating_n + CATEGORY_AVG_RATING * RATING_PRIOR_WEIGHT) / (rating_n + RATING_PRIOR_WEIGHT)


def compute_confidence(review_scores: dict, live: dict | None = None) -> tuple[int, int]:
    """Returns (score 0-100, n sessions)."""
    live = live or {}
    n = int(live.get("n", 0))
    q = blend(review_scores.get("Q", 0), live.get("q_live"), n)
    r = blend(review_scores.get("R", 0), live.get("r_live"), n)
    s = review_scores.get("S", 0)
    k = review_scores.get("K", 0)
    u = user_rating_score(live.get("rating_avg"), int(live.get("rating_n", 0)))
    score = 100 * (0.40 * q + 0.20 * r + 0.20 * s + 0.10 * k + 0.10 * u)
    return int(round(score)), n


def lower_bound(score: int, n: int, z: float = 1.64) -> float:
    """Conservative ranking bound: Wilson lower bound on score/100 with review evidence counted as 10
    pseudo-sessions, so a score from 3 sessions never outranks a similar one from 300."""
    p = score / 100
    m = n + 10
    denom = 1 + z * z / m
    centre = p + z * z / (2 * m)
    margin = z * math.sqrt(p * (1 - p) / m + z * z / (4 * m * m))
    return max(0.0, (centre - margin) / denom) * 100


def badges_for(score: int, n: int, mode: str) -> list[str]:
    badges = []
    if n < 30:
        badges.append("beta")
    elif score >= 90 and n >= 200:
        badges.append("top_rated")
    if n >= 30 and score >= 80:
        badges.append("verified")
    if mode == "external":
        badges.append("external")
    return badges
