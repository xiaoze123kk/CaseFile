from pathlib import Path

import pytest
from casefile.benchmark import general_mutation_qualification as qualification
from casefile.benchmark.general_mutation_qualification import QualificationError


def test_preflight_rejects_dirty_source_before_external_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qualification,
        "_git_identity",
        lambda _root: {"revision": "a" * 40, "branch": "codex/test", "dirty": True},
    )
    with pytest.raises(QualificationError, match="qualification_git_must_be_clean"):
        qualification.qualification_preflight(
            repo_root=Path.cwd(),
            holdout_suite_path=Path("missing.json"),
            database_url="postgresql+psycopg://u:p@localhost/casefile_test",
            credential_database_url="postgresql+psycopg://u:p@localhost/casefile",
        )


def test_database_name_requires_parseable_database() -> None:
    assert (
        qualification._database_name(
            "postgresql+psycopg://u:p@localhost/casefile_m34_qualification_test"
        )
        == "casefile_m34_qualification_test"
    )
    with pytest.raises(QualificationError, match="database_url_invalid"):
        qualification._database_name("not a database url")
