"""Opt-in per-phase timing reports for the repository quality gate."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--timing-report", metavar="PATH", help="Write setup/call/teardown timings as JSON",
    )


def pytest_configure(config: pytest.Config) -> None:
    destination = config.getoption("timing_report")
    if destination:
        config.pluginmanager.register(CheckTimingReporter(Path(destination)), "check-timing")


class CheckTimingReporter:
    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self.started = perf_counter()
        self.rows: list[dict[str, Any]] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.rows.append({
            "nodeid": report.nodeid,
            "phase": report.when,
            "seconds": report.duration,
            "outcome": report.outcome,
        })

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        totals = {
            phase: sum(row["seconds"] for row in self.rows if row["phase"] == phase)
            for phase in ("setup", "call", "teardown")
        }
        report = {
            "schema_version": 1,
            "exit_code": int(exitstatus),
            "collected": session.testscollected,
            "elapsed_seconds": perf_counter() - self.started,
            "phase_seconds": totals,
            "rows": sorted(self.rows, key=lambda row: row["seconds"], reverse=True),
        }
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        terminalreporter.write_sep("-", "pytest phase totals (seconds)")
        for phase in ("setup", "call", "teardown"):
            seconds = sum(row["seconds"] for row in self.rows if row["phase"] == phase)
            terminalreporter.write_line(f"{phase}: {seconds:.3f}")
        terminalreporter.write_line(f"Timing report: {self.destination}")
