"""Crash-visible progress checkpoints for General Mutation benchmarks."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class TrialProgressCheckpoint:
    """Emit progress and atomically persist completed trial identities.

    Checkpoints are diagnostic only. They intentionally declare a
    restart-from-scratch policy and are never accepted as resume input, so a
    failed capability result cannot be selectively rerun.
    """

    def __init__(self, *, suite_id: str, total_trials: int, path: Path | None) -> None:
        self.suite_id = suite_id
        self.total_trials = total_trials
        self.path = path
        self.completed_trial_ids: list[str] = []
        self.classifications: Counter[str] = Counter()

    def record(self, row: Mapping[str, Any]) -> None:
        trial_id = _trial_id(row)
        classification = str(row.get("classification", "unknown"))
        self.completed_trial_ids.append(trial_id)
        self.classifications[classification] += 1
        completed = len(self.completed_trial_ids)
        event = {
            "event": "benchmark.trial_completed",
            "suite_id": self.suite_id,
            "trial_id": trial_id,
            "completed_trials": completed,
            "total_trials": self.total_trials,
            "classification": classification,
        }
        print(json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)
        self._write(status="running")

    def finalize(self, *, status: str) -> None:
        self._write(status=status)

    def _write(self, *, status: str) -> None:
        if self.path is None:
            return
        payload = {
            "schema_version": "casefile-benchmark-trial-progress-v1",
            "suite_id": self.suite_id,
            "status": status,
            "resume_policy": "restart_from_scratch",
            "completed_trials": len(self.completed_trial_ids),
            "total_trials": self.total_trials,
            "last_completed_trial_id": (
                self.completed_trial_ids[-1] if self.completed_trial_ids else None
            ),
            "completed_trial_ids": self.completed_trial_ids,
            "classification_counts": dict(sorted(self.classifications.items())),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.path)


def default_checkpoint_path(report_path: Path | None) -> Path | None:
    if report_path is None:
        return None
    return report_path.with_name(f"{report_path.stem}.checkpoint.json")


def _trial_id(row: Mapping[str, Any]) -> str:
    explicit = row.get("trial_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    return f"{row.get('task_id', 'unknown')}:{row.get('trial_index', 'unknown')}"


__all__ = ["TrialProgressCheckpoint", "default_checkpoint_path"]
