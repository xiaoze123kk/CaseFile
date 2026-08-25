from __future__ import annotations

import json
from pathlib import Path

import pytest
from casefile.benchmark.general_mutation_holdout import (
    HoldoutContractError,
    load_holdout_suite,
)

ROOT = Path(__file__).resolve().parents[3]
PRIVATE_SUITE = ROOT / "backend/var/benchmark/private/general-mutation-holdout-v1/suite.json"


def test_private_holdout_loads_24_unique_tasks() -> None:
    suite = load_holdout_suite(PRIVATE_SUITE)
    assert len(suite.tasks) == 24
    assert len({task.task_id for task in suite.tasks}) == 24
    assert suite.suite_role == "holdout"
    assert suite.metadata["oracle_fingerprint"] != suite.metadata["reference_fingerprint"]


def test_private_holdout_oracle_is_not_in_provider_input() -> None:
    suite = load_holdout_suite(PRIVATE_SUITE)
    for task in suite.tasks:
        input_text = json.dumps(task.input, ensure_ascii=False).lower()
        assert "oracle" not in input_text
        assert "reference" not in input_text
        assert Path(task.reference_path).is_file()


def test_private_holdout_missing_package_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(HoldoutContractError, match="json_invalid"):
        load_holdout_suite(tmp_path / "suite.json")
