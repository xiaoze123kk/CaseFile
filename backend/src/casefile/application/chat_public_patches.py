"""Deterministic zh-CN projection for author-readable Chat patch review."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from casefile.application.errors import ApplicationError

PUBLIC_PATCH_LOCALE: Final = "zh-CN"

_TYPE_LABELS: Final = {
    "resolution_spec": "谜题解答",
    "entity": "人物或对象",
    "relationship": "关系",
    "location": "地点",
    "event": "事件",
    "information_unit": "信息",
    "claim": "主张",
    "hypothesis": "假设",
    "reasoning_path": "推理路径",
    "constraint": "约束",
    "structure_lock": "结构锁定",
    "resolution_specs": "谜题解答",
    "entities": "人物或对象",
    "relationships": "关系",
    "locations": "地点",
    "events": "事件",
    "information_units": "信息",
    "claims": "主张",
    "hypotheses": "假设",
    "reasoning_paths": "推理路径",
    "constraints": "约束",
    "structure_locks": "结构锁定",
}

_FIELD_LABELS: Final = {
    "accepted_answers": "可接受答案",
    "access_rules": "进入条件",
    "adjacency_refs": "相邻地点",
    "aliases": "别名",
    "alternative_path_refs": "替代推理路径",
    "availability": "可获知条件",
    "capabilities": "能力",
    "cause_refs": "前因",
    "claim_type": "主张类型",
    "classification": "保密级别",
    "competing_hypothesis_refs": "竞争假设",
    "conclusion": "结论",
    "conclusion_mode": "结论方式",
    "conflict_refs": "冲突约束",
    "content": "内容",
    "dependency_claim_refs": "依赖主张",
    "description": "描述",
    "direction": "关系方向",
    "effect_refs": "后果",
    "entity_type": "人物或对象类型",
    "falsifier_refs": "证伪条件",
    "field_paths": "锁定字段",
    "from_ref": "关系起点",
    "goals": "目标",
    "information_type": "信息类型",
    "level": "约束等级",
    "location_ref": "发生地点",
    "lock_type": "锁定类型",
    "materiality": "重要程度",
    "name": "名称",
    "object_ref": "锁定对象",
    "observed_by_refs": "目击者",
    "parent_ref": "上级地点",
    "participant_refs": "参与者",
    "path_type": "推理路径类型",
    "proposition": "假设内容",
    "question_type": "谜题类型",
    "reason": "原因",
    "reasoning_question": "核心问题",
    "refute_refs": "反驳证据",
    "refutes_claim_refs": "反驳主张",
    "relationship_type": "关系类型",
    "reliability": "可靠程度",
    "required_claim_refs": "必要主张",
    "required_for_resolution": "是否为解答所必需",
    "required_slots": "必要信息位",
    "rule_expression": "规则表达",
    "scope_refs": "适用范围",
    "score": "可信评分",
    "secrets": "秘密",
    "source_event_ref": "来源事件",
    "spatial_position": "空间位置",
    "statement": "陈述",
    "status": "状态",
    "steps": "推理步骤",
    "support_refs": "支持证据",
    "supports_claim_refs": "支持主张",
    "tags": "标签",
    "target_ref": "推理目标",
    "target_resolution_ref": "目标谜题",
    "time": "时间",
    "title": "标题",
    "to_ref": "关系终点",
    "traits": "特征",
    "travel_times": "通行时间",
    "truth_status": "真实性",
    "visibility": "可见范围",
    "visibility_rules": "可见条件",
}

_EXACT_FIELD_LABELS: Final = {
    "/time/start": "开始时间",
    "/time/end": "结束时间",
    "/time/precision": "时间精度",
    "/availability/perspective_refs": "可获知角色",
    "/availability/acquisition_conditions": "获知条件",
    "/availability/alternative_path_refs": "替代获知路径",
}

_ENUM_LABELS: Final = {
    "true": "真实",
    "false": "不真实",
    "unknown": "尚未确定",
    "confirmed": "已确认",
    "proposed": "待确认",
    "resolved": "已解决",
    "unresolved": "未解决",
    "active": "进行中",
    "inactive": "未启用",
    "public": "公开",
    "private": "不公开",
    "hidden": "隐藏",
    "hard": "必须遵守",
    "soft": "建议遵守",
}

_INTERNAL_VALUE = re.compile(
    r"(?i)^(?:"
    r"(?:ent|evt|obj|claim|scene|loc|rel|clue|info|hyp|path|resolution|constraint|"
    r"lock|draft|task|patch|run)"
    r"_[a-z0-9][a-z0-9_-]{2,}|"
    r"[0-9a-f]{64}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r")$"
)


class FieldLabelRegistry:
    """Resolve frozen JSON Pointer paths to stable zh-CN business labels."""

    locale: Literal["zh-CN"] = "zh-CN"

    def label(self, field_path: Any) -> str:
        if not isinstance(field_path, str) or not field_path.startswith("/"):
            return "卷宗内容"
        exact = _EXACT_FIELD_LABELS.get(field_path)
        if exact is not None:
            return exact
        token = field_path[1:].split("/", 1)[0].replace("~1", "/").replace("~0", "~")
        return _FIELD_LABELS.get(token, "卷宗内容")


@dataclass(frozen=True, slots=True)
class ObjectLabel:
    target_id: str | None
    type_label: str
    name: str


class ObjectLabelResolver:
    """Resolve object names without ever falling back to internal identifiers."""

    def __init__(self, patch: Mapping[str, Any]) -> None:
        self._labels: dict[str, tuple[str | None, str | None]] = {}
        current = patch.get("object_labels")
        if isinstance(current, Mapping):
            for object_id, raw in current.items():
                if not isinstance(object_id, str) or not isinstance(raw, Mapping):
                    continue
                self._labels[object_id] = (
                    _public_name(raw.get("name")),
                    _clean_text(raw.get("object_type"), limit=80),
                )
        operations = patch.get("operations")
        if isinstance(operations, list):
            for operation in operations:
                if not isinstance(operation, Mapping):
                    continue
                for key in ("old_value", "new_value"):
                    frozen = operation.get(key)
                    if not isinstance(frozen, Mapping):
                        continue
                    object_id = frozen.get("id")
                    if not isinstance(object_id, str) or object_id in self._labels:
                        continue
                    self._labels[object_id] = (
                        _object_name(frozen),
                        _clean_text(frozen.get("object_type"), limit=80),
                    )

    def resolve(self, operation: Mapping[str, Any]) -> ObjectLabel:
        target_id_value = operation.get("object_id") or operation.get("target_object_key")
        target_id = str(target_id_value)[:160] if target_id_value else None
        current_name: str | None = None
        current_type: str | None = None
        if target_id is not None and target_id in self._labels:
            current_name, current_type = self._labels[target_id]
        frozen_name = next(
            (
                name
                for name in (
                    _object_name(operation.get("old_value")),
                    _object_name(operation.get("new_value")),
                )
                if name is not None
            ),
            None,
        )
        raw_type = (
            current_type
            or _clean_text(operation.get("object_type"), limit=80)
            or _clean_text(operation.get("target_collection"), limit=80)
            or ""
        )
        type_label = _TYPE_LABELS.get(raw_type, "卷宗内容")
        return ObjectLabel(
            target_id=target_id,
            type_label=type_label,
            name=current_name or frozen_name or type_label,
        )

    def reference_name(self, value: Mapping[str, Any]) -> str:
        object_id = value.get("object_id")
        if isinstance(object_id, str) and object_id in self._labels:
            name, object_type = self._labels[object_id]
            if name:
                return name
            if object_type:
                return _TYPE_LABELS.get(object_type, "卷宗内容")
        object_type = value.get("object_type")
        return _TYPE_LABELS.get(str(object_type), "卷宗内容")

    def reference_name_for_id(self, value: str) -> str | None:
        label = self._labels.get(value)
        if label is None:
            return None
        name, object_type = label
        return name or _TYPE_LABELS.get(str(object_type), "卷宗内容")


class ValueFormatter:
    """Format public before/after values without serializing database-shaped JSON."""

    def __init__(self, labels: ObjectLabelResolver) -> None:
        self._labels = labels
        self._fields = FieldLabelRegistry()

    def format(self, value: Any) -> dict[str, str]:
        if value is None:
            return {"kind": "empty", "text": "未填写"}
        if isinstance(value, bool):
            return {"kind": "boolean", "text": "是" if value else "否"}
        if isinstance(value, (int, float)):
            return {"kind": "number", "text": str(value)}
        if isinstance(value, str):
            if value.startswith("/"):
                return {"kind": "text", "text": self._fields.label(value)}
            reference_name = self._labels.reference_name_for_id(value)
            if reference_name is not None:
                return {"kind": "reference", "text": reference_name}
            if _INTERNAL_VALUE.fullmatch(value):
                return {"kind": "text", "text": "卷宗内容"}
            enum_label = _ENUM_LABELS.get(value)
            return {"kind": "text", "text": enum_label or _bounded(value, 4000)}
        if isinstance(value, Mapping):
            if isinstance(value.get("object_id"), str):
                return {
                    "kind": "reference",
                    "text": _bounded(self._labels.reference_name(value), 4000),
                }
            start = value.get("start")
            end = value.get("end")
            if isinstance(start, str) or isinstance(end, str):
                return {
                    "kind": "time_range",
                    "text": _bounded(f"{start or '未指定'} 至 {end or '未指定'}", 4000),
                }
            if value.get("kind") == "exact" and isinstance(value.get("value"), str):
                return {"kind": "text", "text": _bounded(str(value["value"]), 4000)}
            name = _object_name(value)
            if name:
                detail = next(
                    (
                        text
                        for key in (
                            "description",
                            "content",
                            "statement",
                            "proposition",
                            "reasoning_question",
                        )
                        if (text := _clean_text(value.get(key), limit=3600)) is not None
                    ),
                    None,
                )
                return {
                    "kind": "text",
                    "text": _bounded(f"{name}：{detail}" if detail else name, 4000),
                }
            return {"kind": "text", "text": "多项卷宗内容"}
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            if not value:
                return {"kind": "list", "text": "无"}
            parts = [self._list_item(item) for item in value[:20]]
            suffix = f"，另有 {len(value) - 20} 项" if len(value) > 20 else ""
            return {
                "kind": "list",
                "text": _bounded("、".join(parts) + suffix, 4000),
            }
        return {"kind": "text", "text": "卷宗内容"}

    def _list_item(self, value: Any) -> str:
        formatted = self.format(value)
        text = formatted["text"]
        return "一项卷宗内容" if text == "多项卷宗内容" else text


class ChangeGroupBuilder:
    """Build deterministic requested/support groups and flatten them for the v1 DTO."""

    def __init__(self, patch: Mapping[str, Any]) -> None:
        self._patch = patch
        self._labels = ObjectLabelResolver(patch)
        self._fields = FieldLabelRegistry()
        self._values = ValueFormatter(self._labels)

    def build(self) -> list[dict[str, Any]]:
        operations = self._patch.get("operations")
        operations = operations if isinstance(operations, list) else []
        grouped: dict[str, list[dict[str, Any]]] = {
            "requested": [],
            "consistency_support": [],
        }
        for operation in operations:
            if not isinstance(operation, Mapping):
                continue
            relationship: Literal["requested", "consistency_support"] = (
                "consistency_support"
                if operation.get("origin") == "closure_repair"
                else "requested"
            )
            grouped[relationship].append(self._change(operation, relationship))
        return [*grouped["requested"], *grouped["consistency_support"]]

    def _change(
        self,
        operation: Mapping[str, Any],
        relationship: Literal["requested", "consistency_support"],
    ) -> dict[str, Any]:
        operation_type = str(operation.get("operation_type") or "replace")
        kind = {
            "create_object": "create",
            "delete_object": "delete",
        }.get(operation_type, "update")
        target = self._labels.resolve(operation)
        change_id = int(operation["operation_id"])
        base: dict[str, Any] = {
            "change_id": change_id,
            "kind": kind,
            "relationship": relationship,
            "target": {
                "target_id": target.target_id,
                "type_label": target.type_label,
                "name": target.name,
            },
            "explanation": _change_explanation(kind, relationship),
        }
        if kind == "create":
            return {**base, "after": self._values.format(operation.get("new_value"))}
        if kind == "delete":
            return {**base, "before": self._values.format(operation.get("old_value"))}
        return {
            **base,
            "field_label": self._fields.label(operation.get("field_path")),
            "before": self._values.format(operation.get("old_value")),
            "after": self._values.format(operation.get("new_value")),
        }


def public_patch_set_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    changes = ChangeGroupBuilder(value).build()
    status = "stale" if value.get("is_stale") else str(value.get("status") or "rejected")
    if status not in {"pending", "applied", "undone", "stale", "rejected"}:
        status = "rejected"
    contains_delete = bool(value.get("contains_delete")) or any(
        change["kind"] == "delete" for change in changes
    )
    review_rule = "atomic" if value.get("review_mode") == "atomic" else "selective"
    requested_count = sum(change["relationship"] == "requested" for change in changes)
    support_count = len(changes) - requested_count
    summary_parts = [f"你要求的修改 {requested_count} 项"]
    if support_count:
        summary_parts.append(f"为保持一致性同步调整 {support_count} 项")
    summary = "；".join(summary_parts) + "。"
    impact_summary = f"共涉及 {len(changes)} 项卷宗修改"
    if contains_delete:
        impact_summary += "，其中包含删除"
    if support_count:
        impact_summary += f"，包含 {support_count} 项一致性调整"
    impact_summary += "。"
    return {
        "patch_id": int(value["patch_set_id"]),
        "title": "修改建议",
        "summary": summary,
        "status": status,
        "review_rule": review_rule,
        "base_revision": int(value.get("base_draft_revision") or 0),
        "impact": {
            "summary": impact_summary,
            "affected_change_count": len(changes),
            "has_deletions": contains_delete,
        },
        "changes": changes,
        "actions": {
            "can_simulate": status == "pending",
            "can_undo": status == "applied",
            "can_redo": status == "undone",
        },
    }


def public_patch_review_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    simulation_value = value.get("simulation")
    simulation = simulation_value if isinstance(simulation_value, Mapping) else {}
    can_apply = bool(value.get("can_apply", simulation.get("can_apply", False)))
    patch_id = int(value["patch_set_id"])
    authorization_value = simulation.get("authorization_required_finding_keys")
    authorization_keys = (
        sorted({str(key) for key in authorization_value if isinstance(key, str)})
        if isinstance(authorization_value, list)
        else []
    )
    warnings = [
        {
            "notice_id": public_warning_id(patch_id, key),
            "message": "这项修改会留下需要你明确接受的一致性风险。",
        }
        for key in authorization_keys[:200]
    ]
    blockers = _public_blockers(
        patch_id=patch_id,
        can_apply=can_apply,
        simulation=simulation,
        authorization_keys=frozenset(authorization_keys),
    )
    contains_delete = bool(value.get("contains_delete"))
    pending = str(value.get("status") or "pending") == "pending"
    raw_token = value.get("impact_hash")
    confirmation_token = (
        str(raw_token)[:256]
        if pending and contains_delete and isinstance(raw_token, str) and raw_token
        else None
    )
    return {
        "patch_id": patch_id,
        "can_apply": can_apply,
        "blockers": blockers,
        "warnings": warnings,
        "requires_author_confirmation": pending and bool(warnings or contains_delete),
        "confirmation_token": confirmation_token,
    }


def public_warning_id(patch_id: int, finding_key: str) -> str:
    digest = hashlib.sha256(
        f"m3.6-public-warning-v1\0{patch_id}\0{finding_key}".encode()
    ).hexdigest()[:32]
    return f"warning_{digest}"


def resolve_public_warning_ids(
    *,
    patch_id: int,
    accepted_warning_ids: Sequence[str],
    simulation: Mapping[str, Any],
) -> list[str]:
    raw_keys = simulation.get("authorization_required_finding_keys")
    keys = (
        [str(key) for key in raw_keys if isinstance(key, str)]
        if isinstance(raw_keys, list)
        else []
    )
    key_by_id = {public_warning_id(patch_id, key): key for key in keys}
    accepted = set(accepted_warning_ids)
    unknown = sorted(accepted - set(key_by_id))
    if unknown:
        raise ApplicationError(
            "public_warning_selection_invalid",
            "需要确认的影响已经变化，请重新模拟后再试。",
            status_code=409,
        )
    return [key_by_id[warning_id] for warning_id in accepted_warning_ids]


def _public_blockers(
    *,
    patch_id: int,
    can_apply: bool,
    simulation: Mapping[str, Any],
    authorization_keys: frozenset[str],
) -> list[dict[str, str]]:
    if can_apply:
        return []
    blockers: list[dict[str, str]] = []
    final_findings = simulation.get("final_findings")
    if isinstance(final_findings, list):
        for finding in final_findings:
            if not isinstance(finding, Mapping):
                continue
            key = finding.get("finding_key")
            severity = finding.get("severity")
            if (
                not isinstance(key, str)
                or key in authorization_keys
                or severity not in {"blocker", "error"}
            ):
                continue
            blockers.append(
                {
                    "notice_id": _opaque_notice_id("blocker", patch_id, key),
                    "message": "这项修改会破坏卷宗的一致性，当前不能应用。",
                }
            )
            if len(blockers) == 200:
                break
    if not blockers and not authorization_keys:
        reason = str(simulation.get("reason_code") or "review_failed")
        blockers.append(
            {
                "notice_id": _opaque_notice_id("blocker", patch_id, reason),
                "message": "修改后的卷宗未通过应用前检查。",
            }
        )
    return blockers


def _opaque_notice_id(prefix: str, patch_id: int, value: str) -> str:
    digest = hashlib.sha256(f"m3.6-{prefix}-v1\0{patch_id}\0{value}".encode()).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _change_explanation(kind: str, relationship: str) -> str:
    if relationship == "consistency_support":
        return "为保持卷宗前后一致，需要同步调整这项内容。"
    if kind == "create":
        return "这是你要求新增的卷宗内容。"
    if kind == "delete":
        return "这是你要求删除的卷宗内容。"
    return "这是你要求调整的卷宗内容。"


def _object_name(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("name", "title"):
        candidate = _public_name(value.get(key))
        if candidate:
            return candidate
    return None


def _public_name(value: Any) -> str | None:
    candidate = _clean_text(value, limit=240)
    if candidate is None or _INTERNAL_VALUE.fullmatch(candidate):
        return None
    return candidate


def _clean_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _bounded(value.strip(), limit)


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


__all__ = [
    "ChangeGroupBuilder",
    "FieldLabelRegistry",
    "ObjectLabelResolver",
    "PUBLIC_PATCH_LOCALE",
    "ValueFormatter",
    "public_patch_review_payload",
    "public_patch_set_payload",
    "public_warning_id",
    "resolve_public_warning_ids",
]
