"""Pure creator-language and frozen Brief quality validators."""

from __future__ import annotations

import re
from typing import Any

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    BLUEPRINT_COLLECTIONS,
    DOMAIN_COLLECTIONS,
    CaseBlueprintV1,
)
from casefile.contracts import ContractValidationError, validate_casefile

_CREATOR_TEXT_FIELDS = frozenset(
    {
        "accepted_answer_texts",
        "access_rules",
        "acquisition_conditions",
        "aliases",
        "capabilities",
        "content",
        "description",
        "goals",
        "name",
        "proposition",
        "purpose",
        "rationale",
        "reason",
        "reasoning_question",
        "secrets",
        "statement",
        "tags",
        "title",
        "traits",
        "visibility_rules",
    }
)
_HAN_TEXT = re.compile(r"[\u3400-\u9fff]")
_LATIN_TEXT = re.compile(r"[A-Za-z]")


def _brief_quality_requirement_issues(
    brief: dict[str, Any],
    candidate: dict[str, Any],
    *,
    schema_id: str,
) -> list[dict[str, Any]]:
    """Translate machine-readable Brief quality requirements into repair issues.

    Acceptance scenarios previously checked these properties only after a
    successful candidate was persisted. Putting them in the quality gate makes
    them part of the recoverable contract: the gate fails, the issue is routed
    to the owning component, and the normal repair/worker budget retries it.
    """

    requirements = brief.get("quality_requirements")
    if not isinstance(requirements, dict):
        return []
    issues: list[dict[str, Any]] = []

    temporal_time_kinds = requirements.get("temporal_time_kinds")
    if isinstance(temporal_time_kinds, list) and temporal_time_kinds:
        required = [str(kind) for kind in temporal_time_kinds]
        present = {
            event.get("time", {}).get("kind")
            for event in candidate.get("events", [])
            if isinstance(event, dict)
        }
        missing = [kind for kind in required if kind not in present]
        if missing:
            issues.append(
                {
                    "code": "frozen_temporal_time_kinds_missing",
                    "path": "/events",
                    "message": (
                        "事件时间必须同时包含 Brief 冻结要求的时间种类："
                        + "、".join(sorted(missing))
                        + "。"
                    ),
                    "component_id": "temporal_structure_planner",
                    "failure_layer": "temporal_grounding",
                    "schema_id": schema_id,
                }
            )

    if requirements.get("spatial_scene_topology") is True:
        locations = candidate.get("locations", [])
        has_schematic = any(
            isinstance(item, dict)
            and item.get("spatial_position", {}).get("coordinate_system") == "schematic"
            for item in locations
        )
        has_topology = any(
            isinstance(item, dict)
            and (
                item.get("parent_ref") is not None
                or bool(item.get("adjacency_refs"))
                or bool(item.get("travel_times"))
            )
            for item in locations
        )
        if not has_schematic or not has_topology:
            issues.append(
                {
                    "code": "frozen_spatial_scene_topology_missing",
                    "path": "/locations",
                    "message": (
                        "地点必须使用 schematic 示意坐标"
                        "（spatial_position.coordinate_system 为 schematic），"
                        "且至少包含一条指向其他地点的拓扑关系"
                        "（parent_ref、adjacency_refs 或 travel_times，"
                        "引用不得指向自身）。"
                    ),
                    "component_id": "story_world",
                    "failure_layer": "spatial_grounding",
                    "schema_id": schema_id,
                }
            )
    return issues[:50]


