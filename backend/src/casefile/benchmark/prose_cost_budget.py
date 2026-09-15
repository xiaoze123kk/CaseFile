"""Serial, reserve-before-send budget accounting for the opt-in prose cost smoke."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from casefile.agent_runtime.prose_skills import compact_json


@dataclass(frozen=True)
class PriceSnapshot:
    model_id: str
    verified_at: str
    source: str
    cached_per_million: Decimal
    uncached_per_million: Decimal
    output_per_million: Decimal
    currency: str = "CNY"
    conversion_basis: str = "Official CNY price; no FX conversion"

    def __post_init__(self) -> None:
        if self.currency != "CNY" or not self.source or not self.verified_at:
            raise ValueError("Verified CNY price snapshot required")
        for rate in (self.cached_per_million, self.uncached_per_million, self.output_per_million):
            if not rate.is_finite() or rate < 0:
                raise ValueError("Invalid price")
        if self.uncached_per_million <= 0 or self.output_per_million <= 0:
            raise ValueError("Positive reservation prices required")
        if self.cached_per_million > self.uncached_per_million:
            raise ValueError("Cache price exceeds uncached price")

    def cost(self, input_tokens: int, output_tokens: int, cached: int = 0) -> Decimal:
        return (
            (input_tokens - cached) * self.uncached_per_million
            + cached * self.cached_per_million
            + output_tokens * self.output_per_million
        ) / Decimal(1_000_000)

    def estimate(self, details: dict[str, Any], started: str, ended: str) -> Decimal | None:
        tokens = details["tokens"]
        if tokens["input"] is None or tokens["output"] is None or details["errors"]:
            return None
        if tokens["cached_input"] is None:
            return None
        # Official schedule: weekdays UTC 01-04, 06-10. A boundary-crossing call
        # uses the peak upper estimate because the provider billing instant is unknown.
        start, end = datetime.fromisoformat(started), datetime.fromisoformat(ended)

        def peak(moment: datetime) -> bool:
            return moment.weekday() < 5 and (1 <= moment.hour < 4 or 6 <= moment.hour < 10)

        factor = (
            Decimal(1) if peak(start) or peak(end) or start.hour != end.hour else Decimal("0.5")
        )
        return self.cost(tokens["input"], tokens["output"], tokens["cached_input"]) * factor


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class CostBudget:
    prices: PriceSnapshot
    limit: Decimal = Decimal("20")
    charged: Decimal = Decimal(0)
    reservations: dict[str, Decimal] = field(default_factory=dict)
    settled: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.limit.is_finite() or not Decimal(0) < self.limit <= Decimal(20):
            raise ValueError("Cost smoke budget must be in (0, 20] CNY")

    def reserve(self, call_id: str, body: dict[str, Any]) -> Decimal:
        if self.reservations:
            raise ValueError("Serial budget: outstanding reservation")
        if call_id in self.settled or body["model"] != self.prices.model_id:
            raise ValueError("Duplicate call or model mismatch")
        # UTF-8 byte count bounds byte-tokenized text; double it plus framing reserve.
        upper_input = len(compact_json(body).encode()) * 2 + 4096
        amount = self.prices.cost(upper_input, body["max_tokens"])
        if self.charged + amount > self.limit:
            raise BudgetExhausted("Insufficient budget for worst-case next call")
        self.reservations[call_id] = amount
        return amount

    def settle(self, call_id: str, details: dict[str, Any] | None) -> Decimal:
        reserved = self.reservations.pop(call_id)
        self.settled.add(call_id)
        charge = reserved
        if details is not None and not details["errors"]:
            tokens = details["tokens"]
            if tokens["input"] is not None and tokens["output"] is not None:
                charge = self.prices.cost(
                    tokens["input"], tokens["output"], tokens["cached_input"] or 0
                )
        self.charged += charge
        if charge > reserved:
            raise BudgetExhausted("Observed usage exceeds reserved upper bound; stop")
        return charge


def summarize_calls(records: list[dict[str, Any]]) -> dict[str, Any]:
    details = [r["usage_details"] for r in records if r.get("usage_details")]
    measured = [d for d in details if d["usage_available"] and not d["errors"]]
    cache_measured = [d for d in measured if d["tokens"]["cached_input"] is not None]
    total_input = sum(d["tokens"]["input"] for d in measured)
    cache_denominator = sum(d["tokens"]["input"] for d in cache_measured)
    cached = sum(d["tokens"]["cached_input"] for d in cache_measured)
    costs = [
        Decimal(r["estimated_cost_cny"]) for r in records if r.get("estimated_cost_cny") is not None
    ]
    return {
        "calls": len(records),
        "measured_calls": len(measured),
        "usage_coverage": len(measured) / len(records) if records else None,
        "cache_usage_coverage": len(cache_measured) / len(records) if records else None,
        "input_tokens": total_input,
        "output_tokens": sum(d["tokens"]["output"] for d in measured),
        "cached_input_tokens": cached if cache_measured else None,
        "uncached_input_tokens": cache_denominator - cached if cache_measured else None,
        "cache_hit_rate": cached / cache_denominator if cache_denominator else None,
        "cache_rate_input_denominator": cache_denominator,
        "estimated_cost_cny": str(sum(costs, Decimal(0))) if costs else None,
        "cost_coverage": len(costs) / len(records) if records else None,
        "latency_ms": sum(r.get("latency_ms", 0) for r in records),
        "transport_retries": sum(r.get("attempt", 1) > 1 for r in records),
        "revision_calls": sum(r.get("stage", r.get("role")) == "rewriter" for r in records),
    }


def cache_observations(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report first/repeat separately without a universal cache threshold."""
    return {
        "production_status": "not_evaluated",
        "arms": {
            arm: {
                observation: summarize_calls(
                    [r for r in records if r["arm"] == arm and r["observation"] == observation]
                )
                for observation in ("first_observed", "repeat")
            }
            for arm in ("baseline", "candidate")
        },
        "limitation": "Repeated identical requests do not establish cross-scene cache reuse",
    }
