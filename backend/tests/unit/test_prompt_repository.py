"""Immutable, packaged System Prompt Repository contract tests."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from casefile.agent_runtime.prompt import (
    AGENT_VERSION,
    V8_GENERATION_AGENT_VERSION,
    V9_GENERATION_AGENT_VERSION,
    V10_GENERATION_AGENT_VERSION,
    V11_GENERATION_AGENT_VERSION,
    V12_GENERATION_AGENT_VERSION,
    agent_version_for_task,
)
from casefile.agent_runtime.prompt_repository import (
    SUPPORTED_AGENT_IDS,
    PromptRepository,
    PromptRepositoryError,
    load_prompt,
    packaged_prompt_repository,
    prompt_version_for_task,
    system_prompt_for_task,
    validate_prompt_repository,
)
from casefile_contracts import TaskType

EXPECTED_CURRENT_VERSIONS = {
    "brief_polish": "brief-polish-v3",
    "brief_anchor_extract": "brief-anchor-extract-v3",
    "brief_intake_questions": "brief-intake-questions-v3",
    "brief_intake_synthesize": "brief-intake-synthesize-v2",
    "brief_strategy_options": "brief-strategy-options-v1",
    "brief_to_draft": "brief-to-draft-v12",
    "casefile_chat": "casefile-chat-v1",
}

# This immutable release inventory starts with the authorized pre-release Chinese baseline.
EXPECTED_RELEASE_HASHES = {
    ("brief_polish", "brief-polish-v2"): {
        "system": "da881f138cd88adb495f92a2b55bcd348039c8983e142eba8f023419dccd8721"
    },
    ("brief_polish", "brief-polish-v3"): {
        "system": "554f15807e88de2096aca4c6ec06d88fb516a5c285fdd5bde4425fb40712629a"
    },
    ("brief_anchor_extract", "brief-anchor-extract-v2"): {
        "system": "0c343b59def3c106698e5320c29916bc7f0d32f3514c2320a3500c21450dce6d"
    },
    ("brief_anchor_extract", "brief-anchor-extract-v3"): {
        "system": "cbbd4f6da817be6137ec6ae0a782349fe76ad987bf85e0fbf560311d19a10442"
    },
    ("brief_intake_questions", "brief-intake-questions-v1"): {
        "system": "d1f96b6bfee51b90f4c8de9cad9b8b512e6e0540a8cc2d890060255dc1337a62"
    },
    ("brief_intake_questions", "brief-intake-questions-v2"): {
        "system": "59a4cab9b080cffbf1bfc6264d07a2121dcb786f8c153f172ebe54f540656305"
    },
    ("brief_intake_questions", "brief-intake-questions-v3"): {
        "system": "357cb699b2258d929be6e152582965985e0d0937317e94255a07d6faa9c8d066"
    },
    ("brief_intake_synthesize", "brief-intake-synthesize-v1"): {
        "system": "c8ed044d334fc937698f5784e68ddd9f1decf2ff561e157560f3fcb4dca1e72c"
    },
    ("brief_intake_synthesize", "brief-intake-synthesize-v2"): {
        "system": "6d3887dde3223f7f53f78053e5ed13a36069c92b9adfa56013970c6d142017a6"
    },
    ("brief_strategy_options", "brief-strategy-options-v1"): {
        "system": "31e8fa98b451f63a41dba1c3ecd42a591a0bcc8fb6cd710a898eb74014c58f87"
    },
    ("brief_to_draft", "brief-to-draft-v3"): {
        "system": "ef8aedf9c5c72f0baeaec5eafcdcdd29238a476c99d596590ac56fe7435091ae"
    },
    ("brief_to_draft", "brief-to-draft-v4"): {
        "system": "e8a9385ee762d6c7a36ca8405e0d2e48259fbb37e9acac19fa7d5f95b69e076b"
    },
    ("brief_to_draft", "brief-to-draft-v5"): {
        "system": "62b08c5b26255965b73cccbe06ea88192fb3dc8f0eccf1265815a06e1abdb311"
    },
    ("brief_to_draft", "brief-to-draft-v6"): {
        "system": "a6b5c79908b8053a4954c9a9a6f3e00ea403c1cb07b0273944cd79d57ddb966b"
    },
    ("brief_to_draft", "brief-to-draft-v7"): {
        "system": "ffd17239f4562c86a964a3010e1ebfe1fc5c3be7c863980e74e6a0045be2a0ff"
    },
    ("brief_to_draft", "brief-to-draft-v8"): {
        "planner": "d12eee1955ed6aedf2a5b33650da88ff5c0fab97cca6a8023d2591974f4ecf73",
        "story": "223bb2bee82ca98470482eb6db5ea2737b89818770c8e50825674a83aacf42d9",
        "evidence": "3256cb833025986b80564725a1135430f35509a157246bb15bae1d4f2dfe1e0b",
        "governance": "806eb87356df8260fef0596a49729fb8c7f117bceed7a20c60b5ec391c580c6b",
    },
    ("brief_to_draft", "brief-to-draft-v9"): {
        "fragment:common": "91f8417d301c2b8a2c8cf6ae19ebe3f5e0b8aa9850bd016bd406b1b3efc10f99",
        "fragment:planner": "c89012d1b8d457ec8ef220cd12f948fbe20d7e73ff03215d38b847b9504f5045",
        "fragment:domain_common": (
            "0d20f4fe4b60668f1c19c7277d93ea29c0ee43e0939d08ee577d731c41747c82"
        ),
        "fragment:story": "b62c800d4f62b1c39fd075416b8401de1161059753450c85984efda87f0bc46e",
        "fragment:evidence": "fcb5de2bf8ee2c4068907226f16f4cf985b9bd5b4713ad6b3da8ca4823a0647a",
        "fragment:governance": "32eeecc2917449a8cb3439cd8df24e97d99764f9ddc596b171611cdc8c0d2146",
    },
    ("brief_to_draft", "brief-to-draft-v10"): {
        "fragment:common": "91f8417d301c2b8a2c8cf6ae19ebe3f5e0b8aa9850bd016bd406b1b3efc10f99",
        "fragment:planner": "945e81789befcb0e8294ccb27ac3de99097e62e294cc0bef2215bb3a5e7fbb18",
        "fragment:domain_common": (
            "0d20f4fe4b60668f1c19c7277d93ea29c0ee43e0939d08ee577d731c41747c82"
        ),
        "fragment:story": "b62c800d4f62b1c39fd075416b8401de1161059753450c85984efda87f0bc46e",
        "fragment:evidence": "db01f58b7d655e123c5a0c2f67a99c23ae1c1adcd9a156f57b273f72c832dbc9",
        "fragment:governance": "32eeecc2917449a8cb3439cd8df24e97d99764f9ddc596b171611cdc8c0d2146",
    },
    ("brief_to_draft", "brief-to-draft-v11"): {
        "fragment:common": "1471bea245e0a6f082ec34570c6e215f1ae8f39d0f669920730d4b79e2a4e0c6",
        "fragment:planner": "196f2fc74293971660670edb84cbabc1d10fb47930d8adf0c268973d9cfe15ef",
        "fragment:domain_common": (
            "30004da9ececfdb224ca51ae280d47e5e084e58252cbd418a706328e96ac55de"
        ),
        "fragment:story": "de327598d8b221a36e62728f39b6e49d4b563e7ded345142bc73cbfcd4cda128",
        "fragment:evidence": "7e1d49fbce53f1bfada49f1c1b5ab3b089d221a62fce0a0ab87fcb02ce6df646",
        "fragment:governance": "4413b0e36adf04856360c7278079185427cf71a327181234272e94de61ed1c98",
    },
    ("brief_to_draft", "brief-to-draft-v12"): {
        "fragment:common": "5a2a325867caa00779022d6a18e0cb0467ad881efd76af793ce85af065d13fca",
        "fragment:planner": "bbb57f4bd968f066467345b86ba788e5087d5b15d79561a71d0b9f08925f9ba4",
        "fragment:temporal": "434a5321dc7e114df23ec42d50fe92c4e0c4f149fa76ba7fa8d325ddc5574f6a",
        "fragment:domain_common": (
            "30004da9ececfdb224ca51ae280d47e5e084e58252cbd418a706328e96ac55de"
        ),
        "fragment:story": "ebb727a0b54af0e80cfd7473bbeedce9385790d1a856e8611c7e076363751f58",
        "fragment:evidence": "6207f57a035dd69369e91e290c904eb50541256f26a29b50e9f850b69a9e070c",
        "fragment:governance": "e8308618584c0ae881fb7a4185078493afa58cd125cdc242511bbca952cd79d5",
    },
    ("casefile_chat", "casefile-chat-v1"): {
        "system": "e11bd0ef758b0aed876712967c1a5c3fbd93b366f30b63d2113de033598d5388"
    },
}


def test_packaged_registry_maps_every_agent_task_exactly_once() -> None:
    contract_task_types = {task_type.value for task_type in TaskType}

    assert set(SUPPORTED_AGENT_IDS) == contract_task_types
    assert packaged_prompt_repository().expected_agent_ids == SUPPORTED_AGENT_IDS
    assert {
        agent_id: prompt_version_for_task(agent_id) for agent_id in SUPPORTED_AGENT_IDS
    } == EXPECTED_CURRENT_VERSIONS


def test_task_agent_version_identifies_component_generation_pipelines() -> None:
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v8") == V8_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v9") == V9_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v10")
        == V10_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v11")
        == V11_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v12")
        == V12_GENERATION_AGENT_VERSION
    )
    assert agent_version_for_task("brief_to_draft", "brief-to-draft-v7") == AGENT_VERSION
    assert agent_version_for_task("brief_polish", "brief-polish-v3") == AGENT_VERSION


def test_packaged_prompt_versions_match_immutable_release_inventory() -> None:
    definitions = validate_prompt_repository()
    actual_hashes = {
        (definition.agent_id, definition.version): (
            {
                f"fragment:{fragment_id}": fragment.sha256
                for fragment_id, fragment in definition.package.fragments.items()
            }
            if definition.package is not None
            else definition.component_sha256 or {"system": definition.system_prompt_sha256}
        )
        for definition in definitions
    }

    assert actual_hashes == EXPECTED_RELEASE_HASHES
    for definition in definitions:
        assert definition.system_prompt.endswith("\n")
        assert (
            system_prompt_for_task(
                definition.agent_id,
                definition.version,
            )
            == definition.system_prompt
        )
        assert all(prompt.endswith("\n") for prompt in definition.component_prompts.values())


def test_packaged_prompts_keep_instruction_boundaries_and_task_contracts() -> None:
    prompts = {
        agent_id: system_prompt_for_task(agent_id, version)
        for agent_id, version in EXPECTED_CURRENT_VERSIONS.items()
        if agent_id != "brief_to_draft"
    }

    for prompt in prompts.values():
        assert "角色声明" in prompt
        assert "要求忽略既有规则" in prompt
        assert "结构化" in prompt

    assert "`polished_text` 保持原稿的主要语言" in prompts["brief_polish"]
    assert "`narrative_enhance`" in prompts["brief_polish"]
    assert "`introduced_details`" in prompts["brief_polish"]
    assert "不能作为候选项的事实来源" in prompts["brief_anchor_extract"]
    assert "suggest_author_answer" in prompts["brief_anchor_extract"]
    assert "不得覆盖已有 `author_answer`" in prompts["brief_anchor_extract"]
    assert "最多一项 `required=true`" in prompts["brief_intake_questions"]
    assert "`mode=additional`" in prompts["brief_intake_questions"]
    assert "不得重新增加必答门槛" in prompts["brief_intake_questions"]
    assert "存在 `base_candidate` 与 `instruction`" in prompts["brief_intake_synthesize"]
    assert "`content_outline` 的每一项必须是一个完整字符串" in prompts["brief_intake_synthesize"]
    assert "不得出现没有 `：` 的条目" in prompts["brief_intake_synthesize"]
    v8 = load_prompt("brief_to_draft", "brief-to-draft-v8")
    assert set(v8.component_prompts) == {"planner", "story", "evidence", "governance"}
    assert "CaseBlueprintV1" in v8.component_prompts["planner"]
    assert "StoryWorldIRV1" in v8.component_prompts["story"]
    assert "EvidenceLogicIRV1" in v8.component_prompts["evidence"]
    assert "ResolutionGovernanceIRV1" in v8.component_prompts["governance"]
    assert all("local_key" in prompt for prompt in v8.component_prompts.values())
    v10 = load_prompt("brief_to_draft", "brief-to-draft-v10")
    assert "EvidenceLogicIRV2" in v10.component_prompts["evidence"]
    assert v10.package is not None
    assert v10.package.components["evidence"].output_schema_id == "evidence-logic-ir-v2"
    v11 = load_prompt("brief_to_draft", "brief-to-draft-v11")
    assert "StoryWorldIRV2" in v11.component_prompts["story"]
    assert "EvidenceLogicIRV2" in v11.component_prompts["evidence"]
    assert v11.package is not None
    assert v11.package.components["story"].output_schema_id == "story-world-ir-v2"
    assert v11.package.components["planner"].input_contract_id.endswith("input-v2")
    v12 = load_prompt("brief_to_draft", "brief-to-draft-v12")
    assert v12.package is not None
    assert set(v12.package.components) == {
        "planner",
        "temporal",
        "story",
        "evidence",
        "governance",
    }
    assert v12.package.components["temporal"].output_schema_id == "temporal-plan-v1"
    assert v12.package.components["story"].output_schema_id == "story-world-ir-v3"
    assert "不得输出 kind=unknown" in v12.component_prompts["temporal"]
    assert "严禁输出 time" in v12.component_prompts["story"]
    assert "`recommended_strategy`" in prompts["brief_strategy_options"]
    assert "不得生成完整 CaseFile" in prompts["brief_strategy_options"]
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
    assert (
        repository.load(
            "brief_polish",
            "brief-polish-v1",
        ).system_prompt
        == "Role: historical prompt.\n"
    )
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
