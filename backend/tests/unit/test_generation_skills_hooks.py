"""Progressive materials, isolated callbacks, and production-path generation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from casefile.agent_runtime.brief_to_draft_runtime import resolve_pipeline_spec
from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1
from casefile.agent_runtime.brief_to_draft_v8.workflow import _build_context_pack, _model_step
from casefile.agent_runtime.generation_hooks import (
    HookBinding,
    HookDispatcher,
    HookEvent,
    HookExecutionError,
    HookInput,
    HookResult,
)
from casefile.agent_runtime.generation_skills import SkillLoader
from casefile.agent_runtime.models import GenerationRequest
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.providers import (
    FakeProvider,
    _add_fake_v10_matrix_plan,
    _fake_v8_output,
)
from casefile.agent_runtime.tools import TOOLSET_VERSION


def request(events: list[Any], version: str = "brief-to-draft-v17") -> GenerationRequest:
    return GenerationRequest(
        task_run_id=1,
        prompt_version=version,
        agent_version=version.replace("draft-v", "draft-pipeline-v"),
        toolset_version=TOOLSET_VERSION,
        brief={"creative_intent": "测试案件", "conclusion_mode": "unique"},
        casefile_id="case_test",
        brief_id="brief_test",
        brief_version=1,
        version_id="draft_test",
        version_no=1,
        parent_version_id=None,
        model_id="fake",
        api_key=None,
        max_turns=8,
        emit=lambda *args: events.append(args),
        schema_version="2.0",
    )


def test_dispatch_order_dedup_snapshot_and_issue_merge() -> None:
    seen = []

    async def first(event: HookInput) -> HookResult:
        event.payload["nested"]["value"] = 2
        seen.append("first")
        return HookResult(issues=({"code": "missing"},))

    async def second(event: HookInput) -> HookResult:
        assert event.payload["nested"]["value"] == 1
        seen.append("second")
        return HookResult(issues=({"code": "missing"},))

    dispatcher = HookDispatcher({("first", "1"): first, ("second", "1"): second})
    first_binding = HookBinding("first", "1", HookEvent.AFTER_ARTIFACT)
    records: list[dict[str, Any]] = []
    result = asyncio.run(
        dispatcher.dispatch(
            (first_binding, first_binding, HookBinding("second", "1", HookEvent.AFTER_ARTIFACT)),
            HookInput(HookEvent.AFTER_ARTIFACT, "test", payload={"nested": {"value": 1}}),
            records=records,
        )
    )
    assert seen == ["first", "second"]
    assert result.issues == ({"code": "missing"},)
    assert len(records) == 2


@pytest.mark.parametrize("failure", ["block", "exception", "timeout", "invalid_result"])
def test_required_hook_failures(failure: str) -> None:
    async def handler(event: HookInput) -> HookResult:
        if failure == "exception":
            raise ValueError("test")
        if failure == "timeout":
            await asyncio.sleep(10)
        if failure == "invalid_result":
            return HookResult(resources=("forbidden",))
        return HookResult(block_reason="blocked")

    records: list[dict[str, Any]] = []
    with pytest.raises(HookExecutionError):
        asyncio.run(
            HookDispatcher({("test", "1"): handler}).dispatch(
                (HookBinding("test", "1", HookEvent.AFTER_ARTIFACT, timeout=0.01),),
                HookInput(HookEvent.AFTER_ARTIFACT, "test"),
                records=records,
            )
        )
    assert records[0]["status"] == "failed"


def test_optional_observer_failure_continues() -> None:
    async def handler(event: HookInput) -> HookResult:
        raise ValueError("observation unavailable")

    records: list[dict[str, Any]] = []
    result = asyncio.run(
        HookDispatcher({("test", "1"): handler}).dispatch(
            (HookBinding("test", "1", HookEvent.STAGE_FINISHED, required=False),),
            HookInput(HookEvent.STAGE_FINISHED, "test"),
            records=records,
        )
    )
    assert result == HookResult()
    assert records[0]["status"] == "failed"


def test_skill_stage_and_repair_disclosure_and_fingerprint() -> None:
    req = request([])
    payload = {
        "context_pack": _build_context_pack(
            req, resolve_pipeline_spec(req.prompt_version)
        ).model_dump(mode="json")
    }
    package = load_prompt("brief_to_draft", req.prompt_version).package
    assert package is not None
    loader = SkillLoader()

    async def render(value: dict[str, Any]) -> Any:
        return await loader.render(
            package,
            "planner",
            value,
            agent_version=req.agent_version,
            toolset_version=req.toolset_version,
        )

    ordinary, metadata = asyncio.run(render(payload))
    repaired, repair_metadata = asyncio.run(
        render(
            {
                **payload,
                "targeted_repair_issues": [{"code": "unknown_future_issue"}],
            }
        )
    )
    assert "relationship_planner" in metadata["resources"]
    assert not any(item.startswith("repair_") for item in metadata["resources"])
    assert "repair_common" in repair_metadata["resources"]
    assert ordinary.input_sha256 != repaired.input_sha256
    again, _ = asyncio.run(render(payload))
    assert again.input_sha256 == ordinary.input_sha256
    assert "Matrix Evaluator" not in ordinary.instructions


def test_missing_or_corrupted_resources_fail_before_provider() -> None:
    package = load_prompt("brief_to_draft", "brief-to-draft-v17").package
    assert package is not None
    fragments = dict(package.fragments)
    fragments["planner"] = replace(fragments["planner"], content="corrupted")
    with pytest.raises(ValueError, match="hash mismatch"):
        SkillLoader().validate(replace(package, fragments=fragments))
    loader = SkillLoader()
    loader.manifest["components"]["planner"]["hooks"][0]["handler_id"] = "unknown"
    with pytest.raises(HookExecutionError, match="Unknown hook"):
        loader.validate(package)


def test_v17_full_generation_and_legacy_candidate_equivalence() -> None:
    events: list[Any] = []
    current = FakeProvider().generate(request(events))
    legacy = FakeProvider().generate(request([], "brief-to-draft-v16"))
    for candidate in (current.candidate, legacy.candidate):
        for collection in candidate.values():
            if isinstance(collection, list):
                for item in collection:
                    if isinstance(item, dict):
                        item.pop("updated_at", None)
    assert current.candidate == legacy.candidate
    completed = [
        payload
        for name, _, payload in events
        if name == "agent.step.completed" and "_execution" in payload
    ]
    assert len(completed) == 6
    assert all(payload["_execution"]["resources"] for payload in completed)
    assert any(name == "agent.hooks.executed" for name, _, _ in events)


def test_v18_plan_execute_generation_persists_plan_checkins_and_summary() -> None:
    events: list[Any] = []
    result = FakeProvider().generate(request(events, "brief-to-draft-v18"))
    assert result.planning_summary == {
        "status": "completed",
        "fulfilled": 2,
        "partial": 0,
        "not_fulfilled": 0,
        "unknown": 0,
        "unresolved_items": [],
        "suggested_plan_changes": [],
    }
    completed = [payload for name, _, payload in events if name == "agent.step.completed"]
    plan = next(item for item in completed if item["component_id"] == "planning_execution_plan")
    assert plan["schema_id"] == "casefile.execution-plan.v1"
    assert len(plan["_artifact"]["goals"]) == 2
    assert any(item["component_id"] == "plan_reconciliation" for item in completed)
    assert any(item["component_id"] == "planning_summary" for item in completed)


def test_v18_blueprint_repair_keeps_plan_and_competition_closure_in_context() -> None:
    """A Blueprint repair must receive the complete prior plan and competing paths."""

    req = request([], "brief-to-draft-v18")
    blueprint = _fake_v8_output(CaseBlueprintV1)
    _add_fake_v10_matrix_plan(CaseBlueprintV1, blueprint)
    package = load_prompt("brief_to_draft", req.prompt_version).package
    assert package is not None
    previous_plan = {
        "schema_id": "casefile.execution-plan-candidate.v1",
        "goals": [
            {
                "goal_id": "story_discovery",
                "objective": "保留发现事件",
                "verification": "Story 保留 discovery",
                "owner_branch": "story_world",
                "source_refs": ["author", "discovery"],
                "depends_on_goal_ids": [],
                "preserve_constraints": ["不得提前确认答案"],
                "applies_from": 1,
                "applies_until": 1,
            },
            {
                "goal_id": "evidence_competition",
                "objective": "保留竞争解释的证据闭包",
                "verification": "Evidence 保留两个假设及其路径",
                "owner_branch": "evidence_logic",
                "source_refs": ["record", "hypothesis", "alternative_hypothesis"],
                "depends_on_goal_ids": [],
                "preserve_constraints": [],
                "applies_from": 1,
                "applies_until": 1,
            },
        ],
    }
    rendered, metadata = asyncio.run(
        SkillLoader("v18").render(
            package,
            "planner",
            {
                "context_pack": _build_context_pack(
                    req, resolve_pipeline_spec(req.prompt_version)
                ).model_dump(mode="json"),
                "targeted_repair_issues": [
                    {"code": "competing_hypothesis_path_plan_missing"}
                ],
                "previous_output": blueprint,
                "previous_execution_plan": previous_plan,
            },
            agent_version=req.agent_version,
            toolset_version=req.toolset_version,
        )
    )

    repair_input = json.loads(rendered.input_text)
    assert "repair_common" in metadata["resources"]
    assert repair_input["previous_execution_plan"] == previous_plan
    assert {
        item["local_key"] for item in repair_input["previous_output"]["hypotheses"]
    } == {"hypothesis", "alternative_hypothesis"}
    assert {
        (item["target_key"], tuple(item["required_information_keys"]))
        for item in repair_input["previous_output"]["reasoning_paths"]
    } >= {
        ("hypothesis", ("record",)),
        ("alternative_hypothesis", ("record",)),
    }
    assert "previous_execution_plan 中的 goal_id、source_refs" in rendered.instructions
    assert "竞争 hypothesis 及其 reasoning_path" in rendered.instructions


def test_v18_temporal_prompt_retains_machine_parseable_time_requirements() -> None:
    req = request([], "brief-to-draft-v18")
    package = load_prompt("brief_to_draft", req.prompt_version).package
    assert package is not None
    rendered, _ = asyncio.run(
        SkillLoader("v18").render(
            package,
            "temporal",
            {
                "context_pack": _build_context_pack(
                    req, resolve_pipeline_spec(req.prompt_version)
                ).model_dump(mode="json"),
                "blueprint": _fake_v8_output(CaseBlueprintV1),
            },
            agent_version=req.agent_version,
            toolset_version=req.toolset_version,
        )
    )

    for requirement in (
        "YYYY-MM-DDTHH:MM",
        "range 的 start 与 end 必须使用同一种 precision 格式",
        "relative 的 before 和 after 必须提供非空的 offset_minutes",
        "至少一个 assignment 必须是 exact、approximate 或 range",
        "basis_refs",
    ):
        assert requirement in rendered.instructions


def test_v18_harness_failure_repair_constraints_remain_explicit() -> None:
    """Keep the latest live-harness repair failures tied to their prompt contracts."""

    package = load_prompt("brief_to_draft", "brief-to-draft-v18").package
    assert package is not None
    resources = {name: fragment.content for name, fragment in package.fragments.items()}

    assert "required_slots" in resources["repair_common"]
    assert "value_key，value 为 null" in resources["repair_common"]
    assert "value，value_key 为 null" in resources["repair_common"]
    assert "supporting_reasoning_path_keys" in resources["governance"]
    assert "同一 Resolution" in resources["governance"]
    assert "required_for_resolution" in resources["governance"]

    assert "Blueprint.relationships 完全相等" in resources["repair_story"]
    assert "绝不新增、删除、改名或临时生成 relationship local_key" in resources[
        "repair_story"
    ]
    assert "previous_execution_plan" in resources["planner"]
    assert "竞争 hypothesis 及其 reasoning_path 的闭环" in resources["repair_planner"]

    for requirement in (
        "值必须匹配其 precision",
        "range 的 start/end 同一 precision",
        "before/after 具有非空 offset_minutes",
        "不得输出 unknown、时区、低位零、循环或自引用",
    ):
        assert requirement in resources["repair_common"]


def test_model_step_reuses_only_matching_execution_fingerprint() -> None:
    events: list[Any] = []
    req = request(events)
    FakeProvider().generate(req)
    started = next(
        p
        for n, _, p in events
        if n == "agent.step.started" and p["component_id"] == "case_blueprint_planner"
    )
    completed = next(
        p
        for n, _, p in events
        if n == "agent.step.completed" and p["component_id"] == "case_blueprint_planner"
    )
    reusable = {
        "case_blueprint_planner": {
            "input_hash": started["input_hash"],
            "schema_id": started["schema_id"],
            "output_hash": completed["output_hash"],
            "output": completed["_artifact"],
            "step_run_id": 1,
        }
    }
    calls = []

    async def call(*args: Any) -> Any:
        calls.append(args)
        return completed["_artifact"], {}

    resumed = replace(req, reusable_steps=reusable)

    async def run() -> Any:
        return await _model_step(
            resumed,
            call,
            component_id="case_blueprint_planner",
            prompt_component="planner",
            stage="planning",
            output_type=CaseBlueprintV1,
            input_payload={
                "context_pack": _build_context_pack(
                    req, resolve_pipeline_spec(req.prompt_version)
                ).model_dump(mode="json")
            },
        )

    asyncio.run(run())
    assert not calls
    reusable["case_blueprint_planner"]["input_hash"] = "0" * 64
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_skill_activation_scope_cleans_up(outcome: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from casefile.agent_runtime.generation_skills import SkillActivation

    closed = []
    original = SkillActivation.close

    def close(activation: SkillActivation) -> None:
        original(activation)
        closed.append(activation)

    monkeypatch.setattr(SkillActivation, "close", close)
    req = request([])
    package = load_prompt("brief_to_draft", req.prompt_version).package
    assert package is not None
    payload = {
        "context_pack": _build_context_pack(
            req, resolve_pipeline_spec(req.prompt_version)
        ).model_dump(mode="json")
    }

    async def run() -> None:
        async with SkillLoader().activate(
            package,
            "planner",
            payload,
            agent_version=req.agent_version,
            toolset_version=req.toolset_version,
        ):
            assert not closed
            if outcome == "failure":
                raise ValueError("provider failed")
            if outcome == "cancel":
                raise asyncio.CancelledError

    if outcome == "success":
        asyncio.run(run())
    else:
        with pytest.raises(ValueError if outcome == "failure" else asyncio.CancelledError):
            asyncio.run(run())
    assert len(closed) == 1 and closed[0].closed and not closed[0].bindings


def test_parallel_material_selection_does_not_leak() -> None:
    req = request([])
    package = load_prompt("brief_to_draft", req.prompt_version).package
    assert package is not None
    payload = {
        "context_pack": _build_context_pack(
            req, resolve_pipeline_spec(req.prompt_version)
        ).model_dump(mode="json")
    }
    loader = SkillLoader()

    async def run() -> Any:
        return await asyncio.gather(
            *(
                loader.render(
                    package,
                    "planner",
                    value,
                    agent_version=req.agent_version,
                    toolset_version=req.toolset_version,
                )
                for value in (
                    payload,
                    {
                        **payload,
                        "targeted_repair_issues": [{"code": "resolution_hypothesis_plan_missing"}],
                    },
                )
            )
        )

    normal, repair = asyncio.run(run())
    assert "repair_planner" not in normal[1]["resources"]
    assert "repair_planner" in repair[1]["resources"]
    assert len(repair[1]["resources"]) == len(set(repair[1]["resources"]))


def test_all_historical_instruction_paragraphs_are_preserved_in_resources() -> None:
    old = load_prompt("brief_to_draft", "brief-to-draft-v16").package
    new = load_prompt("brief_to_draft", "brief-to-draft-v17").package
    assert old is not None and new is not None
    paragraphs = {
        paragraph
        for fragment in new.fragments.values()
        for paragraph in fragment.content.strip().split("\n\n")
    }
    assert all(
        paragraph in paragraphs
        for fragment in old.fragments.values()
        for paragraph in fragment.content.strip().split("\n\n")
    )


def test_registered_validator_extension_needs_no_workflow_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from casefile.agent_runtime import generation_validation_hooks as hooks

    req = request([])
    spec = resolve_pipeline_spec(req.prompt_version)

    async def extra(event: HookInput) -> HookResult:
        return HookResult(issues=({"code": "test_extension", "path": "/entities"},))

    registry = {**hooks.DISPATCHER.handlers, ("test_extension", "1"): extra}
    monkeypatch.setattr(hooks, "DISPATCHER", HookDispatcher(registry))
    monkeypatch.setattr(
        hooks,
        "resolve_pipeline_spec",
        lambda _: replace(
            spec,
            hook_bindings=(
                *spec.hook_bindings,
                HookBinding(
                    "test_extension",
                    "1",
                    HookEvent.AFTER_ARTIFACT,
                    component="_creator_chinese_issues",
                ),
            ),
        ),
    )
    issues = asyncio.run(hooks.validate_generation_artifact(req, "_creator_chinese_issues", {}))
    assert issues == [{"code": "test_extension", "path": "/entities"}]


def test_activation_failure_is_persistable_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []
    req = request(events)
    called = False

    def invalid(*args: Any) -> None:
        raise ValueError("Skill resource hash mismatch")

    async def call(*args: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("Invalid resources must never reach the provider")

    monkeypatch.setattr(SkillLoader, "validate", invalid)
    with pytest.raises(ValueError, match="hash mismatch"):
        asyncio.run(
            _model_step(
                req,
                call,
                component_id="case_blueprint_planner",
                prompt_component="planner",
                stage="planning",
                output_type=CaseBlueprintV1,
                input_payload={},
            )
        )
    assert not called
    failure = next(payload for event, _, payload in events if event == "agent.step.failed")
    assert failure["error_code"] == "skill_activation_failed"
    assert failure["recoverable"] is False
