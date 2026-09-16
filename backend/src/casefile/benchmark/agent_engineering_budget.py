"""Durable serial budget observer for the shared physical-call audit boundary."""

from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from casefile.benchmark.prose_cost_budget import BudgetExhausted, PriceSnapshot


def historical_budget(reports: list[Path]) -> dict[str, Any]:
    """Import explicit, distinct run reports, never rounded conversational totals."""
    sources = []
    identities: set[Path] = set()
    charged = Decimal(0)
    estimated = Decimal(0)
    for path in reports:
        path = path.resolve(strict=True)
        if path in identities:
            raise ValueError("Duplicate historical budget report")
        identities.add(path)
        content = path.read_bytes()
        report = json.loads(content)
        amount = Decimal(report["budget_charged_upper_cny"])
        outstanding = sum(
            (Decimal(str(value)) for value in report.get("outstanding_reservations", {}).values()),
            Decimal(0),
        )
        summary = report.get("summary", report.get("usage"))
        estimate = Decimal(summary["estimated_cost_cny"])
        if any(not value.is_finite() or value < 0 for value in (amount, outstanding, estimate)):
            raise ValueError("Invalid historical cost")
        charged += amount + outstanding
        estimated += estimate
        sources.append({"path": str(path), "sha256": sha256(content).hexdigest()})
    return {
        "sources": sources,
        "charged_upper_cny": str(charged),
        "estimated_cost_cny": str(estimated),
    }


class EngineeringBudget:
    """One non-resumable run directory. Failures with unknown usage retain reserves."""

    def __init__(self, path: Path, prices: PriceSnapshot, history: dict[str, Any]) -> None:
        if path.exists():
            raise ValueError("Refusing to overwrite or automatically resume a budget ledger")
        self.path = path
        self.prices = prices
        self.charged = Decimal(history["charged_upper_cny"])
        if not self.charged.is_finite() or not Decimal(0) <= self.charged <= Decimal(20):
            raise ValueError("Invalid historical budget debit")
        self.reservation: tuple[str, Decimal] | None = None
        self.state: dict[str, Any] = {"history": history, "limit_cny": "20", "calls": []}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents two processes from starting the same ledger.
        with path.open("x", encoding="utf-8") as output:
            output.write("{}")
        self.persist()

    def persist(self) -> None:
        self.state["charged_upper_cny"] = str(self.charged)
        self.state["reservation"] = self.reservation
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        temporary.replace(self.path)

    def __call__(self, record: dict[str, Any]) -> None:
        if record["event"] == "started":
            if self.reservation is not None:
                raise ValueError("Engineering smoke requires serial physical requests")
            maximum = record["max_output_tokens"]
            if type(maximum) is not int or maximum <= 0:
                raise ValueError("Explicit positive maximum output required before live call")
            if record["request_model"] != self.prices.model_id:
                raise ValueError("Frozen budget model mismatch")
            amount = self.prices.cost(2 * record["request_bytes"] + 4096, maximum, 0)
            if self.charged + amount > Decimal(20):
                raise BudgetExhausted("Original 20 CNY budget exhausted")
            self.reservation = (record["attempt_id"], amount)
            self.state["calls"].append(record)
            self.persist()
            return
        if self.reservation is None or self.reservation[0] != record["attempt_id"]:
            raise ValueError("Physical attempt does not match budget reservation")
        _, reserved = self.reservation
        details = record["usage"]
        tokens = details["tokens"]
        amount = reserved
        if details["usage_available"] and not details["errors"]:
            amount = self.prices.cost(
                tokens["input"], tokens["output"], tokens["cached_input"] or 0
            )
        self.charged += amount
        self.reservation = None
        estimate = self.prices.estimate(details, record["started_at"], record["ended_at"])
        self.state["calls"].append(
            {
                **record,
                "charged_upper_cny": str(amount),
                "estimated_cost_cny": str(estimate) if estimate is not None else None,
            }
        )
        self.persist()
        if amount > reserved:
            raise BudgetExhausted("Usage exceeded reservation; stop without another request")
