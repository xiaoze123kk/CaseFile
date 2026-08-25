from __future__ import annotations

import json
from pathlib import Path

import pytest
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerRequest,
    MutationPlanV1,
    MutationPlanV2,
    explicit_batch_create_count,
    general_mutation_explicit_system_field_reason,
    general_mutation_explicit_unknown_object_ids,
    general_mutation_request_budget_reason,
    general_mutation_request_dependency_reason,
)
from casefile.agent_runtime.general_mutation_prompt import (
    general_mutation_output_type,
    render_general_mutation_prompt,
)
from casefile.application.agent_mutation import (
    GeneralMutationBindingError,
    bind_general_mutation_plan,
    general_mutation_impact_hash,
)
from casefile.domain.logical_mutation import CreateObject, UpdateField
from casefile.domain.verification_engine import VerificationEngine
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]


def _document() -> dict[str, object]:
    return json.loads(
        (ROOT / "fixtures/casefiles/restart_loop.casefile.json").read_text(encoding="utf-8")
    )


def test_plan_rejects_duplicate_local_refs_and_dependency_cycles() -> None:
    base = {
        "operation_type": "create_object",
        "collection": "entities",
        "fields": {"entity_type": "person", "name": "新角色"},
        "reason": "需要新角色",
    }
    with pytest.raises(ValidationError, match="general_mutation_local_ref_duplicate"):
        MutationPlanV1.model_validate(
            {
                "operations": [
                    {**base, "operation_key": "a", "local_ref": "new_actor"},
                    {**base, "operation_key": "b", "local_ref": "new_actor"},
                ]
            }
        )
    with pytest.raises(ValidationError, match="general_mutation_dependency_cycle"):
        MutationPlanV1.model_validate(
            {
                "operations": [
                    {
                        **base,
                        "operation_key": "a",
                        "local_ref": "actor_a",
                        "depends_on_operation_keys": ["b"],
                    },
                    {
                        **base,
                        "operation_key": "b",
                        "local_ref": "actor_b",
                        "depends_on_operation_keys": ["a"],
                    },
                ]
            }
        )


