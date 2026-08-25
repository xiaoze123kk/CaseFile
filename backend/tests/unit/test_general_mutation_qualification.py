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


def test_formal_qualification_writes_failed_index_when_stage_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    revision = "a" * 40
    manifest = {
        "source_revision": revision,
        "runtime_fingerprint": "b" * 64,
        "model_id": "deepseek-v4-pro",
    }
    monkeypatch.setattr(qualification, "qualification_preflight", lambda **_kwargs: manifest)
    monkeypatch.setattr(
        qualification,
        "_saved_credential",
        lambda **_kwargs: ("not-a-real-key", "deepseek-v4-pro"),
    )
    monkeypatch.setattr(
        qualification,
        "_git_identity",
        lambda _root: {"revision": revision, "branch": "codex/test", "dirty": False},
    )

    def fail_kernel() -> dict[str, object]:
        raise RuntimeError("sensitive transport detail must not be persisted")

    monkeypatch.setattr(qualification, "run_kernel_qualification", fail_kernel)

    result = qualification.run_formal_qualification(
        repo_root=Path.cwd(),
        holdout_suite_path=tmp_path / "holdout.json",
        output_dir=tmp_path / "evidence",
        database_url="postgresql+psycopg://u:p@localhost/casefile_test",
        credential_database_url="postgresql+psycopg://u:p@localhost/casefile",
    )

    assert result["qualified"] is False
    assert "qualification_s0_execution_failed" in result["blockers"]
    assert result["diagnostics"] == [
        {
            "path": "s0/execution-error.json",
            "canonical_sha256": result["diagnostics"][0]["canonical_sha256"],
            "reason_code": "qualification_s0_execution_failed",
            "error_type": "RuntimeError",
        }
    ]
    diagnostic = (tmp_path / "evidence/s0/execution-error.json").read_text(encoding="utf-8")
    assert "sensitive transport detail" not in diagnostic
