"""Measured-zero vs unknown, conservative reservations and weighted cache rates."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from casefile.agent_runtime.usage import response_usage_details
from casefile.benchmark.prose_cost_budget import (
    BudgetExhausted,
    CostBudget,
    PriceSnapshot,
    cache_observations,
    summarize_calls,
)


def prices():
    return PriceSnapshot(
        "deepseek-flash",
        "2026-09-15T00:00:00+00:00",
        "official",
        Decimal("0.04"),
        Decimal("2"),
        Decimal("8"),
    )


def details(**kwargs):
    return response_usage_details(SimpleNamespace(usage=SimpleNamespace(**kwargs)))


def test_missing_zero_and_invalid_usage():
    assert response_usage_details(SimpleNamespace(usage=None))["tokens"]["input"] is None
    zero = details(prompt_tokens=0, completion_tokens=0, prompt_cache_hit_tokens=0)
    assert zero["usage_available"] and zero["tokens"]["cached_input"] == 0
    invalid = details(prompt_tokens=10, completion_tokens=2, prompt_cache_hit_tokens=11)
    assert invalid["errors"] and invalid["raw"]["prompt_cache_hit_tokens"] == 11
    assert invalid["tokens"]["cached_input"] is None
    assert details(prompt_tokens=True)["errors"]
    assert details(prompt_tokens=-1)["tokens"]["input"] is None


def test_weighted_cache_rate_and_coverage():
    rows = [
        {
            "usage_details": details(
                prompt_tokens=100, completion_tokens=5, prompt_cache_hit_tokens=80
            )
        },
        {
            "usage_details": details(
                prompt_tokens=10, completion_tokens=5, prompt_cache_hit_tokens=0
            )
        },
        {"usage_details": None},
    ]
    result = summarize_calls(rows)
    assert result["cache_hit_rate"] == 80 / 110
    assert result["usage_coverage"] == 2 / 3
    assert result["cached_input_tokens"] == 80
    assert result["uncached_input_tokens"] == 30


def test_reserve_unknown_failure_and_stop():
    body = {"model": "deepseek-flash", "max_tokens": 100, "messages": []}
    budget = CostBudget(prices(), Decimal("0.03"))
    reserved = budget.reserve("a", body)
    with pytest.raises(ValueError, match="outstanding"):
        budget.reserve("b", body)
    budget.settle("a", None)
    assert budget.charged == reserved
    budget.reserve("b", body)
    budget.settle("b", None)
    budget.reserve("c", body)
    budget.settle("c", None)
    with pytest.raises(BudgetExhausted):
        budget.reserve("d", body)
    assert not budget.reservations


def test_settle_frees_unused_reservation_and_guards_limits():
    budget = CostBudget(prices())
    body = {"model": "deepseek-flash", "max_tokens": 100, "messages": []}
    reserve = budget.reserve("a", body)
    actual = budget.settle(
        "a", details(prompt_tokens=10, completion_tokens=2, prompt_cache_hit_tokens=5)
    )
    assert actual < reserve
    with pytest.raises(ValueError):
        budget.reserve("a", body)
    with pytest.raises(ValueError):
        CostBudget(prices(), Decimal("21"))
    with pytest.raises(ValueError):
        CostBudget(prices(), Decimal("NaN"))


def test_peak_and_offpeak_estimates():
    value = details(prompt_tokens=100, completion_tokens=10, prompt_cache_hit_tokens=50)
    p = prices()
    peak = p.estimate(value, "2026-09-15T02:00:00+00:00", "2026-09-15T02:01:00+00:00")
    offpeak = p.estimate(value, "2026-09-15T12:00:00+00:00", "2026-09-15T12:01:00+00:00")
    assert peak == offpeak * 2


def test_first_and_repeat_are_reported_without_threshold():
    rows = [
        {
            "arm": "candidate",
            "observation": observation,
            "usage_details": details(
                prompt_tokens=100,
                completion_tokens=1,
                prompt_cache_hit_tokens=70 if observation == "repeat" else 0,
            ),
        }
        for observation in ("first_observed", "repeat")
    ]
    result = cache_observations(rows)
    assert "threshold" not in result
    assert result["arms"]["candidate"]["repeat"]["cache_hit_rate"] == 0.7
    assert result["arms"]["candidate"]["first_observed"]["cache_hit_rate"] == 0
    assert result["production_status"] == "not_evaluated"
