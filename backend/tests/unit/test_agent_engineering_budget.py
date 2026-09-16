"""Budget persistence, historical accounting and stop-before-send behavior."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from casefile.agent_runtime.usage import normalize_usage
from casefile.benchmark.agent_engineering_budget import EngineeringBudget, historical_budget
from casefile.benchmark.prose_cost_budget import BudgetExhausted, PriceSnapshot


def prices() -> PriceSnapshot:
    return PriceSnapshot(
        "model", "2026-09-16T00:00:00+00:00", "fixture", Decimal("0.04"), Decimal(2), Decimal(8)
    )


def test_historical_reports_are_exact_and_distinct(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "budget_charged_upper_cny": "1.85512344",
                "summary": {"estimated_cost_cny": "0.92756172"},
            }
        )
    )
    result = historical_budget([report])
    assert result["charged_upper_cny"] == "1.85512344"
    assert result["estimated_cost_cny"] == "0.92756172"
    with pytest.raises(ValueError, match="Duplicate"):
        historical_budget([report, report])


def test_unknown_attempt_keeps_charge_and_ledger_cannot_restart(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    history = {"charged_upper_cny": "1.85512344"}
    budget = EngineeringBudget(path, prices(), history)
    start = {
        "event": "started",
        "attempt_id": "a",
        "max_output_tokens": 100,
        "request_bytes": 100,
        "request_model": "model",
    }
    budget(start)
    saved = json.loads(path.read_text())
    assert saved["reservation"][0] == "a"
    reserved = Decimal(saved["reservation"][1])
    with pytest.raises(ValueError, match="serial"):
        budget({**start, "attempt_id": "b"})
    budget(
        {
            "event": "finished",
            "attempt_id": "a",
            "usage": normalize_usage(None),
            "started_at": "2026-09-16T00:00:00+00:00",
            "ended_at": "2026-09-16T00:00:01+00:00",
        }
    )
    assert budget.charged == Decimal(history["charged_upper_cny"]) + reserved
    assert json.loads(path.read_text())["reservation"] is None
    with pytest.raises(ValueError, match="overwrite"):
        EngineeringBudget(path, prices(), history)


def test_budget_stops_without_new_reservation(tmp_path: Path) -> None:
    budget = EngineeringBudget(tmp_path / "ledger.json", prices(), {"charged_upper_cny": "20"})
    with pytest.raises(BudgetExhausted):
        budget(
            {
                "event": "started",
                "attempt_id": "a",
                "max_output_tokens": 100,
                "request_bytes": 100,
                "request_model": "model",
            }
        )
    assert budget.reservation is None
    assert budget.state["calls"] == []
