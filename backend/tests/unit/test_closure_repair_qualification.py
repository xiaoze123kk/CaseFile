from __future__ import annotations

from pathlib import Path

import pytest
from casefile.benchmark import closure_repair_qualification as qualification


def test_qualification_preflight_rejects_dirty_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        qualification,
        "_git_identity",
        lambda _root: {"revision": "a" * 40, "branch": "codex/test", "dirty": True},
    )

    with pytest.raises(
        qualification.QualificationError,
        match="qualification_git_must_be_clean",
    ):
        qualification.qualification_preflight(
            repo_root=tmp_path,
            holdout_suite_path=tmp_path / "suite.json",
            database_url="postgresql+psycopg://localhost/casefile_test",
        )


def test_qualification_preflight_rejects_non_test_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        qualification,
        "_git_identity",
        lambda _root: {"revision": "a" * 40, "branch": "codex/test", "dirty": False},
    )

    with pytest.raises(
        qualification.QualificationError,
        match="qualification_database_must_end_test",
    ):
        qualification.qualification_preflight(
            repo_root=tmp_path,
            holdout_suite_path=tmp_path / "suite.json",
            database_url="postgresql+psycopg://localhost/casefile",
        )
