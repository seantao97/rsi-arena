"""``rsi_arena.core.costs`` — the ledger, the ceiling and the bail-out reserve."""

from __future__ import annotations

import pytest

from rsi_arena.core.costs import (
    BudgetExceeded,
    Cost,
    CostTracker,
    MaxSpendExceeded,
    Pricing,
    Usage,
)


# --- usage and cost ---------------------------------------------------------


def test_usage_reads_openrouter_field_names():
    usage = Usage.from_openrouter({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "completion_tokens_details": {"reasoning_tokens": 5},
        "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
    })
    assert (usage.prompt_tokens, usage.reasoning_tokens, usage.cached_tokens) == (100, 5, 40)


def test_usage_of_nothing_is_zero():
    assert Usage.from_openrouter(None).total_tokens == 0


def test_usage_adds():
    total = Usage(prompt_tokens=10) + Usage(prompt_tokens=5, completion_tokens=2)
    assert total.prompt_tokens == 15 and total.completion_tokens == 2


def test_cache_hits_are_recorded_at_zero_not_dropped():
    cost = Cost.free(cached=True)
    assert cost.usd == 0.0 and cost.cached and cost.source == "free"


def test_flat_cost_carries_its_details():
    cost = Cost.flat(0.004, endpoint="search")
    assert cost.usd == 0.004 and cost.source == "fixed" and cost.details["endpoint"] == "search"


def test_pricing_estimates_from_tokens():
    pricing = Pricing.from_models_endpoint({"prompt": "0.000003", "completion": "0.000015"})
    estimate = pricing.estimate(Usage(prompt_tokens=1000, completion_tokens=100))
    assert estimate == pytest.approx(0.003 + 0.0015)


def test_pricing_survives_junk_from_the_models_endpoint():
    pricing = Pricing.from_models_endpoint({"prompt": None, "completion": "not-a-number"})
    assert pricing.prompt == 0.0 and pricing.completion == 0.0


# --- the ledger -------------------------------------------------------------


def test_totals_and_breakdowns():
    tracker = CostTracker()
    tracker.add("llm", "model-a", Cost(usd=0.01, usage=Usage(prompt_tokens=100)))
    tracker.add("api", "search", Cost.flat(0.004))
    tracker.add("llm", "model-a", Cost(usd=0.02))
    assert tracker.total_usd == pytest.approx(0.034)
    assert tracker.calls == 3
    assert tracker.by("kind") == pytest.approx({"llm": 0.03, "api": 0.004})
    assert tracker.usage.prompt_tokens == 100


def test_summary_reports_cached_calls():
    tracker = CostTracker()
    tracker.add("llm", "m", Cost.free(cached=True))
    assert tracker.summary()["cached_calls"] == 1


# --- the ceiling ------------------------------------------------------------


def test_add_raises_once_the_total_crosses_the_ceiling():
    tracker = CostTracker(max_usd=0.01)
    tracker.add("llm", "m", Cost(usd=0.009))
    with pytest.raises(BudgetExceeded):
        tracker.add("llm", "m", Cost(usd=0.002))


def test_check_refuses_before_spending_rather_than_after():
    tracker = CostTracker(max_usd=0.01)
    tracker.add("llm", "m", Cost(usd=0.01))
    with pytest.raises(BudgetExceeded) as exc:
        tracker.check("next step")
    assert "next step" in str(exc.value)


def test_call_cap_is_enforced_separately_from_the_money():
    tracker = CostTracker(max_calls=2)
    tracker.add("llm", "m", Cost.free())
    tracker.add("llm", "m", Cost.free())
    with pytest.raises(BudgetExceeded) as exc:
        tracker.check()
    assert "call limit" in str(exc.value)


def test_no_ceiling_means_no_ceiling():
    tracker = CostTracker()
    tracker.add("llm", "m", Cost(usd=1000.0))
    tracker.check()


# --- the reserve ------------------------------------------------------------


def test_the_reserve_is_not_spendable_until_it_is_opened():
    tracker = CostTracker(max_usd=1.0, reserve_usd=0.05)
    assert tracker.limit_usd == 1.0
    tracker.add("llm", "m", Cost(usd=1.0))
    with pytest.raises(BudgetExceeded):
        tracker.check()

    allowance = tracker.open_reserve()
    assert allowance == pytest.approx(0.05)
    assert tracker.limit_usd == pytest.approx(1.05)
    tracker.check()  # now affordable


def test_opening_the_reserve_lifts_the_call_cap():
    # A run that tripped max_calls still has money; refusing its one bail-out
    # call on a count would defeat the point of holding a reserve.
    tracker = CostTracker(max_usd=1.0, max_calls=1, reserve_usd=0.05)
    tracker.add("llm", "m", Cost(usd=0.1))
    with pytest.raises(BudgetExceeded):
        tracker.check()
    tracker.open_reserve()
    tracker.check()


def test_reserve_spent_reports_the_overshoot():
    tracker = CostTracker(max_usd=1.0, reserve_usd=0.05)
    tracker.add("llm", "m", Cost(usd=1.0))
    tracker.open_reserve()
    tracker.add("llm", "m", Cost(usd=0.03))
    assert tracker.reserve_spent() == pytest.approx(0.03)
    assert tracker.summary()["reserve_used_usd"] == pytest.approx(0.03)


def test_an_overshooting_bailout_is_still_recorded():
    # A model call's price is only known after it has been made, so the reserve
    # is an allowance, not a guarantee. What is guaranteed is that the real
    # number lands in the ledger.
    tracker = CostTracker(max_usd=1.0, reserve_usd=0.05)
    tracker.add("llm", "m", Cost(usd=0.99))
    tracker.open_reserve()
    with pytest.raises(BudgetExceeded):
        tracker.add("llm", "m", Cost(usd=0.20))
    assert tracker.total_usd == pytest.approx(1.19), "the overshoot must still be on the books"


def test_max_spend_exceeded_is_a_budget_exceeded_with_its_own_message():
    exc = MaxSpendExceeded(1.03, 1.0, "llm:m", reserve_usd=0.05, answered=True)
    assert isinstance(exc, BudgetExceeded)
    assert "max spend reached" in str(exc) and "answered from state" in str(exc)
    assert MaxSpendExceeded(1.0, 1.0, "x", answered=False).answered is False
