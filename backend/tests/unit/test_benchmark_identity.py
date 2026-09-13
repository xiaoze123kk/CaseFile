"""Stable Git failure policies and existing database fingerprint bytes."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from casefile.benchmark import (
    chat_public_language_qualification,
    closure_repair_qualification,
    general_mutation_backend_release,
    general_mutation_qualification,
    source_identity,
)
from casefile.benchmark.database_fingerprint import public_schema_fingerprint


def test_git_identity_keeps_command_order_and_report_fields(monkeypatch, tmp_path: Path) -> None:
    commands = []
    responses = iter(("abc\n", "\n", "?? untracked.txt\n"))

    def run(args, **kwargs):
        commands.append(args)
        assert kwargs == {"cwd": tmp_path, "capture_output": True, "text": True, "check": False}
        return SimpleNamespace(returncode=0, stdout=next(responses))

    monkeypatch.setattr(source_identity.subprocess, "run", run)
    assert source_identity.read_git_identity(tmp_path, strict=True) == {
        "revision": "abc", "branch": "", "dirty": True,
    }
    assert commands == [
        ["git", "rev-parse", "HEAD"], ["git", "branch", "--show-current"],
        ["git", "status", "--porcelain"],
    ]


@pytest.mark.parametrize("failed_index", [0, 1, 2])
def test_strict_git_stops_at_first_failed_command(monkeypatch, failed_index: int) -> None:
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if len(calls) - 1 == failed_index else 0, stdout="")

    monkeypatch.setattr(source_identity.subprocess, "run", run)
    with pytest.raises(source_identity.GitIdentityUnavailable):
        source_identity.read_git_identity(Path.cwd(), strict=True)
    assert len(calls) == failed_index + 1


def test_diagnostic_git_keeps_best_effort_behavior(monkeypatch) -> None:
    monkeypatch.setattr(
        source_identity.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    assert source_identity.read_git_identity(Path.cwd()) == {
        "revision": "", "branch": "", "dirty": False,
    }


@pytest.mark.parametrize("module,error_type,code", [
    (closure_repair_qualification, closure_repair_qualification.QualificationError,
     "qualification_git_identity_unavailable"),
    (general_mutation_qualification, general_mutation_qualification.QualificationError,
     "qualification_git_identity_unavailable"),
    (general_mutation_backend_release, general_mutation_backend_release.BackendReleaseContractError,
     "backend_release_git_identity_unavailable"),
])
def test_qualification_preserves_its_public_error(monkeypatch, module, error_type, code) -> None:
    monkeypatch.setattr(
        source_identity.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=128, stdout=""),
    )
    with pytest.raises(error_type, match=code):
        module._git_identity(Path.cwd())


def test_public_language_qualification_preserves_git_error(monkeypatch) -> None:
    monkeypatch.setattr(
        source_identity.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=128, stdout=""),
    )
    with pytest.raises(
        chat_public_language_qualification.PublicLanguageQualificationError,
        match="qualification_git_identity_unavailable",
    ):
        chat_public_language_qualification.git_identity(Path.cwd())


def test_schema_fingerprint_preserves_ordered_ascii_json_and_connection_lifetime() -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.all.return_value = [
        ("a", "id", "bigint"), ("a", "名称", "text"),
    ]
    frozen_bytes = b'[["a","id","bigint"],["a","\\u540d\\u79f0","text"]]'
    assert public_schema_fingerprint(engine) == hashlib.sha256(frozen_bytes).hexdigest()
    query = str(connection.execute.call_args.args[0])
    assert "table_schema='public'" in query
    assert "ORDER BY table_name,ordinal_position" in query
    engine.connect.return_value.__exit__.assert_called_once()
