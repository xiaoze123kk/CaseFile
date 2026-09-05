from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from casefile.api.schemas import AgentPatchApplyRequest, AgentPatchSimulateRequest
from casefile.application.chat_public_contracts import (
    public_patch_review_view,
    public_patch_set_view,
)
from casefile.application.chat_public_patches import (
    FieldLabelRegistry,
    ObjectLabelResolver,
    ValueFormatter,
    public_warning_id,
    resolve_public_warning_ids,
)
from casefile.application.errors import ApplicationError


def _patch(*, review_mode: str = "atomic") -> dict:
    return {
        "patch_set_id": 73,
        "base_draft_revision": 8,
        "review_mode": review_mode,
        "status": "pending",
        "is_stale": False,
        "contains_delete": True,
        "impact_hash": "opaque-delete-confirmation-token",
        "object_labels": {
            "evt_current": {"object_type": "event", "name": "当前事件名称"},
            "ent_lincheng": {"object_type": "entity", "name": "林澈"},
            "claim_internal_name": {
                "object_type": "claim",
                "name": "claim_internal_name",
            },
        },
        "operations": [
            {
                "operation_id": 701,
                "operation_type": "update_field",
                "object_id": "evt_current",
                "object_type": "event",
                "target_collection": "events",
                "target_object_key": "evt_current",
                "field_path": "/time",
                "old_value": {"start": "第 2 天 20:00", "end": "第 2 天 21:00"},
                "new_value": {"start": "第 2 天 21:00", "end": "第 2 天 22:00"},
                "origin": "closure_repair",
            },
            {
                "operation_id": 702,
                "operation_type": "create_object",
                "object_id": None,
                "target_collection": "locations",
                "target_object_key": "loc_new_internal",
                "field_path": "",
                "old_value": None,
                "new_value": {
                    "id": "loc_new_internal",
                    "name": "旧钟楼",
                    "description": "发现线索的地点。",
                },
                "origin": "primary",
            },
            {
                "operation_id": 703,
                "operation_type": "delete_object",
                "object_id": "ent_deleted_internal",
                "object_type": "entity",
                "target_collection": "entities",
                "target_object_key": "ent_deleted_internal",
                "field_path": "",
                "old_value": {
                    "id": "ent_deleted_internal",
                    "name": "废弃的证人",
                    "description": "不再需要。",
                },
                "new_value": None,
                "origin": "primary",
            },
            {
                "operation_id": 704,
                "operation_type": "replace",
                "object_id": "claim_missing_internal",
                "object_type": "claim",
                "target_collection": "claims",
                "target_object_key": "claim_missing_internal",
                "field_path": "/legacy_private_field",
                "old_value": "旧值",
                "new_value": "新值",
                "origin": "primary",
            },
            {
                "operation_id": 705,
                "operation_type": "add",
                "object_id": "evt_current",
                "object_type": "event",
                "target_collection": "events",
                "target_object_key": "evt_current",
                "field_path": "/participant_refs",
                "old_value": [],
                "new_value": [
                    {"object_type": "entity", "object_id": "ent_lincheng"},
                    {"object_type": "event", "object_id": "evt_unknown_internal"},
                ],
                "origin": "primary",
            },
            {
                "operation_id": 706,
                "operation_type": "remove",
                "object_id": "evt_current",
                "object_type": "event",
                "target_collection": "events",
                "target_object_key": "evt_current",
                "field_path": "/description",
                "old_value": "原描述",
                "new_value": None,
                "origin": "primary",
            },
        ],
    }


def test_field_labels_never_fall_back_to_json_pointer_or_field_name() -> None:
    registry = FieldLabelRegistry()

    assert registry.locale == "zh-CN"
    assert registry.label("/description") == "描述"
    assert registry.label("/time/start") == "开始时间"
    assert registry.label("/availability/acquisition_conditions") == "获知条件"
    assert registry.label("/legacy_private_field") == "卷宗内容"
    assert registry.label("not-a-pointer") == "卷宗内容"


def test_object_label_priority_and_value_formatting_hide_internal_ids() -> None:
    patch = _patch()
    labels = ObjectLabelResolver(patch)
    formatter = ValueFormatter(labels)

    current = labels.resolve(
        {
            "object_id": "evt_current",
            "object_type": "event",
            "old_value": {"title": "冻结旧名称"},
            "new_value": {"title": "冻结新名称"},
        }
    )
    deleted = labels.resolve(patch["operations"][2])
    created = labels.resolve(patch["operations"][1])
    missing = labels.resolve(patch["operations"][3])
    disguised_internal_name = labels.resolve(
        {"object_id": "claim_internal_name", "object_type": "claim"}
    )

    assert current.name == "当前事件名称"
    assert deleted.name == "废弃的证人"
    assert created.name == "旧钟楼"
    assert missing.name == "主张"
    assert disguised_internal_name.name == "主张"
    assert "claim_missing_internal" not in missing.name
    refs = formatter.format(patch["operations"][4]["new_value"])
    assert refs == {"kind": "list", "text": "林澈、事件"}
    assert "ent_lincheng" not in refs["text"]
    assert "evt_unknown_internal" not in refs["text"]
    assert formatter.format(["ent_lincheng", "evt_unknown_internal"]) == {
        "kind": "list",
        "text": "林澈、卷宗内容",
    }
    assert formatter.format(["/title", "/legacy_private_field"]) == {
        "kind": "list",
        "text": "标题、卷宗内容",
    }
    assert formatter.format({"start": "上午", "end": "下午"}) == {
        "kind": "time_range",
        "text": "上午 至 下午",
    }
    assert formatter.format({"kind": "exact", "value": "第 2 天 21:00"}) == {
        "kind": "text",
        "text": "第 2 天 21:00",
    }
    long_value = formatter.format("长" * 5000)
    assert len(long_value["text"]) == 4000
    assert long_value["text"].endswith("…")


