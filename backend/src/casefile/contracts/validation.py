"""Load and validate versioned runtime mirrors of the CaseFile contract."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from casefile.contracts.object_types import COLLECTION_OBJECT_TYPES as COLLECTION_OBJECT_TYPES

CASEFILE_SCHEMA_VERSION = "2.0"
LEGACY_CASEFILE_SCHEMA_VERSIONS = frozenset({"1.0"})
SUPPORTED_CASEFILE_SCHEMA_VERSIONS = frozenset(
    {CASEFILE_SCHEMA_VERSION, *LEGACY_CASEFILE_SCHEMA_VERSIONS}
)

_EXTERNAL_REFERENCE_TYPES = {"source_fragment"}
_PUBLIC_ISSUE_LIMIT = 20
_PUBLIC_MESSAGE_LIMIT = 240
_REQUIRED_PROPERTY = re.compile(r"^'([^']+)' is a required property$")
_UNEXPECTED_PROPERTIES = re.compile(
    r"^Additional properties are not allowed \((.+) (?:was|were) unexpected\)$"
)
_WRONG_TYPE = re.compile(r"^.+ is not of type (.+)$")

_PUBLIC_INTEGRITY_MESSAGES = {
    "duplicate_object_id": "对象 ID 重复",
    "missing_reference": "引用的对象不存在",
    "reference_type_mismatch": "引用类型不匹配",
    "self_reference": "对象不能引用自身",
    "invalid_time_range": "结束时间不能早于开始时间",
    "invalid_wall_clock_time": "作品内时间格式无效",
    "time_precision_mismatch": "时间值与精度不一致",
    "invalid_relative_time": "相对时间约束无效",
    "duplicate_key": "同一集合中存在重复键",
    "competing_hypothesis_path_plan_missing": "案件蓝图缺少面向该竞争假设的独立推理路径",
    "competing_hypothesis_path_missing": "竞争假设缺少使用信息输入的对应推理路径",
    "competing_hypothesis_group_incomplete": "同一解答下的竞争假设集合不完整",
    "unscoped_evidence_assessment": "证据评估引用了当前竞争集合之外的信息",
    "missing_evidence_assessment": "竞争假设缺少必要的证据评估",
    "duplicate_evidence_assessment": "同一竞争假设包含重复的证据评估",
    "conclusion_hypothesis_scope_invalid": "结论只能选择同一核心问题下的假设",
    "conclusion_reasoning_path_scope_invalid": (
        "结论依据路径必须属于当前问题的必要推理链"
    ),
    "conclusion_required_slot_missing": "答案结论缺少必填答案槽位",
    "conclusion_basis_missing": "答案结论必须关联同题假设和有效推理路径",
    "undetermined_basis_missing": "未定论必须列出并存解释、分析路径和证据缺口",
    "duplicate_scene_id": "场景 ID 重复",
    "duplicate_floor_id": "同一场景内楼层 ID 重复",
    "duplicate_region_id": "同一场景内区域 ID 重复",
    "missing_scene_reference": "地点引用的场景不存在",
    "floor_without_scene": "引用楼层时必须同时指定场景",
    "floor_scene_mismatch": "楼层不属于地点引用的场景",
}


class ContractValidationError(ValueError):
    """A stable collection of structural or reference-integrity errors."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("CaseFile contract validation failed")
        self.errors = errors


@lru_cache(maxsize=2)
def load_casefile_schema(
    schema_version: str = CASEFILE_SCHEMA_VERSION,
) -> dict[str, Any]:
    """Load one generated CaseFile entry schema by document version."""

    return _load_schema(schema_version, "casefile.schema.json")


@lru_cache(maxsize=2)
def _validator(schema_version: str) -> Draft202012Validator:
    schemas = [_load_schema(schema_version, name) for name in _schema_names()]
    resources = [(cast(str, schema["$id"]), Resource.from_contents(schema)) for schema in schemas]
    registry: Registry[Any] = Registry().with_resources(resources)
    return Draft202012Validator(load_casefile_schema(schema_version), registry=registry)


