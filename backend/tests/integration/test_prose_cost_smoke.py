"""Actual fixture/build/provider normalization path, with no provider network."""

from decimal import Decimal
from pathlib import Path

import pytest

from casefile.agent_runtime.prose_writer import DeepSeekProseWriterProvider
from casefile.benchmark.prose_cost_budget import PriceSnapshot
from casefile.benchmark.prose_cost_smoke import run_smoke


def test_offline_smoke_completes_all_pairs(tmp_path: Path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Network called from offline smoke")

    monkeypatch.setattr(DeepSeekProseWriterProvider, "_create_completion", forbidden)
    report = run_smoke(tmp_path / "run")
    assert report["status"] == "completed"
    assert len(report["rows"]) == 12
    assert all(r["validation_error"] is None for r in report["rows"])
    assert report["summary"]["cache_hit_rate"] is None
    assert report["budget_charged_upper_cny"] == "0"
    assert not report["qualification"]
    with pytest.raises(FileExistsError):
        run_smoke(tmp_path / "run")


def test_budget_stops_before_first_network_call(tmp_path: Path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Budget failed to prevent network")

    monkeypatch.setattr(DeepSeekProseWriterProvider, "_create_completion", forbidden)
    from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID

    prices = PriceSnapshot(
        DEEPSEEK_MODEL_ID, "2026-09-15", "official", Decimal("0.04"), Decimal("2"), Decimal("8")
    )
    report = run_smoke(
        tmp_path / "run",
        mode="live",
        prices=prices,
        budget_cny=Decimal("0.00001"),
        api_key="canary",
    )
    assert report["status"] == "budget_stopped"
    assert report["completed_observations"] == 0
    assert "canary" not in (tmp_path / "run" / "manifest.json").read_text(encoding="utf-8")


def test_failed_attempts_are_reserved_and_retry_is_bounded(tmp_path: Path, monkeypatch):
    import json

    from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
    from casefile.agent_runtime.prose_rewriter import DeepSeekProseRewriterProvider

    output = tmp_path / "failed"
    calls = 0

    def timeout(*args):
        nonlocal calls
        calls += 1
        saved = json.loads((output / "report.json").read_text(encoding="utf-8"))
        assert saved["outstanding_reservations"]
        assert saved["rows"][-1]["status"] == "pending"
        raise TimeoutError("transport timeout")

    monkeypatch.setattr(DeepSeekProseWriterProvider, "_create_completion", timeout)
    monkeypatch.setattr(DeepSeekProseRewriterProvider, "_create_completion", timeout)
    prices = PriceSnapshot(
        DEEPSEEK_MODEL_ID, "2026-09-15", "official", Decimal("0.04"), Decimal("2"), Decimal("8")
    )
    report = run_smoke(output, mode="live", prices=prices, api_key="canary")
    assert calls == 24  # 12 observations, at most one retry apiece
    assert report["status"] == "incomplete"
    assert report["summary"]["transport_retries"] == 12
    assert Decimal(report["budget_charged_upper_cny"]) > 0
    assert report["summary"]["estimated_cost_cny"] is None
    assert not report["outstanding_reservations"]