def test_change_group_builder_supports_all_persisted_operation_types() -> None:
    projected = public_patch_set_view(_patch()).model_dump(mode="json")
    changes = projected["changes"]

    assert [change["change_id"] for change in changes] == [702, 703, 704, 705, 706, 701]
    assert [change["kind"] for change in changes] == [
        "create",
        "delete",
        "update",
        "update",
        "update",
        "update",
    ]
    assert changes[-1]["relationship"] == "consistency_support"
    assert (
        changes[-1]["explanation"]
        == "这项修改未记录具体原因，请让 Agent 补充依据后再决定是否应用。"
    )
    assert changes[2]["field_label"] == "卷宗内容"
    assert changes[0]["after"] == {
        "kind": "text",
        "text": "旧钟楼：发现线索的地点。",
    }
    assert changes[3]["field_label"] == "参与者"
    assert changes[3]["after"] == {"kind": "list", "text": "林澈、事件"}
    assert changes[-1]["field_label"] == "时间"
    assert changes[-1]["before"]["kind"] == "time_range"
    assert projected["summary"] == "你要求的修改 5 项；为保持一致性同步调整 1 项。"
    assert projected["impact"] == {
        "summary": "共涉及 6 项卷宗修改，其中包含删除，包含 1 项一致性调整。",
        "affected_change_count": 6,
        "has_deletions": True,
    }
    assert projected["review_rule"] == "atomic"
    assert public_patch_set_view(_patch(review_mode="selective")).review_rule.value == "selective"
    serialized = json.dumps(projected, ensure_ascii=False)
    assert "field_path" not in serialized
    assert "operation_type" not in serialized


def test_patch_changes_preserve_specific_persisted_reasons_for_all_operation_kinds() -> None:
    patch = _patch()
    reasons = [
        "目击者在九点才到场，因此把这段事件推迟一小时，避免提前目击。",
        "现场记录提到了钟楼，但卷宗缺少对应地点；补充后可明确线索发生位置。",
        "这名证人已被作者弃用，保留会使人物列表与当前设定不一致。",
        "原主张与最新证词冲突，因此收窄为证词能够支持的内容。",
        "现场记录确认林澈在场，补充参与者以对应记录。",
        "原描述与已经确认的时间安排矛盾，先移除该段不成立的描述。",
    ]
    for operation, reason in zip(patch["operations"], reasons, strict=True):
        operation["reason"] = reason
    changes = public_patch_set_view(patch).model_dump(mode="json")["changes"]
    assert {change["change_id"]: change["explanation"] for change in changes} == {
        operation["operation_id"]: operation["reason"] for operation in patch["operations"]
    }


@pytest.mark.parametrize("reason", [None, "", "  ", "这是你要求调整的卷宗内容。"])
def test_missing_or_generic_reasons_are_not_presented_as_specific_basis(reason: str | None) -> None:
    patch = _patch()
    patch["operations"][0]["reason"] = reason
    explanation = public_patch_set_view(patch).model_dump(mode="json")["changes"][-1]["explanation"]
    assert "未记录具体原因" in explanation


@pytest.mark.parametrize(
    "reason",
    [
        "internal reason",
        "修改 /time/start 以通过校验。",
        "因为 Worker 检查结果。",
        "修改 ent_lincheng 对应的字段。",
        "修改旧信息 info_unavailable 对应的字段。",
        "依据 opaque-delete-confirmation-token 进行修改。",
        "依据自定义编号 z9opaque 修改。",
        '原始结果 {"object_id": "evt_current"}',
        "因为" + "时间冲突" * 300 + "，内部字段 field_path。",
    ],
)
def test_internal_reasons_fail_closed_before_truncation(reason: str) -> None:
    patch = _patch()
    patch["operations"][0]["reason"] = reason
    patch["operations"][0]["operation_key"] = "z9opaque"
    explanation = public_patch_set_view(patch).model_dump(mode="json")["changes"][-1]["explanation"]
    assert explanation == "这项修改的具体原因暂无法展示，请让 Agent 补充可供审阅的依据。"


