"""Bounded provider-neutral N4.4 Scene Fill tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.scene_compiler import (
    SceneFillBatchRequest,
    SceneFillBatchResult,
    execute_scene_semantic_fill,
    validate_scene_semantic_fill,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    SceneFillValidationError,
)


def _ref(object_type: str, object_id: str) -> dict[str, str]:
    return {"object_type": object_type, "object_id": object_id}


def _batch(ordinal: int, scene_id: str) -> dict[str, Any]:
    entity = _ref("entity", "ent_actor")
    event = _ref("event", "evt_anchor")
    return {
        "batch_id": f"scene_batch_chapter_1_{ordinal:03d}",
        "ordinal": ordinal,
        "chapter_id": "chapter_1",
        "scene_ids": [scene_id],
        "scenes": [
            {
                "scene_id": scene_id,
                "chapter_id": "chapter_1",
                "discourse_order": ordinal,
                "purpose": "investigation",
                "presentation_mode": "linear",
                "pov_ref": entity,
                "participant_refs": [entity],
                "location_ref": None,
                "story_time_refs": [event],
                "basis_refs": [event],
                "prerequisite_scene_ids": [],
                "obligations": [
                    {
                        "obligation_key": f"obligation_{scene_id}_event_001",
                        "kind": "event",
                        "basis_refs": [event],
                        "event_ref": event,
                        "exposure": None,
                        "resolution": None,
                    }
                ],
                "forbidden_reveal_entry_keys": [],
            }
        ],
        "object_catalog": [
            {"object_ref": entity, "label": "调查者", "facts": []},
            {"object_ref": event, "label": "关键事件", "facts": []},
        ],
        "state_seed": {"character_knowledge": [], "events": []},
    }


def _model_view(count: int = 1) -> dict[str, Any]:
    return {
        "schema_id": "compiler.scene-compiler-model-view.v1",
        "source": {
            "projection_version": "compiler.scene-compiler-model-view-projection.v1",
            "scene_compiler_input_hash": "1" * 64,
        },
        "batches": [_batch(index, f"scene_{index}") for index in range(1, count + 1)],
    }


def test_fake_scene_fill_is_bounded_chained_and_deterministic() -> None:
    execution = execute_scene_semantic_fill(
        FakeProvider(),
        task_run_id=7,
        model_view=_model_view(2),
        component_hash="2" * 64,
        model_id="fake-scene-compiler",
        api_key="unused",
    )

    assert len(execution.proposals) == 2
    assert [stage.batch_ordinal for stage in execution.stages] == [1, 2]
    assert execution.stages[1].inbound_state_hash == execution.stages[0].outbound_state_hash
    assert execution.final_state_hash == execution.stages[-1].outbound_state_hash
    assert all(not stage.recovered for stage in execution.stages)


def test_exact_recovery_skips_provider_and_preserves_hash_chain() -> None:
    baseline = execute_scene_semantic_fill(
        FakeProvider(),
        task_run_id=8,
        model_view=_model_view(),
        component_hash="3" * 64,
        model_id="fake-scene-compiler",
        api_key="unused",
    )

    class FailingProvider:
        def fill_scene_batch(self, request: SceneFillBatchRequest) -> SceneFillBatchResult:
            raise AssertionError(f"unexpected provider call: {request.input_hash}")

    recovered = execute_scene_semantic_fill(
        FailingProvider(),
        task_run_id=8,
        model_view=_model_view(),
        component_hash="3" * 64,
        model_id="fake-scene-compiler",
        api_key="unused",
        recover_stage=lambda batch_id, input_hash: (
            baseline.proposals[0]
            if batch_id == baseline.stages[0].batch_id
            and input_hash == baseline.stages[0].input_hash
            else None
        ),
    )

    assert recovered.stages[0].recovered
    assert recovered.final_state_hash == baseline.final_state_hash


def test_fill_rejects_unknown_refs_duplicate_obligation_and_forward_dependency() -> None:
    batch = _batch(1, "scene_1")
    proposal = (
        FakeProvider()
        .fill_scene_batch(
            SceneFillBatchRequest(
                task_run_id=1,
                prompt_version="scene-compiler-semantic-fill-v1",
                batch_view=batch,
                inbound_state_hash="0" * 64,
                input_hash="1" * 64,
                model_id="fake",
                api_key="unused",
            )
        )
        .proposal
    )

    invalid = deepcopy(proposal)
    invalid["scenes"][0]["beats"][0]["actor_refs"] = [_ref("entity", "ent_unknown")]
    with pytest.raises(CompilerContractError, match="compiler_scene_fill_actor_invalid"):
        validate_scene_semantic_fill(invalid, batch_view=batch)

    invalid = deepcopy(proposal)
    invalid["scenes"][0]["beats"][0]["target_refs"] = [
        _ref("information_unit", "info_unknown")
    ]
    with pytest.raises(
        SceneFillValidationError, match="compiler_scene_fill_reference_invalid"
    ) as captured:
        validate_scene_semantic_fill(invalid, batch_view=batch)
    assert captured.value.evidence["batch_id"] == "scene_batch_chapter_1_001"
    assert captured.value.evidence["scene_id"] == "scene_1"
    assert captured.value.evidence["beat_local_key"] == "beat_local_scene_1_001"
    assert captured.value.evidence["json_path"] == "/scenes/0/beats/0/target_refs/0"
    assert captured.value.evidence["emitted_ref"] == _ref(
        "information_unit", "info_unknown"
    )
    assert captured.value.evidence["allowed_ref_count"] == 2
    assert len(captured.value.evidence["allowed_ref_hash"]) == 64

    invalid = deepcopy(proposal)
    invalid["scenes"][0]["beats"].append(deepcopy(invalid["scenes"][0]["beats"][0]))
    invalid["scenes"][0]["beats"][1]["local_key"] = "beat_local_scene_1_002"
    invalid["scenes"][0]["beats"][1]["depends_on"] = ["beat_local_scene_1_001"]
    with pytest.raises(
        CompilerContractError, match="compiler_scene_fill_obligation_coverage_invalid"
    ):
        validate_scene_semantic_fill(invalid, batch_view=batch)

    invalid = deepcopy(proposal)
    invalid["scenes"][0]["beats"][0]["depends_on"] = ["beat_local_future"]
    with pytest.raises(CompilerContractError, match="compiler_scene_fill_dependency_invalid"):
        validate_scene_semantic_fill(invalid, batch_view=batch)

    forbidden = deepcopy(proposal)
    batch["scenes"][0]["forbidden_reveal_entry_keys"] = ["exposure_future_secret"]
    forbidden["scenes"][0]["beats"][0]["directive"] += " exposure_future_secret"
    with pytest.raises(CompilerContractError, match="compiler_scene_fill_forbidden_reveal"):
        validate_scene_semantic_fill(forbidden, batch_view=batch)