def validate_casefile(document: dict[str, Any]) -> None:
    """Validate one supported JSON shape and deterministic cross-object invariants."""

    raw_schema_version = document.get("schema_version")
    schema_version = (
        raw_schema_version
        if isinstance(raw_schema_version, str)
        and raw_schema_version in SUPPORTED_CASEFILE_SCHEMA_VERSIONS
        else CASEFILE_SCHEMA_VERSION
    )

    schema_errors = [
        {
            "code": "schema_invalid",
            "path": _json_pointer(list(error.absolute_path)),
            "message": error.message,
        }
        for error in sorted(
            _validator(schema_version).iter_errors(document),
            key=lambda item: list(item.path),
        )
    ]
    if schema_errors:
        raise ContractValidationError(schema_errors)

    integrity_errors = _validate_integrity(document, schema_version=schema_version)
    if integrity_errors:
        raise ContractValidationError(integrity_errors)


def public_validation_issues(
    errors: list[dict[str, Any]],
    *,
    limit: int = _PUBLIC_ISSUE_LIMIT,
) -> list[dict[str, str]]:
    """Return bounded field-level issues without persisting candidate values."""

    issues: list[dict[str, str]] = []
    for error in errors[: max(0, limit)]:
        raw_code = str(error.get("code", "validation_failed"))
        code = raw_code if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", raw_code) else "validation_failed"
        raw_path = str(error.get("path", ""))
        path = raw_path[:512] if raw_path.startswith("/") or not raw_path else ""
        message = _public_validation_message(code, str(error.get("message", "")))
        issues.append({"code": code, "path": path, "message": message})
    return issues


def _public_validation_message(code: str, message: str) -> str:
    if code in _PUBLIC_INTEGRITY_MESSAGES:
        return _PUBLIC_INTEGRITY_MESSAGES[code]
    if code == "candidate_json_invalid":
        return "模型返回的 JSON 无法解析"
    if code == "missing":
        return "缺少必填字段"
    if code == "extra_forbidden":
        return "包含契约未允许的字段"

    required = _REQUIRED_PROPERTY.fullmatch(message)
    if required:
        return f"缺少必填字段 {required.group(1)}"[:_PUBLIC_MESSAGE_LIMIT]
    unexpected = _UNEXPECTED_PROPERTIES.fullmatch(message)
    if unexpected:
        return f"包含契约未允许的字段 {unexpected.group(1)}"[:_PUBLIC_MESSAGE_LIMIT]
    wrong_type = _WRONG_TYPE.fullmatch(message)
    if wrong_type:
        return f"字段类型应为 {wrong_type.group(1)}"[:_PUBLIC_MESSAGE_LIMIT]
    if " is not one of " in message or " should be " in message:
        return "字段值不在契约允许范围内"
    if " does not match " in message or "String should match pattern" in message:
        return "字段格式不符合契约要求"
    if "too short" in message or "at least" in message:
        return "字段长度或数量低于契约下限"
    if "too long" in message or "at most" in message:
        return "字段长度或数量超过契约上限"
    return "字段不符合 CaseFile 结构约束"


