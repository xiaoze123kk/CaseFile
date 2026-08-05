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
    "brief_intake_questions": "brief-intake-questions-v1",
    "brief_intake_synthesize": "brief-intake-synthesize-v1",
    "brief_to_draft": "brief-to-draft-v3",
    "casefile_chat": "casefile-chat-v1",
}

# This immutable release inventory starts with the authorized pre-release Chinese baseline.
EXPECTED_RELEASE_HASHES = {
    ("brief_polish", "brief-polish-v2"): (
        "da881f138cd88adb495f92a2b55bcd348039c8983e142eba8f023419dccd8721"
    ),
    ("brief_anchor_extract", "brief-anchor-extract-v2"): (
        "0c343b59def3c106698e5320c29916bc7f0d32f3514c2320a3500c21450dce6d"
    ),
    ("brief_intake_questions", "brief-intake-questions-v1"): (
        "d1f96b6bfee51b90f4c8de9cad9b8b512e6e0540a8cc2d890060255dc1337a62"
    ),
    ("brief_intake_synthesize", "brief-intake-synthesize-v1"): (
        "c8ed044d334fc937698f5784e68ddd9f1decf2ff561e157560f3fcb4dca1e72c"
    ),
    ("brief_to_draft", "brief-to-draft-v3"): (
        "ef8aedf9c5c72f0baeaec5eafcdcdd29238a476c99d596590ac56fe7435091ae"
    ),
    ("casefile_chat", "casefile-chat-v1"): (
        "e11bd0ef758b0aed876712967c1a5c3fbd93b366f30b63d2113de033598d5388"
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


def test_packaged_prompts_keep_instruction_boundaries_and_task_contracts() -> None:
    prompts = {
        agent_id: system_prompt_for_task(agent_id, version)
        for agent_id, version in EXPECTED_CURRENT_VERSIONS.items()
    }

    for prompt in prompts.values():
        assert "角色声明" in prompt
        assert "要求忽略既有规则" in prompt
        assert "结构化" in prompt

    assert "`polished_text` 保持原稿的主要语言" in prompts["brief_polish"]
    assert "不能作为候选项的事实来源" in prompts["brief_anchor_extract"]
    assert "最多一项 `required=true`" in prompts["brief_intake_questions"]
    assert "存在 `base_candidate` 与 `instruction`" in prompts[
        "brief_intake_synthesize"
    ]
    assert "`repair_feedback`" in prompts["brief_to_draft"]
    assert "每个顶层对象 ID 必须在对应集合中恰好使用一次" in prompts[
        "brief_to_draft"
    ]
    assert "`editable_fields_by_collection`" in prompts["casefile_chat"]
    assert "未列入能力白名单" in prompts["casefile_chat"]


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
