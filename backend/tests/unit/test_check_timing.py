"""Timing reports must remain useful when checks or test setup fail."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("failure", [False, True])
def test_pytest_report_preserves_phase_outcomes(tmp_path: Path, failure: bool) -> None:
    shutil.copyfile(ROOT / "backend/tests/conftest.py", tmp_path / "conftest.py")
    source = """import pytest

def test_pass():
    assert True

@pytest.mark.skip(reason="example")
def test_skip():
    pass
"""
    if failure:
        source += """
@pytest.fixture
def broken():
    raise RuntimeError("setup failure")

def test_setup_failure(broken):
    pass

def test_call_failure():
    assert False
"""
    (tmp_path / "test_sample.py").write_text(source, encoding="utf-8")
    destination = tmp_path / "reports with spaces" / "pytest.json"
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--timing-report", str(destination)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == (1 if failure else 0), result.stdout + result.stderr
    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["exit_code"] == result.returncode
    assert report["collected"] == (4 if failure else 2)
    assert set(report["phase_seconds"]) == {"setup", "call", "teardown"}
    for phase, seconds in report["phase_seconds"].items():
        assert seconds == pytest.approx(sum(
            row["seconds"] for row in report["rows"] if row["phase"] == phase
        ))
    assert [row["seconds"] for row in report["rows"]] == sorted(
        (row["seconds"] for row in report["rows"]), reverse=True,
    )
    assert any(row["outcome"] == "skipped" for row in report["rows"])
    if failure:
        failed = {row["phase"] for row in report["rows"] if row["outcome"] == "failed"}
        assert failed == {"setup", "call"}
        assert not any(
            "test_setup_failure" in row["nodeid"] and row["phase"] == "call"
            for row in report["rows"]
        )
    assert "pytest phase totals" in result.stdout


def test_check_script_keeps_failure_and_writes_partial_timings(tmp_path: Path) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / "scripts/check.ps1", scripts / "check.ps1")
    (scripts / "check-migration-names.ps1").write_text(
        'throw "expected-static-failure"', encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts / "check.ps1"), "-SkipPostgres"],
        cwd=tmp_path, capture_output=True, check=False,
    )
    assert result.returncode != 0
    reports = list((tmp_path / "var/checks").glob("*/summary.json"))
    assert len(reports) == 1, result.stdout + result.stderr
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["skip_postgres"] is True
    assert len(report["stages"]) == 1
    assert report["stages"][0]["name"] == "static"
    assert report["stages"][0]["status"] == "failed"
    assert report["elapsed_seconds"] >= report["stages"][0]["seconds"] >= 0