def _validate_integrity(
    document: dict[str, Any],
    *,
    schema_version: str,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    registry: dict[str, str] = {document["casefile_id"]: "casefile"}

    for collection_name, object_type in COLLECTION_OBJECT_TYPES.items():
        for index, item in enumerate(document[collection_name]):
            object_id = item["id"]
            if object_id in registry:
                errors.append(
                    _error(
                        "duplicate_object_id",
                        f"/{collection_name}/{index}/id",
                        f"object_id {object_id!r} is already registered",
                    )
                )
            else:
                registry[object_id] = object_type

    for path, reference in _walk_object_refs(document):
        expected_type = reference["object_type"]
        object_id = reference["object_id"]
        if expected_type in _EXTERNAL_REFERENCE_TYPES:
            continue
        actual_type = registry.get(object_id)
        if actual_type is None:
            errors.append(
                _error(
                    "missing_reference",
                    path,
                    f"object_id {object_id!r} does not exist",
                )
            )
        elif actual_type != expected_type:
            errors.append(
                _error(
                    "reference_type_mismatch",
                    path,
                    f"expected {expected_type}, got {actual_type}",
                )
            )

    for location_index, location in enumerate(document["locations"]):
        _optional_declared_type(
            errors,
            location["parent_ref"],
            "location",
            f"/locations/{location_index}/parent_ref",
        )
        for ref_index, reference in enumerate(location["adjacency_refs"]):
            _require_declared_type(
                errors,
                reference,
                "location",
                f"/locations/{location_index}/adjacency_refs/{ref_index}",
            )
            if reference["object_id"] == location["id"]:
                errors.append(
                    _error(
                        "self_reference",
                        f"/locations/{location_index}/adjacency_refs/{ref_index}",
                        "a location cannot be adjacent to itself",
                    )
                )
        for travel_index, travel_time in enumerate(location["travel_times"]):
            _require_declared_type(
                errors,
                travel_time["to_ref"],
                "location",
                f"/locations/{location_index}/travel_times/{travel_index}/to_ref",
            )

    scene_ids: set[str] = set()
    floor_ids_by_scene: dict[str, set[str]] = {}
    for scene_index, scene in enumerate(document.get("spatial_scenes", [])):
        scene_id = scene["scene_id"]
        scene_base = f"/spatial_scenes/{scene_index}"
        if scene_id in scene_ids:
            errors.append(
                _error(
                    "duplicate_scene_id",
                    f"{scene_base}/scene_id",
                    f"scene_id {scene_id!r} is already declared",
                )
            )
        scene_ids.add(scene_id)
        floor_ids: set[str] = set()
        region_ids: set[str] = set()
        for floor_index, floor in enumerate(scene.get("floors", [])):
            floor_id = floor["floor_id"]
            if floor_id in floor_ids:
                errors.append(
                    _error(
                        "duplicate_floor_id",
                        f"{scene_base}/floors/{floor_index}/floor_id",
                        f"floor_id {floor_id!r} is already declared in scene {scene_id!r}",
                    )
                )
            floor_ids.add(floor_id)
        for region_index, region in enumerate(scene.get("regions", [])):
            region_id = region["region_id"]
            if region_id in region_ids:
                errors.append(
                    _error(
                        "duplicate_region_id",
                        f"{scene_base}/regions/{region_index}/region_id",
                        f"region_id {region_id!r} is already declared in scene {scene_id!r}",
                    )
                )
            region_ids.add(region_id)
        floor_ids_by_scene[scene_id] = floor_ids

    for location_index, location in enumerate(document["locations"]):
        position = location.get("spatial_position")
        if not position or position.get("coordinate_system") != "schematic":
            continue
        position_base = f"/locations/{location_index}/spatial_position"
        scene_id = position.get("scene_id")
        floor_id = position.get("floor_id")
        if scene_id and scene_id not in scene_ids:
            errors.append(
                _error(
                    "missing_scene_reference",
                    f"{position_base}/scene_id",
                    f"scene_id {scene_id!r} is not declared in spatial_scenes",
                )
            )
        if floor_id and not scene_id:
            errors.append(
                _error(
                    "floor_without_scene",
                    f"{position_base}/floor_id",
                    "floor_id requires a scene_id on the same schematic position",
                )
            )
        if scene_id and floor_id and floor_id not in floor_ids_by_scene.get(scene_id, set()):
            errors.append(
                _error(
                    "floor_scene_mismatch",
                    f"{position_base}/floor_id",
                    f"floor_id {floor_id!r} is not declared in scene {scene_id!r}",
                )
            )
    for event_index, event in enumerate(document["events"]):
        _optional_declared_type(
            errors,
            event["location_ref"],
            "location",
            f"/events/{event_index}/location_ref",
        )
        for field in ("participant_refs", "observed_by_refs"):
            _require_list_type(errors, event[field], "entity", f"/events/{event_index}/{field}")
        if schema_version == "1.0":
            start = datetime.fromisoformat(event["time"]["start"])
            end_value = event["time"]["end"]
            if end_value is not None and datetime.fromisoformat(end_value) < start:
                errors.append(
                    _error(
                        "invalid_time_range",
                        f"/events/{event_index}/time/end",
                        "event end cannot be before start",
                    )
                )
        else:
            _validate_temporal_position_v2(errors, event, event_index)
    for entity_index, entity in enumerate(document["entities"]):
        for state_index, state in enumerate(entity["knowledge_states"]):
            base = f"/entities/{entity_index}/knowledge_states/{state_index}"
            _optional_declared_type(
                errors,
                state["as_of_event_ref"],
                "event",
                f"{base}/as_of_event_ref",
            )
            _require_list_type(
                errors, state["knows_refs"], "information_unit", f"{base}/knows_refs"
            )
            for field in ("believes_refs", "false_belief_refs"):
                _require_list_type(errors, state[field], "claim", f"{base}/{field}")
    for unit_index, unit in enumerate(document["information_units"]):
        base = f"/information_units/{unit_index}"
        _optional_declared_type(
            errors, unit["source_event_ref"], "event", f"{base}/source_event_ref"
        )
        for field in ("supports_claim_refs", "refutes_claim_refs"):
            _require_list_type(errors, unit[field], "claim", f"{base}/{field}")
        _require_list_type(
            errors,
            unit["availability"]["perspective_refs"],
            "entity",
            f"{base}/availability/perspective_refs",
        )
        _require_list_type(
            errors,
            unit["availability"]["alternative_path_refs"],
            "reasoning_path",
            f"{base}/availability/alternative_path_refs",
        )
    for claim_index, claim in enumerate(document["claims"]):
        base = f"/claims/{claim_index}"
        for field in ("support_refs", "refute_refs"):
            _require_list_type(errors, claim[field], "information_unit", f"{base}/{field}")
        _require_list_type(
            errors, claim["dependency_claim_refs"], "claim", f"{base}/dependency_claim_refs"
        )
    for hypothesis_index, hypothesis in enumerate(document["hypotheses"]):
        base = f"/hypotheses/{hypothesis_index}"
        _require_declared_type(
            errors,
            hypothesis["target_resolution_ref"],
            "resolution_spec",
            f"{base}/target_resolution_ref",
        )
        _require_list_type(
            errors, hypothesis["required_claim_refs"], "claim", f"{base}/required_claim_refs"
        )
        _require_list_type(
            errors,
            hypothesis["competing_hypothesis_refs"],
            "hypothesis",
            f"{base}/competing_hypothesis_refs",
        )
        assessment_information_ids: set[str] = set()
        for assessment_index, assessment in enumerate(hypothesis.get("evidence_assessments", [])):
            assessment_path = f"{base}/evidence_assessments/{assessment_index}"
            information_ref = assessment["information_ref"]
            _require_declared_type(
                errors,
                information_ref,
                "information_unit",
                f"{assessment_path}/information_ref",
            )
            information_id = information_ref["object_id"]
            if information_id in assessment_information_ids:
                errors.append(
                    _error(
                        "duplicate_key",
                        f"{assessment_path}/information_ref",
                        "同一假设不能重复评估同一信息。",
                    )
                )
            assessment_information_ids.add(information_id)
    for path_index, reasoning_path in enumerate(document["reasoning_paths"]):
        _unique_string(
            errors,
            reasoning_path["steps"],
            "step_id",
            f"/reasoning_paths/{path_index}/steps",
        )
    for spec_index, resolution in enumerate(document["resolution_specs"]):
        _unique_string(
            errors,
            resolution["required_slots"],
            "slot_id",
            f"/resolution_specs/{spec_index}/required_slots",
        )
        conclusion = resolution.get("conclusion")
        if conclusion is not None:
            _validate_resolution_conclusion(
                errors,
                document,
                resolution,
                conclusion,
                spec_index,
            )
    return errors


def _validate_resolution_conclusion(
    errors: list[dict[str, Any]],
    document: dict[str, Any],
    resolution: dict[str, Any],
    conclusion: dict[str, Any],
    spec_index: int,
) -> None:
    base = f"/resolution_specs/{spec_index}/conclusion"
    _require_list_type(
        errors,
        conclusion["selected_hypothesis_refs"],
        "hypothesis",
        f"{base}/selected_hypothesis_refs",
    )
    _require_list_type(
        errors,
        conclusion["supporting_reasoning_path_refs"],
        "reasoning_path",
        f"{base}/supporting_reasoning_path_refs",
    )
    _unique_string(errors, conclusion["values"], "slot_id", f"{base}/values")
    slot_by_id = {slot["slot_id"]: slot for slot in resolution["required_slots"]}
    for value_index, entry in enumerate(conclusion["values"]):
        slot = slot_by_id.get(entry["slot_id"])
        path = f"{base}/values/{value_index}"
        if slot is None:
            errors.append(
                _error("conclusion_slot_unknown", f"{path}/slot_id", "结论答案引用了未声明的槽位")
            )
        elif not _resolution_slot_value_matches(slot["value_type"], entry["value"]):
            errors.append(
                _error("conclusion_slot_type_mismatch", f"{path}/value", "结论答案与槽位类型不匹配")
            )
    selected_ids = {ref["object_id"] for ref in conclusion["selected_hypothesis_refs"]}
    hypothesis_by_id = {item["id"]: item for item in document["hypotheses"]}
    for ref_index, reference in enumerate(conclusion["selected_hypothesis_refs"]):
        object_id = reference["object_id"]
        hypothesis = hypothesis_by_id.get(object_id)
        if hypothesis is None or (
            hypothesis["target_resolution_ref"]["object_id"] != resolution["id"]
        ):
            errors.append(
                _error(
                    "conclusion_hypothesis_scope_invalid",
                    f"{base}/selected_hypothesis_refs/{ref_index}",
                    "结论只能选择同一核心问题下的假设",
                )
            )
    valid_target_ids = resolution_conclusion_target_ids(
        document,
        resolution,
        selected_ids,
    )
    path_by_id = {item["id"]: item for item in document["reasoning_paths"]}
    for ref_index, reference in enumerate(conclusion["supporting_reasoning_path_refs"]):
        reasoning_path = path_by_id.get(reference["object_id"])
        if reasoning_path is None or (
            reasoning_path["target_ref"]["object_id"] not in valid_target_ids
            or not reasoning_path["required_for_resolution"]
        ):
            errors.append(
                _error(
                    "conclusion_reasoning_path_scope_invalid",
                    f"{base}/supporting_reasoning_path_refs/{ref_index}",
                    "结论依据路径必须属于当前问题、所选假设或其必要 Claim 链，且标记为解答所需",
                )
            )
    if conclusion["outcome"] == "answer":
        value_ids = {entry["slot_id"] for entry in conclusion["values"]}
        for slot in resolution["required_slots"]:
            if slot["required"] and slot["slot_id"] not in value_ids:
                errors.append(
                    _error(
                        "conclusion_required_slot_missing",
                        f"{base}/values",
                        f"答案结论缺少必填槽位 {slot['slot_id']}",
                    )
                )
        if not selected_ids or not conclusion["supporting_reasoning_path_refs"]:
            errors.append(
                _error("conclusion_basis_missing", base, "答案结论必须关联假设和推理路径")
            )
    elif (
        not selected_ids
        or not conclusion["supporting_reasoning_path_refs"]
        or not conclusion["unresolved_gaps"]
    ):
        errors.append(
            _error("undetermined_basis_missing", base, "未定论必须列出并存解释、分析路径和证据缺口")
        )


def resolution_conclusion_target_ids(
    document: dict[str, Any],
    resolution: dict[str, Any],
    selected_hypothesis_ids: set[str],
) -> set[str]:
    """Return valid reasoning-path targets for one Resolution conclusion."""

    resolution_id = resolution["id"]
    hypotheses = {
        item["id"]: item
        for item in document["hypotheses"]
        if item["id"] in selected_hypothesis_ids
        and item["target_resolution_ref"]["object_id"] == resolution_id
    }
    claim_ids = {
        ref["object_id"]
        for ref in [
            *resolution["required_claim_refs"],
            *(
                ref
                for hypothesis in hypotheses.values()
                for ref in hypothesis["required_claim_refs"]
            ),
        ]
    }
    claims = {item["id"]: item for item in document["claims"]}
    pending = list(claim_ids)
    while pending:
        claim = claims.get(pending.pop())
        if claim is None:
            continue
        for reference in claim["dependency_claim_refs"]:
            dependency_id = reference["object_id"]
            if dependency_id not in claim_ids:
                claim_ids.add(dependency_id)
                pending.append(dependency_id)
    return {resolution_id, *hypotheses, *claim_ids}


def _resolution_slot_value_matches(value_type: str, value: Any) -> bool:
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if value_type == "text":
        return isinstance(value, str)
    if value_type == "object_ref":
        return isinstance(value, dict)
    if value_type == "entity_or_claim_ref":
        return isinstance(value, dict) and value.get("object_type") in {"entity", "claim"}
    if value_type == "text_or_claim_ref":
        return isinstance(value, str) or (
            isinstance(value, dict) and value.get("object_type") == "claim"
        )
    return False


def _require_list_type(
    errors: list[dict[str, Any]],
    references: list[dict[str, str]],
    expected_type: str,
    path: str,
) -> None:
    for index, reference in enumerate(references):
        _require_declared_type(errors, reference, expected_type, f"{path}/{index}")


def _validate_temporal_position_v2(
    errors: list[dict[str, Any]],
    event: dict[str, Any],
    event_index: int,
) -> None:
    time = event["time"]
    base = f"/events/{event_index}/time"
    kind = time["kind"]
    if kind in {"exact", "approximate"}:
        _validate_wall_clock_value(
            errors,
            time["value"],
            time["precision"],
            f"{base}/value",
        )
        return
    if kind == "range":
        start = _validate_wall_clock_value(
            errors,
            time["start"],
            time["precision"],
            f"{base}/start",
        )
        end = _validate_wall_clock_value(
            errors,
            time["end"],
            time["precision"],
            f"{base}/end",
        )
        if start is not None and end is not None and end < start:
            errors.append(
                _error(
                    "invalid_time_range",
                    f"{base}/end",
                    "event end cannot be before start",
                )
            )
        return
    if kind == "relative":
        anchor = time["anchor_event_ref"]
        _require_declared_type(errors, anchor, "event", f"{base}/anchor_event_ref")
        if anchor["object_id"] == event["id"]:
            errors.append(
                _error(
                    "self_reference",
                    f"{base}/anchor_event_ref",
                    "an event cannot use itself as a relative-time anchor",
                )
            )
        if time["relation"] == "same_time" and time["offset_minutes"] not in {None, 0}:
            errors.append(
                _error(
                    "invalid_relative_time",
                    f"{base}/offset_minutes",
                    "same_time cannot carry a non-zero offset",
                )
            )


def _validate_wall_clock_value(
    errors: list[dict[str, Any]],
    value: str,
    precision: str,
    path: str,
) -> datetime | None:
    actual_precision = _wall_clock_precision(value)
    if actual_precision != precision:
        errors.append(
            _error(
                "time_precision_mismatch",
                path,
                f"expected {precision} precision, got {actual_precision or 'invalid'}",
            )
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors.append(_error("invalid_wall_clock_time", path, "invalid wall-clock value"))
        return None
    if parsed.tzinfo is not None:
        errors.append(
            _error("invalid_wall_clock_time", path, "wall-clock time must not include timezone")
        )
        return None
    return parsed


def _wall_clock_precision(value: str) -> str | None:
    if "T" not in value:
        return "day"
    clock = value.split("T", 1)[1]
    colon_count = clock.count(":")
    if colon_count == 0:
        return "hour"
    if colon_count == 1:
        return "minute"
    if colon_count == 2:
        return "second"
    return None


def _optional_declared_type(
    errors: list[dict[str, Any]],
    reference: dict[str, str] | None,
    expected_type: str,
    path: str,
) -> None:
    if reference is not None:
        _require_declared_type(errors, reference, expected_type, path)


def _require_declared_type(
    errors: list[dict[str, Any]],
    reference: dict[str, str],
    expected_type: str,
    path: str,
) -> None:
    if reference["object_type"] != expected_type:
        errors.append(
            _error(
                "reference_type_mismatch",
                path,
                f"expected {expected_type}, got {reference['object_type']}",
            )
        )


def _walk_object_refs(value: Any, path: str = "") -> Iterator[tuple[str, dict[str, str]]]:
    if isinstance(value, dict):
        if set(value) == {"object_type", "object_id"}:
            yield path, cast(dict[str, str], value)
            return
        for key, item in value.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from _walk_object_refs(item, f"{path}/{escaped}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_object_refs(item, f"{path}/{index}")


def _unique_string(
    errors: list[dict[str, Any]], items: list[dict[str, Any]], field: str, path: str
) -> None:
    _unique_value(errors, items, field, path, "duplicate_key")


def _unique_value(
    errors: list[dict[str, Any]],
    items: list[dict[str, Any]],
    field: str,
    path: str,
    code: str,
) -> None:
    seen: set[Any] = set()
    for index, item in enumerate(items):
        value = item[field]
        if value in seen:
            errors.append(
                _error(code, f"{path}/{index}/{field}", f"{field} {value!r} is duplicated")
            )
        seen.add(value)


def _schema_names() -> tuple[str, ...]:
    return ("casefile.schema.json", "common.schema.json", "objects.schema.json")


def _load_schema(schema_version: str, name: str) -> dict[str, Any]:
    if schema_version not in SUPPORTED_CASEFILE_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported CaseFile schema version: {schema_version}")
    directory = "v1" if schema_version == "1.0" else "v2"
    resource = files("casefile.contracts.schemas").joinpath(directory, "casefile", name)
    return cast(dict[str, Any], json.loads(resource.read_text(encoding="utf-8")))


def _json_pointer(parts: list[Any]) -> str:
    if not parts:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _error(code: str, path: str, message: str) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message}
