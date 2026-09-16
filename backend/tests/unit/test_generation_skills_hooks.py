"""Progressive materials, isolated callbacks, and production-path generation."""

from __future__ import annotations

import asyncio
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
from casefile.agent_runtime.providers import FakeProvider
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
