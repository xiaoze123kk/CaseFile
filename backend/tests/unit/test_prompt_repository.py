"""Immutable, packaged System Prompt Repository contract tests."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from casefile.agent_runtime.prompt_repository import (
    SUPPORTED_AGENT_IDS,
    PromptRepository,
    PromptRepositoryError,
    packaged_prompt_repository,
    prompt_version_for_task,
    system_prompt_for_task,
    validate_prompt_repository,
)
from casefile_contracts import TaskType

EXPECTED_CURRENT_VERSIONS = {
    "brief_polish": "brief-polish-v2",
    "brief_anchor_extract": "brief-anchor-extract-v2",
    "brief_to_draft": "brief-to-draft-v3",
    "casefile_chat": "casefile-chat-v1",
}

# This immutable release inventory starts with hashes captured before migration.
EXPECTED_RELEASE_HASHES = {
    ("brief_polish", "brief-polish-v2"): (
        "08348b8cc32c3a5b35502b377e301f19cd586241a346b76320abfd2d598f6fe2"
    ),
    ("brief_anchor_extract", "brief-anchor-extract-v2"): (
        "b1610c82e54f74ee3a0c8ed5d65779bbfc33e3af942c409f3f63a0a9bf445816"
    ),
    ("brief_to_draft", "brief-to-draft-v3"): (
        "b299091db94567addd570c1402a57454cd1b4814d3ccdb3adcf6315b1d58eb6a"
    ),
    ("casefile_chat", "casefile-chat-v1"): (
        "adf7f60f993c703023a56b9cfda8cdaae00f262b93dffa0b81d9a1046f0e572f"
    ),
}


def test_packaged_registry_maps_every_agent_task_exactly_once() -> None:
    contract_task_types = {task_type.value for task_type in TaskType}

    assert set(SUPPORTED_AGENT_IDS) == contract_task_types
    assert packaged_prompt_repository().expected_agent_ids == SUPPORTED_AGENT_IDS
    assert {
        agent_id: prompt_version_for_task(agent_id)
        for agent_id in SUPPORTED_AGENT_IDS
    } == EXPECTED_CURRENT_VERSIONS


def test_packaged_prompt_versions_match_immutable_release_inventory() -> None:
    definitions = validate_prompt_repository()
    actual_hashes = {
        (definition.agent_id, definition.version): definition.system_prompt_sha256
        for definition in definitions
    }

    assert actual_hashes == EXPECTED_RELEASE_HASHES
    for definition in definitions:
        assert definition.system_prompt.endswith("\n")
        assert system_prompt_for_task(
            definition.agent_id,
            definition.version,
        ) == definition.system_prompt


def test_repository_loads_an_explicit_inactive_historical_version(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(
        tmp_path,
        versions={
            "brief-polish-v1": "Role: historical prompt.\n",
            "brief-polish-v2": "Role: current prompt.\n",
        },
    )
    _write_manifest(
        root,
        version="brief-polish-v2",
        system_prompt="Role: current prompt.\n",
        previous_version="brief-polish-v1",
    )

    definitions = repository.validate()

    assert [definition.version for definition in definitions] == [
        "brief-polish-v1",
        "brief-polish-v2",
    ]
    assert repository.load(
        "brief_polish",
        "brief-polish-v1",
    ).system_prompt == "Role: historical prompt.\n"
    assert repository.load("brief_polish").version == "brief-polish-v2"


def test_repository_rejects_unknown_agent_and_version(tmp_path: Path) -> None:
    repository, _root = _one_agent_repository(tmp_path)

    with pytest.raises(PromptRepositoryError, match="Unsupported Agent Prompt"):
        repository.load("unknown_agent")
    with pytest.raises(PromptRepositoryError, match="does not belong"):
        repository.load("brief_polish", "casefile-chat-v1")
    with pytest.raises(PromptRepositoryError, match="Unknown Prompt version"):
        repository.load("brief_polish", "brief-polish-v9")


def test_repository_rejects_a_missing_system_prompt(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "brief_polish" / "v2" / "system.md").unlink()

    with pytest.raises(PromptRepositoryError, match="System Prompt .* is missing"):
        repository.load("brief_polish")


def test_repository_rejects_an_empty_system_prompt(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "brief_polish" / "v2" / "system.md").write_text("", encoding="utf-8")

    with pytest.raises(PromptRepositoryError, match="must not be empty"):
        repository.load("brief_polish")


def test_repository_rejects_system_prompt_hash_drift(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "brief_polish" / "v2" / "system.md").write_text(
        "Role: silently modified prompt.\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(PromptRepositoryError, match="System Prompt hash mismatch"):
        repository.load("brief_polish")


def test_repository_rejects_non_utf8_and_crlf_prompts(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    system_path = root / "brief_polish" / "v2" / "system.md"
    system_path.write_bytes(b"\xff\xfe")
    with pytest.raises(PromptRepositoryError, match="must be UTF-8"):
        repository.load("brief_polish")

    system_path.write_bytes(b"Role: CRLF prompt.\r\n")
    with pytest.raises(PromptRepositoryError, match="must use LF"):
        repository.load("brief_polish")


def test_repository_rejects_manifest_and_registry_drift(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    manifest_path = root / "brief_polish" / "v2" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["agent_id"] = "casefile_chat"
    _write_json(manifest_path, manifest)
    with pytest.raises(PromptRepositoryError, match="agent_id does not match"):
        repository.load("brief_polish")

    repository, root = _one_agent_repository(tmp_path / "second")
    registry_path = root / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["agents"]["brief_polish"]["current_version"] = "brief-polish-v9"
    _write_json(registry_path, registry)
    with pytest.raises(PromptRepositoryError, match="Unknown Prompt version"):
        repository.load("brief_polish")
    with pytest.raises(PromptRepositoryError, match="Current Prompt version is not present"):
        repository.validate()


def test_repository_rejects_unregistered_resources(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "unexpected.txt").write_text("not part of the contract", encoding="utf-8")
    with pytest.raises(PromptRepositoryError, match="Unexpected resource.*root"):
        repository.validate()

    repository, root = _one_agent_repository(tmp_path / "second")
    (root / "brief_polish" / "v2" / "notes.md").write_text(
        "not part of this immutable version",
        encoding="utf-8",
    )
    with pytest.raises(PromptRepositoryError, match="version resources do not match"):
        repository.validate()


def _one_agent_repository(
    tmp_path: Path,
    *,
    versions: dict[str, str] | None = None,
) -> tuple[PromptRepository, Path]:
    root = tmp_path / "prompts"
    root.mkdir(parents=True)
    _write_json(
        root / "registry.json",
        {
            "schema_version": 1,
            "agents": {
                "brief_polish": {
                    "current_version": "brief-polish-v2",
                }
            },
        },
    )
    resolved_versions = versions or {"brief-polish-v2": "Role: test prompt.\n"}
    for version, system_prompt in resolved_versions.items():
        _write_manifest(root, version=version, system_prompt=system_prompt)
    return PromptRepository(root, expected_agent_ids=("brief_polish",)), root


def _write_manifest(
    root: Path,
    *,
    version: str,
    system_prompt: str,
    previous_version: str | None = None,
) -> None:
    version_directory = version.rsplit("-", 1)[-1]
    version_root = root / "brief_polish" / version_directory
    version_root.mkdir(parents=True, exist_ok=True)
    (version_root / "system.md").write_text(system_prompt, encoding="utf-8", newline="\n")
    _write_json(
        version_root / "manifest.json",
        {
            "agent_id": "brief_polish",
            "version": version,
            "system_prompt_file": "system.md",
            "system_prompt_sha256": sha256(system_prompt.encode("utf-8")).hexdigest(),
            "previous_version": previous_version,
            "change_summary": "Test Prompt version.",
        },
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
