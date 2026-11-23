"""Cost formula (Section 6) and buyer charge P per pricing model (Section 7). Integers only."""
from ..config import MICRO

FLAT_MODELS = ("per_task", "outcome_based")


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def llm_cost(t_in: int, t_out: int, price_in: int, price_out: int, markup_bps: int) -> tuple[int, int]:
    """C_call = (t_in*p_in + t_out*p_out) * (1+m) / v_credit, in microcredits (rounded up).

    Prices are micro-USD per 1M tokens. v_credit = $0.01 = 10_000 micro-USD = 1000 microcredits,
    so 1 microcredit = 10 micro-USD. Returns (cost_microcredits, provider_cost_micro_usd)."""
    raw = t_in * price_in + t_out * price_out  # micro-USD * 1e6
    cost = ceil_div(raw * (10_000 + markup_bps), 1_000_000 * 10 * 10_000)
    return cost, ceil_div(raw, 1_000_000)


def max_output_tokens(remaining: int, price_out: int, markup_bps: int) -> int:
    """Largest output that keeps one call within the remaining budget (input cost ignored; overshoot absorbed)."""
    if price_out <= 0:
        return 1_000_000
    return max(1, (remaining * 1_000_000 * 10 * 10_000) // (price_out * (10_000 + markup_bps)))


def _markup_pct(pricing: dict) -> int:
    return int(pricing.get("markup_pct", 0))


def full_charge(pricing: dict, cost: int, agent_messages: int, active_ms: int) -> int:
    model = pricing.get("model", "per_task")
    price = int(pricing.get("price_credits", 0)) * MICRO
    usage = cost * (100 + _markup_pct(pricing)) // 100
    if model in FLAT_MODELS:
        return price
    if model == "per_message":
        return price * agent_messages
    if model == "per_minute":
        return price * ceil_div(active_ms, 60_000) if active_ms > 0 else 0
    if model == "usage_based":
        return usage
    if model == "hybrid":
        return price + usage
    raise ValueError(f"unknown pricing model {model}")


def compute_price(pricing: dict, outcome: str, *, cost: int, cap: int, agent_messages: int = 0, active_ms: int = 0,
                  has_result: bool = False, work_started: bool = False) -> int:
    """Buyer charge P for a finished session, applying the Section 7 failure/cancel rules."""
    model = pricing.get("model", "per_task")
    full = full_charge(pricing, cost, agent_messages, active_ms)
    if outcome in ("failed", "timed_out"):
        p = 0
    elif outcome == "cancelled":
        if not work_started:
            p = 0
        elif model in FLAT_MODELS:
            p = full // 2  # per-task charges 50% of P once work started (assumption in the spec)
        elif model == "hybrid":
            p = int(pricing.get("price_credits", 0)) * MICRO // 2 + (full - int(pricing.get("price_credits", 0)) * MICRO)
        else:
            p = full  # consumed units
    elif outcome == "budget_exhausted":
        p = full if has_result else 0
    elif outcome == "completed":
        p = full if (model != "outcome_based" or has_result) else 0
    else:
        raise ValueError(outcome)
    return max(0, min(p, cap))


def cost_limit(pricing: dict, cap: int, limits: dict) -> int:
    """Model+tool spend limit the proxy enforces: the buyer's cap (scaled for usage-based pricing so the
    charge stays under the cap), never above the manifest's max_llm_cost_credits."""
    model = pricing.get("model", "per_task")
    if model in ("usage_based", "hybrid"):
        base = int(pricing.get("price_credits", 0)) * MICRO if model == "hybrid" else 0
        cap_for_cost = max(0, cap - base) * 100 // (100 + _markup_pct(pricing))
    else:
        cap_for_cost = cap
    manifest_limit = int(limits.get("max_llm_cost_credits", 10**9)) * MICRO
    return min(cap_for_cost, manifest_limit)
