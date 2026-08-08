"""Release acceptance gate must require every requested run to succeed."""

from __future__ import annotations

from tests.integration.test_brief_to_draft_v8_live_acceptance import _report_status


def _report(
    *,
    attempted: int,
    successful: int,
    failed: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "status": "running",
        "runs_attempted": attempted,
        "successful_runs": successful,
        "failed_runs": failed or [],
        "invariant_violations": [],
    }


def test_thirty_run_release_gate_requires_thirty_successes() -> None:
    assert (
        _report_status(_report(attempted=30, successful=29), expected_runs=30)
        == "failed"
    )
    assert (
        _report_status(_report(attempted=30, successful=30), expected_runs=30)
        == "passed"
    )


def test_incomplete_acceptance_batch_is_blocked() -> None:
    assert (
        _report_status(_report(attempted=29, successful=29), expected_runs=30)
        == "blocked"
    )
