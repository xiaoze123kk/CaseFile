from __future__ import annotations

import json

from casefile.benchmark.general_mutation_progress import TrialProgressCheckpoint


def test_trial_progress_checkpoint_is_atomic_and_non_resumable(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "progress.json"
    progress = TrialProgressCheckpoint(suite_id="suite-test", total_trials=2, path=path)

    progress.record({"trial_id": "task-a:1", "task_id": "task-a", "classification": "success"})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "running"
    assert payload["resume_policy"] == "restart_from_scratch"
    assert payload["completed_trials"] == 1
    assert payload["last_completed_trial_id"] == "task-a:1"
    assert not path.with_name(".progress.json.tmp").exists()
    stdout = capsys.readouterr().out
    assert '"completed_trials":1' in stdout
    assert '"total_trials":2' in stdout

    progress.record({"task_id": "task-b", "trial_index": 1, "classification": "safe_block"})
    progress.finalize(status="completed")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["completed_trial_ids"] == ["task-a:1", "task-b:1"]
    assert payload["classification_counts"] == {"safe_block": 1, "success": 1}
