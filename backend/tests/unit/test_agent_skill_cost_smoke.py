"""The engineering smoke is offline by default and freezes byte-identical arms."""

import json
from decimal import Decimal
from pathlib import Path

from casefile.benchmark.agent_skill_cost_smoke import smoke
from casefile.benchmark.prose_cost_budget import PriceSnapshot


def test_offline_smoke_plans_all_protocol_classes_without_network(tmp_path: Path) -> None:
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps(
            {
                "budget_charged_upper_cny": "1.85512344",
                "summary": {"estimated_cost_cny": "0.927561720"},
            }
        )
    )
    prices = PriceSnapshot(
        "deepseek-flash",
        "2026-09-16T00:00:00+00:00",
        "fixture",
        Decimal("0.04"),
        Decimal("2"),
        Decimal("8"),
    )
    output = tmp_path / "run"
    report = smoke(output, live=False, prices=prices, history_reports=[history], api_key="")
    assert report["status"] == "offline_ready"
    assert report["planned_calls"] == 28
    manifest = json.loads((output / "manifest.json").read_text())
    assert {case["protocol"] for case in manifest["cases"]} == {"json", "stream", "tool"}
    assert all(
        case["baseline_prefix_hash"] == case["candidate_prefix_hash"] for case in manifest["cases"]
    )