def _creator_chinese_issues(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject English-only creator-facing prose while preserving machine values."""

    issues: list[dict[str, Any]] = []

    def add_issue(path: str, component_id: str) -> None:
        issues.append(
            {
                "code": "generated_creator_text_not_simplified_chinese",
                "path": path,
                "message": "面向作者的自然语言字段必须使用简体中文，不能输出纯英文。",
                "component_id": component_id,
                "failure_layer": "creator_language",
                "schema_id": "casefile-v2",
            }
        )

    def visit(value: object, path: str, component_id: str) -> None:
        if isinstance(value, str):
            if value.strip() and _LATIN_TEXT.search(value) and not _HAN_TEXT.search(value):
                add_issue(path, component_id)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}/{index}", component_id)

    def scan(value: object, path: str, component_id: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                scan(item, f"{path}/{index}", component_id)
            return
        if not isinstance(value, dict):
            return
        for field_name, field_value in value.items():
            field_path = f"{path}/{field_name}"
            if field_name in _CREATOR_TEXT_FIELDS:
                visit(field_value, field_path, component_id)
            elif isinstance(field_value, (dict, list)):
                scan(field_value, field_path, component_id)

    root_title = candidate.get("title")
    visit(root_title, "/title", "case_blueprint_planner")
    for component_id, collections in DOMAIN_COLLECTIONS.items():
        for collection in collections:
            values = candidate.get(collection)
            if not isinstance(values, list):
                continue
            for index, item in enumerate(values):
                scan(item, f"/{collection}/{index}", component_id)
    for index, notice in enumerate(candidate.get("content_notices", [])):
        scan(notice, f"/content_notices/{index}", "resolution_governance")
    return issues[:50]


def _blueprint_creator_chinese_issues(
    blueprint: CaseBlueprintV1,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def inspect(value: str, path: str) -> None:
        if value.strip() and _LATIN_TEXT.search(value) and not _HAN_TEXT.search(value):
            issues.append(
                {
                    "code": "generated_creator_text_not_simplified_chinese",
                    "path": path,
                    "message": "Blueprint 面向作者的标题和用途必须使用简体中文。",
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "creator_language",
                    "schema_id": "case-blueprint-v1",
                }
            )

    inspect(blueprint.title, "/title")
    for collection in BLUEPRINT_COLLECTIONS:
        for index, item in enumerate(getattr(blueprint, collection)):
            inspect(item.title, f"/{collection}/{index}/title")
            inspect(item.purpose, f"/{collection}/{index}/purpose")
    return issues[:50]


def final_candidate_issues(
    candidate: dict[str, Any],
    brief: dict[str, Any],
    *,
    schema_id: str,
    language_gate: bool,
) -> list[dict[str, Any]]:
    """Preserve final-gate ordering and first failing group semantics."""
    try:
        validate_casefile(candidate)
        description_issues: list[dict[str, Any]] = []
        for component_id, collections in DOMAIN_COLLECTIONS.items():
            for collection in collections:
                for index, item in enumerate(candidate.get(collection, [])):
                    description = item.get("description") if isinstance(item, dict) else None
                    if not isinstance(description, str) or not description.strip():
                        description_issues.append(
                            {
                                "code": "generated_description_missing",
                                "path": f"/{collection}/{index}/description",
                                "message": "Agent 生成的对象必须填写非空描述。",
                                "component_id": component_id,
                                "failure_layer": "description_gate",
                                "schema_id": schema_id,
                            }
                        )
        if description_issues:
            raise ContractValidationError(description_issues)
        quality_requirement_issues = _brief_quality_requirement_issues(
            brief,
            candidate,
            schema_id=schema_id,
        )
        if quality_requirement_issues:
            raise ContractValidationError(quality_requirement_issues)
        if language_gate:
            creator_language_issues = _creator_chinese_issues(candidate)
            if creator_language_issues:
                raise ContractValidationError(creator_language_issues)
        expected_mode = brief.get("conclusion_mode")
        mismatched = [
            index
            for index, resolution in enumerate(candidate.get("resolution_specs", []))
            if resolution.get("conclusion_mode") != expected_mode
        ]
        if mismatched:
            raise ContractValidationError(
                [
                    {
                        "code": "frozen_conclusion_mode_mismatch",
                        "path": f"/resolution_specs/{index}/conclusion_mode",
                        "message": "解答模式与冻结 Brief 不一致。",
                        "component_id": "resolution_governance",
                        "failure_layer": "frozen_context",
                        "schema_id": schema_id,
                    }
                    for index in mismatched
                ]
            )
    except ContractValidationError as error:
        return list(error.errors)
    return []