def test_plan_rejects_protected_collection_and_model_system_fields() -> None:
    with pytest.raises(ValidationError, match="general_mutation_collection_forbidden"):
        MutationPlanV1.model_validate(
            {
                "operations": [
                    {
                        "operation_key": "create_resolution",
                        "operation_type": "create_object",
                        "local_ref": "resolution",
                        "collection": "resolution_specs",
                        "fields": {"title": "禁止"},
                        "reason": "越权",
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="general_mutation_model_system_field_forbidden"):
        MutationPlanV1.model_validate(
            {
                "operations": [
                    {
                        "operation_key": "create_entity",
                        "operation_type": "create_object",
                        "local_ref": "actor",
                        "collection": "entities",
                        "fields": {"id": "ent_injected", "name": "禁止"},
                        "reason": "注入 ID",
                    }
                ]
            }
        )


@pytest.mark.parametrize("operation_type", ("update_field", "delete_object"))
def test_v2_binder_rejects_protected_collection_targets(operation_type: str) -> None:
    operation = {
        "operation_key": "mutate_constraint",
        "operation_type": operation_type,
        "target": {"ref_kind": "existing", "object_id": "con_no_supernatural"},
        "reason": "不得修改服务端保护集合",
    }
    if operation_type == "update_field":
        operation.update({"field_path": "/statement", "new_value": "允许超自然解释"})
    plan = MutationPlanV2.model_validate({"operations": [operation]})

    with pytest.raises(GeneralMutationBindingError, match="general_mutation_collection_forbidden"):
        bind_general_mutation_plan(plan, _document(), task_run_id=1, draft_id=1, base_revision=1)


def test_v1_protected_update_remains_replayable() -> None:
    plan = MutationPlanV1.model_validate(
        {
            "operations": [
                {
                    "operation_key": "historical_constraint_update",
                    "operation_type": "update_field",
                    "target": {
                        "ref_kind": "existing",
                        "object_id": "con_no_supernatural",
                    },
                    "field_path": "/statement",
                    "new_value": "历史报告回放值",
                    "reason": "验证 v1 历史计划仍可回放",
                }
            ]
        }
    )

    bound = bind_general_mutation_plan(
        plan, _document(), task_run_id=1, draft_id=1, base_revision=1
    )

    assert bound.binder_version == "general-mutation-binder-v1"
    assert bound.operations[0].target_collection == "constraints"


def test_binder_is_deterministic_and_resolves_local_references() -> None:
    plan = MutationPlanV1.model_validate(
        {
            "operations": [
                {
                    "operation_key": "rename_created",
                    "operation_type": "update_field",
                    "target": {"ref_kind": "local", "local_ref": "new_actor"},
                    "field_path": "/name",
                    "new_value": "新名字",
                    "reason": "完善名称",
                },
                {
                    "operation_key": "create_actor",
                    "operation_type": "create_object",
                    "local_ref": "new_actor",
                    "collection": "entities",
                    "fields": {"entity_type": "person", "name": "临时名字"},
                    "reason": "新增角色",
                },
            ]
        }
    )
    first = bind_general_mutation_plan(
        plan,
        _document(),
        task_run_id=77,
        draft_id=9,
        base_revision=3,
        updated_at="2042-06-01T13:00:00Z",
    )
    second = bind_general_mutation_plan(
        plan,
        _document(),
        task_run_id=77,
        draft_id=9,
        base_revision=3,
        updated_at="2042-06-01T13:00:00Z",
    )
    assert first == second
    assert first.operations[0].target_object_key == "ent_agent_t77_01"
    assert isinstance(first.mutation_set.operations[0], CreateObject)
    assert isinstance(first.mutation_set.operations[1], UpdateField)
    assert first.mutation_set.operations[1].object_id == "ent_agent_t77_01"


def test_binder_rejects_unknown_local_ref_and_impact_hash_is_stable() -> None:
    unknown = MutationPlanV1.model_validate(
        {
            "operations": [
                {
                    "operation_key": "rename_missing",
                    "operation_type": "update_field",
                    "target": {"ref_kind": "local", "local_ref": "missing"},
                    "field_path": "/name",
                    "new_value": "不存在",
                    "reason": "非法引用",
                }
            ]
        }
    )
    with pytest.raises(GeneralMutationBindingError, match="general_mutation_local_ref_unknown"):
        bind_general_mutation_plan(unknown, _document(), task_run_id=1, draft_id=1, base_revision=1)

    update = MutationPlanV1.model_validate(
        {
            "operations": [
                {
                    "operation_key": "rename",
                    "operation_type": "update_field",
                    "target": {
                        "ref_kind": "existing",
                        "object_id": "ent_researcher",
                    },
                    "field_path": "/name",
                    "new_value": "林博士",
                    "reason": "统一称谓",
                }
            ]
        }
    )
    bound = bind_general_mutation_plan(
        update, _document(), task_run_id=2, draft_id=1, base_revision=1
    )
    simulation = VerificationEngine(profile="fast").simulate_mutation_set(
        _document(), bound.mutation_set
    )
    assert general_mutation_impact_hash(simulation) == general_mutation_impact_hash(simulation)
    assert len(general_mutation_impact_hash(simulation)) == 64


def _relationship_plan(plan_type, from_ref, to_ref):  # type: ignore[no-untyped-def]
    return plan_type.model_validate(
        {
            "operations": [
                {
                    "operation_key": "create_actor",
                    "operation_type": "create_object",
                    "local_ref": "new_actor",
                    "collection": "entities",
                    "fields": {"entity_type": "person", "name": "新角色"},
                    "reason": "新增角色",
                },
                {
                    "operation_key": "create_relationship",
                    "operation_type": "create_object",
                    "local_ref": "new_relationship",
                    "collection": "relationships",
                    "fields": {
                        "title": "协作关系",
                        "from_ref": from_ref,
                        "to_ref": to_ref,
                        "relationship_type": "assists",
                        "direction": "directed",
                        "truth_status": "canon_true",
                        "visibility": "public",
                    },
                    "depends_on_operation_keys": ["create_actor"],
                    "reason": "建立关系",
                },
            ]
        }
    )


def test_v1_nested_reference_replay_remains_typed() -> None:
    plan = _relationship_plan(
        MutationPlanV1,
        {"ref_kind": "local", "local_ref": "new_actor", "object_type": "entity"},
        {
            "ref_kind": "existing",
            "object_id": "ent_researcher",
            "object_type": "entity",
        },
    )
    bound = bind_general_mutation_plan(
        plan, _document(), task_run_id=10, draft_id=1, base_revision=1
    )

    relationship = bound.operations[1].new_value
    assert relationship["from_ref"]["object_type"] == "entity"
    assert relationship["to_ref"]["object_id"] == "ent_researcher"
    assert bound.binder_version == "general-mutation-binder-v1"


def test_v2_nested_references_infer_local_and_existing_types() -> None:
    plan = _relationship_plan(
        MutationPlanV2,
        {"ref_kind": "local", "local_ref": "new_actor"},
        {"ref_kind": "existing", "object_id": "ent_researcher"},
    )
    bound = bind_general_mutation_plan(
        plan, _document(), task_run_id=11, draft_id=1, base_revision=1
    )

    relationship = bound.operations[1].new_value
    assert relationship["from_ref"] == {
        "object_type": "entity",
        "object_id": "ent_agent_t11_01",
    }
    assert relationship["to_ref"] == {
        "object_type": "entity",
        "object_id": "ent_researcher",
    }
    assert bound.binder_version == "general-mutation-binder-v3"


def test_v2_rejects_redundant_type_and_unknown_existing_reference() -> None:
    with pytest.raises(ValidationError, match="general_mutation_ref_object_type_forbidden"):
        _relationship_plan(
            MutationPlanV2,
            {
                "ref_kind": "local",
                "local_ref": "new_actor",
                "object_type": "entity",
            },
            {"ref_kind": "existing", "object_id": "ent_researcher"},
        )

    with pytest.raises(ValidationError, match="general_mutation_ref_object_type_forbidden"):
        MutationPlanV2.model_validate(
            {
                "operations": [
                    {
                        "operation_key": "inject_formal_ref",
                        "operation_type": "update_field",
                        "target": {
                            "ref_kind": "existing",
                            "object_id": "claim_backup_trigger",
                        },
                        "field_path": "/support_refs",
                        "new_value": [
                            {
                                "object_type": "information_unit",
                                "object_id": "info_external_secret",
                            }
                        ],
                        "reason": "禁止绕过 v2 identity-only ref",
                    }
                ]
            }
        )

    unknown = _relationship_plan(
        MutationPlanV2,
        {"ref_kind": "local", "local_ref": "new_actor"},
        {"ref_kind": "existing", "object_id": "ent_other_case"},
    )
    with pytest.raises(GeneralMutationBindingError, match="general_mutation_object_unknown"):
        bind_general_mutation_plan(
            unknown, _document(), task_run_id=12, draft_id=1, base_revision=1
        )


def test_v2_rejects_inferred_type_incompatible_with_target_field() -> None:
    plan = MutationPlanV2.model_validate(
        {
            "operations": [
                {
                    "operation_key": "create_location",
                    "operation_type": "create_object",
                    "local_ref": "new_location",
                    "collection": "locations",
                    "fields": {"name": "错误参与者"},
                    "reason": "测试类型门禁",
                },
                {
                    "operation_key": "set_participant",
                    "operation_type": "update_field",
                    "target": {
                        "ref_kind": "existing",
                        "object_id": "evt_restart_seven",
                    },
                    "field_path": "/participant_refs",
                    "new_value": [{"ref_kind": "local", "local_ref": "new_location"}],
                    "depends_on_operation_keys": ["create_location"],
                    "reason": "测试类型门禁",
                },
            ]
        }
    )
    with pytest.raises(GeneralMutationBindingError, match="general_mutation_ref_type_mismatch"):
        bind_general_mutation_plan(plan, _document(), task_run_id=13, draft_id=1, base_revision=1)


@pytest.mark.parametrize(
    ("prompt_version", "expected_type"),
    [
        ("general-mutation-planner-v1", MutationPlanV1),
        ("general-mutation-planner-v2", MutationPlanV2),
        ("general-mutation-planner-v3", MutationPlanV2),
        ("general-mutation-planner-v4", MutationPlanV2),
        ("general-mutation-planner-v5", MutationPlanV2),
        ("general-mutation-planner-v6", MutationPlanV2),
    ],
)
def test_prompt_version_routes_matching_output_contract(
    prompt_version: str,
    expected_type: type[MutationPlanV1] | type[MutationPlanV2],
) -> None:
    rendered = render_general_mutation_prompt(
        GeneralMutationPlannerRequest(
            task_run_id=1,
            model_id="fake",
            api_key=None,
            casefile=_document(),
            message="新增人物",
            input_hash="0" * 64,
            editable_fields_by_collection={"entities": ("name",)},
            emit=lambda *_args: None,
            prompt_version=prompt_version,
        )
    )

    assert general_mutation_output_type(rendered) is expected_type


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("一次创建 13 个新人物，分别加入当前卷宗。", 13),
        ("新建2名人物并补充名称。", 2),
        ("把事件时间改为 13:00。", None),
        ("给实体新增 13 个标签。", None),
    ],
)
def test_explicit_batch_create_count_is_narrow(
    message: str, expected: int | None
) -> None:
    assert explicit_batch_create_count(message) == expected


def test_explicit_batch_create_over_budget_fails_before_planning() -> None:
    assert (
        general_mutation_request_budget_reason("创建 13 个新人物。")
        == "general_mutation_requested_create_budget_exceeded"
    )
    assert general_mutation_request_budget_reason("创建 4 个新人物。") is None


@pytest.mark.parametrize(
    "message",
    (
        "把事件 evt_restart_seven 的 revision 改成 99。",
        "把主张的 confirmation_status 改成 confirmed。",
        "把 ent_researcher.id 改成 ent_intruder。",
    ),
)
def test_explicit_system_field_is_blocked_before_planning(message: str) -> None:
    assert (
        general_mutation_explicit_system_field_reason(message)
        == "general_mutation_requested_system_field_forbidden"
    )


def test_system_field_substrings_do_not_trigger_preflight() -> None:
    assert general_mutation_explicit_system_field_reason("新增 identity 标签。") is None


def test_explicit_dependency_cycle_is_blocked_before_planning() -> None:
    assert (
        general_mutation_request_dependency_reason("甲依赖乙，乙依赖甲，必须保持循环依赖。")
        == "general_mutation_requested_dependency_cycle"
    )
    assert general_mutation_request_dependency_reason("甲的创建依赖乙。") is None


def test_explicit_unknown_object_ids_exclude_contract_fields_and_known_ids() -> None:
    unknown = general_mutation_explicit_unknown_object_ids(
        (
            "把主张 claim_backup_trigger 的 support_refs "
            "改为只引用 info_external_secret。"
        ),
        _document(),
        {"claims": ("support_refs", "status")},
    )

    assert unknown == ("info_external_secret",)
    assert general_mutation_explicit_unknown_object_ids(
        "把主张状态改为 partially_supported。",
        _document(),
        {"claims": ("status",)},
    ) == ()