def test_long_public_reason_is_bounded_and_direction_values_are_readable() -> None:
    patch = _patch()
    patch["operations"][0]["reason"] = "对应现场记录中的时间冲突。" * 100
    explanation = public_patch_set_view(patch).model_dump(mode="json")["changes"][-1]["explanation"]
    assert len(explanation) == 1000
    assert explanation.endswith("…")
    formatter = ValueFormatter(ObjectLabelResolver(patch))
    assert formatter.format("directed")["text"] == "单向关系"
    assert formatter.format("bidirectional")["text"] == "双向关系"


def test_legacy_reason_translates_registered_terms_without_inventing_basis() -> None:
    patch = _patch()
    patch["operations"][0]["reason"] = (
        "信息 title 提到回避，但 content 缺少具体表现；补充后与 CaseFile 的设定一致。"
    )
    explanation = public_patch_set_view(patch).model_dump(mode="json")["changes"][-1]["explanation"]
    assert explanation == "信息 标题 提到回避，但 内容 缺少具体表现；补充后与 卷宗 的设定一致。"


def test_review_uses_simulation_authority_and_stable_opaque_warning_ids() -> None:
    finding_key = "det:internal_policy_finding_key"
    value = {
        **_patch(),
        "can_apply": False,
        "simulation": {
            "can_apply": False,
            "reason_code": "author_confirmation_required",
            "authorization_required_finding_keys": [finding_key],
            "final_findings": [
                {
                    "finding_key": finding_key,
                    "severity": "error",
                    "title": "internal title",
                }
            ],
        },
    }

    review = public_patch_review_view(value).model_dump(mode="json")
    warning_id = public_warning_id(73, finding_key)

    assert review["can_apply"] is False
    assert review["blockers"] == []
    assert review["warnings"] == [
        {
            "notice_id": warning_id,
            "message": "这项修改会留下需要你明确接受的一致性风险。",
        }
    ]
    assert review["requires_author_confirmation"] is True
    assert review["confirmation_token"] == "opaque-delete-confirmation-token"
    assert finding_key not in json.dumps(review)
    assert resolve_public_warning_ids(
        patch_id=73,
        accepted_warning_ids=[warning_id],
        simulation=value["simulation"],
    ) == [finding_key]
    with pytest.raises(ApplicationError, match="需要确认的影响已经变化"):
        resolve_public_warning_ids(
            patch_id=73,
            accepted_warning_ids=["warning_stale"],
            simulation=value["simulation"],
        )


def test_non_delete_hash_is_not_exposed_and_hard_failure_gets_public_blocker() -> None:
    value = {
        **_patch(),
        "contains_delete": False,
        "can_apply": False,
        "simulation": {
            "can_apply": False,
            "reason_code": "post_document_invalid",
            "authorization_required_finding_keys": [],
            "final_findings": [
                {
                    "finding_key": "det:raw-internal-key",
                    "severity": "blocker",
                }
            ],
        },
    }

    review = public_patch_review_view(value).model_dump(mode="json")

    assert review["confirmation_token"] is None
    assert review["requires_author_confirmation"] is False
    assert review["warnings"] == []
    assert review["blockers"][0]["notice_id"].startswith("blocker_")
    assert "det:raw-internal-key" not in json.dumps(review)

    authoritative_true = {
        **value,
        "can_apply": True,
        "simulation": {**value["simulation"], "can_apply": True},
    }
    authoritative_review = public_patch_review_view(authoritative_true).model_dump(mode="json")
    assert authoritative_review["can_apply"] is True
    assert authoritative_review["blockers"] == []

    applied_value = {**value, "status": "applied", "contains_delete": True}
    applied_review = public_patch_review_view(applied_value).model_dump(mode="json")
    assert applied_review["requires_author_confirmation"] is False
    assert applied_review["confirmation_token"] is None


def test_public_patch_requests_use_change_warning_and_confirmation_handles() -> None:
    apply = AgentPatchApplyRequest.model_validate(
        {
            "expected_draft_id": 4,
            "expected_revision": 8,
            "change_ids": [701, 702],
            "confirmation_token": "opaque-token",
            "accepted_warning_ids": ["warning_abc"],
            "confirmation_note": "我接受这项一致性风险。",
        }
    )
    simulate = AgentPatchSimulateRequest.model_validate(
        {
            "expected_draft_id": 4,
            "base_revision": 8,
            "change_ids": None,
        }
    )

    assert apply.change_ids == [701, 702]
    assert apply.confirmation_token == "opaque-token"
    assert simulate.change_ids is None
    with pytest.raises(ValidationError):
        AgentPatchApplyRequest.model_validate(
            {
                "expected_draft_id": 4,
                "expected_revision": 8,
                "operation_ids": [701],
            }
        )
    with pytest.raises(ValidationError):
        AgentPatchSimulateRequest.model_validate(
            {
                "expected_draft_id": 4,
                "base_revision": 8,
                "accepted_warning_ids": ["warning_abc"],
            }
        )
