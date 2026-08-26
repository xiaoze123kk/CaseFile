"""Shared value objects for foundation migration tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lineage:
    owner_id: int
    project_id: int
    casefile_id: int
    draft_id: int


@dataclass(frozen=True)
class MigrationCompatibilityIds:
    project_id: int
    brief_id: int
    brief_version_id: int
    task_run_id: int
    task_attempt_id: int
    snapshot_id: int
    canon_id: int
    legacy_casefile: dict[str, object]
    legacy_brief: dict[str, object]
