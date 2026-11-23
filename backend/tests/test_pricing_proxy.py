from app.config import MICRO
from app.services import pricing, proxy


def test_cost_formula_integer_and_rounded_up():
    # 1820 in / 412 out on fast ($0.15/M in, $0.60/M out), m = 10%:
    # provider = 273.0 + 247.2 = 520.2 micro-USD -> 52.02 microcredits * 1.1 = 57.22 -> 58
    cost, provider_uusd = pricing.llm_cost(1820, 412, 150_000, 600_000, 1000)
    assert cost == 58 and provider_uusd == 521
    assert isinstance(cost, int)
    # spec example: $0.0031 provider cost -> 0.31 credits -> 310 microcredits at m=0
    assert pricing.llm_cost(1240, 0, 2_500_000, 0, 0)[0] == 310


def test_cost_formula_markup_and_zero():
    assert pricing.llm_cost(0, 0, 150_000, 600_000, 1000) == (0, 0)
    base = pricing.llm_cost(100_000, 100_000, 2_500_000, 10_000_000, 0)[0]
    marked = pricing.llm_cost(100_000, 100_000, 2_500_000, 10_000_000, 1000)[0]
    assert base == 125_000 and marked == 137_500


def test_max_output_tokens_keeps_call_under_budget():
    remaining = 500  # microcredits
    n = pricing.max_output_tokens(remaining, 600_000, 1000)
    assert pricing.llm_cost(0, n, 0, 600_000, 1000)[0] <= remaining + 1
    assert pricing.llm_cost(0, n + 10, 0, 600_000, 1000)[0] > remaining


def test_compute_price_rules():
    per_task = {"model": "per_task", "price_credits": 150}
    cap = 200 * MICRO
    kw = dict(cost=40 * MICRO, cap=cap)
    assert pricing.compute_price(per_task, "completed", **kw, has_result=True) == 150 * MICRO
    assert pricing.compute_price(per_task, "failed", **kw) == 0
    assert pricing.compute_price(per_task, "timed_out", **kw) == 0
    assert pricing.compute_price(per_task, "cancelled", **kw, work_started=False) == 0
    assert pricing.compute_price(per_task, "cancelled", **kw, work_started=True) == 75 * MICRO
    assert pricing.compute_price(per_task, "budget_exhausted", **kw, has_result=True) == 150 * MICRO
    assert pricing.compute_price(per_task, "budget_exhausted", **kw, has_result=False) == 0
    usage = {"model": "usage_based", "markup_pct": 150}
    assert pricing.compute_price(usage, "completed", **kw) == 100 * MICRO  # 40 x 2.5
    outcome = {"model": "outcome_based", "price_credits": 50}
    assert pricing.compute_price(outcome, "completed", **kw, has_result=False) == 0
    per_msg = {"model": "per_message", "price_credits": 10}
    assert pricing.compute_price(per_msg, "completed", **kw, agent_messages=3) == 30 * MICRO
    per_min = {"model": "per_minute", "price_credits": 5}
    assert pricing.compute_price(per_min, "completed", **kw, active_ms=61_000) == 10 * MICRO
    assert pricing.compute_price({"model": "per_message", "price_credits": 100}, "completed", **kw,
                                 agent_messages=5) == cap  # never above the cap


def test_cost_limit():
    lim = {"max_llm_cost_credits": 200}
    assert pricing.cost_limit({"model": "per_task"}, 150 * MICRO, lim) == 150 * MICRO
    assert pricing.cost_limit({"model": "per_task"}, 500 * MICRO, lim) == 200 * MICRO
    assert pricing.cost_limit({"model": "usage_based", "markup_pct": 150}, 100 * MICRO, lim) == 40 * MICRO


def test_fake_llm_deterministic_with_realistic_tokens():
    msgs = [{"role": "user", "content": "Plan a 5-day trip to Tokyo under $2,000 with food and museums"}]
    a = proxy.fake_completion("fast", msgs, 1024)
    b = proxy.fake_completion("fast", msgs, 1024)
    assert a == b
    text, t_in, t_out = a
    assert 10 <= t_in <= 40 and 50 <= t_out <= 300 and t_out == proxy.count_tokens(text)
    assert proxy.fake_completion("fast", msgs, 20)[2] <= 20
