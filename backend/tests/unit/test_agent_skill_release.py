"""All active Agents are bound to an immutable, byte-compatible Skill release."""

import json
import runpy
from pathlib import Path

from casefile.agent_runtime.agent_skill_release import RELEASE, binding_for_prompt_version
from casefile.agent_runtime.prompt_repository import (
    load_prompt,
    packaged_prompt_repository,
    validate_prompt_repository,
)


def test_all_active_prompts_are_skill_bound_and_byte_compatible() -> None:
    repository = packaged_prompt_repository()
    definitions = validate_prompt_repository()
    active = {
        item.agent_id: item
        for item in definitions
        if item.version == repository.current_version(item.agent_id)
    }
    assert len(active) == 41
    for agent_id, original in active.items():
        selected = load_prompt(agent_id, original.version)
        assert selected.system_prompt == original.system_prompt
        assert selected.system_prompt_sha256 == original.system_prompt_sha256
        assert selected.component_prompts == original.component_prompts
        assert selected.skill_metadata["release"] == RELEASE


def test_historical_prompt_version_is_not_silently_rebound() -> None:
    historical = load_prompt("brief_polish", "brief-polish-v2")
    assert historical.skill_metadata == {}
    assert binding_for_prompt_version(historical.version) == {}


def test_physical_call_binding_records_runtime_selected_resources() -> None:
    package = binding_for_prompt_version("casefile-chat-v7", "router")
    chat = package["skill_bindings"]["casefile_chat"]
    assert package["skill_release"] == RELEASE
    assert chat["selected_resources"]
    assert chat["tool_policy"] == "no-tools-v1"

    legacy = binding_for_prompt_version("prose-fidelity-judge-v8")
    judge = legacy["skill_bindings"]["prose_fidelity_judge"]
    assert judge["selected_resources"] == ["method"]
    assert judge["reasons"] == {"method": "stage_required"}


def test_generated_release_is_reproducible(tmp_path: Path) -> None:
    build = runpy.run_path(str(Path(__file__).parents[2] / "scripts/build_agent_skill_release.py"))[
        "build"
    ]
    source = Path(__file__).parents[2] / "src/casefile/agent_runtime/prompts"
    output = tmp_path / "release"
    output.mkdir()
    generated = build(source, output)
    packaged = json.loads(
        (
            Path(__file__).parents[2]
            / "src/casefile/agent_runtime/skill_releases/agent-skill-runtime-v1/manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert generated == packaged
