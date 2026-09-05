"""Pure blueprint, story, and evidence validation helpers for v8+ workflows."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    EvidenceLogicIRV1,
    EvidenceLogicIRV2,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import (
    CoordinatePairV1,
    RelativeTemporalPositionIRV2,
    StoryWorldIRV2,
    Wgs84SpatialPositionIRV2,
)
from casefile.agent_runtime.brief_to_draft_v12.contracts import (
    StoryWorldIRV3,
)
from casefile.agent_runtime.models import GenerationRequest

ComponentCall = Callable[
    [str, str, type[BaseModel], str, str, str],
    Awaitable[tuple[dict[str, Any], dict[str, Any]]],
]


def _request_repair_issues(request: GenerationRequest) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for feedback in request.repair_feedback:
        raw_issues = feedback.get("issues")
        if not isinstance(raw_issues, list):
            continue
        for issue in raw_issues:
            if isinstance(issue, dict):
                normalized = _diagnostic_issue(issue)
                key = (
                    str(normalized.get("component_id") or ""),
                    str(normalized.get("code") or ""),
                    str(normalized.get("path") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                issues.append(normalized)
                if len(issues) >= 50:
                    return issues
    return issues


def _repair_issues_for_component(
    issues: list[dict[str, Any]], component_id: str
) -> list[dict[str, Any]] | None:
    selected = [issue for issue in issues if issue.get("component_id") == component_id]
    return selected or None


def _blueprint_path_plan_issues(
    blueprint: CaseBlueprintV1,
    *,
    explicit_targets: bool,
) -> list[dict[str, Any]]:
    """Blueprint competition-path gate; v15+ requires explicit target plans."""

    if explicit_targets:
        return _v15_blueprint_path_issues(blueprint)
    return _v11_blueprint_issues(blueprint)


def _blueprint_competition_groups(blueprint: CaseBlueprintV1) -> list[set[str]]:
    """Derive competing-hypothesis groups from blueprint dependencies."""

    hypothesis_keys = {item.local_key for item in blueprint.hypotheses}
    resolution_keys = {item.local_key for item in blueprint.resolution_specs}
    groups: list[set[str]] = []

    def add_group(values: set[str]) -> None:
        if len(values) < 2:
            return
        merged = set(values)
        remaining: list[set[str]] = []
        for existing in groups:
            if existing & merged:
                merged.update(existing)
            else:
                remaining.append(existing)
        remaining.append(merged)
        groups[:] = remaining

    for path in blueprint.reasoning_paths:
        add_group(set(path.dependency_keys) & hypothesis_keys)
    for resolution in blueprint.resolution_specs:
        add_group(set(resolution.dependency_keys) & hypothesis_keys)
    hypotheses_by_resolution: dict[str, set[str]] = {}
    for hypothesis in blueprint.hypotheses:
        for resolution_key in set(hypothesis.dependency_keys) & resolution_keys:
            hypotheses_by_resolution.setdefault(resolution_key, set()).add(hypothesis.local_key)
    for values in hypotheses_by_resolution.values():
        add_group(values)
    return groups


def _v11_blueprint_issues(blueprint: CaseBlueprintV1) -> list[dict[str, Any]]:
    """Reject competition plans that no Evidence output can satisfy (v11-v14)."""

    issues: list[dict[str, Any]] = []
    for group in _blueprint_competition_groups(blueprint):
        for hypothesis_key in sorted(group):
            has_dedicated_path = _blueprint_has_hypothesis_path(
                blueprint,
                hypothesis_key,
            )
            if has_dedicated_path:
                continue
            issues.append(
                {
                    "code": "competing_hypothesis_path_plan_missing",
                    "path": f"/reasoning_paths/{hypothesis_key}",
                    "message": "Planner 必须为每个竞争假设规划一条以该假设为目标的独立推理路径。",
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "blueprint_semantics",
                    "schema_id": "case-blueprint-v1",
                    "ir_path": f"/hypotheses/{hypothesis_key}",
                }
            )
    return issues


def _v15_blueprint_path_issues(blueprint: CaseBlueprintV1) -> list[dict[str, Any]]:
    """Require explicit targeted path plans and one hypothesis per Resolution (v15+)."""

    issues: list[dict[str, Any]] = []
    for group in _blueprint_competition_groups(blueprint):
        for hypothesis_key in sorted(group):
            if _blueprint_has_explicit_target_path(blueprint, hypothesis_key):
                continue
            issues.append(
                {
                    "code": "competing_hypothesis_path_plan_missing",
                    "path": f"/reasoning_paths/{hypothesis_key}",
                    "message": (
                        "Planner 必须为每个竞争假设规划一条以该假设为 target "
                        "且声明所需信息输入的推理路径。"
                    ),
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "blueprint_semantics",
                    "schema_id": "case-blueprint-v1",
                    "ir_path": f"/hypotheses/{hypothesis_key}",
                }
            )
    for resolution in blueprint.resolution_specs:
        if any(
            resolution.local_key in hypothesis.dependency_keys
            for hypothesis in blueprint.hypotheses
        ):
            continue
        issues.append(
            {
                "code": "resolution_hypothesis_plan_missing",
                "path": f"/resolution_specs/{resolution.local_key}",
                "message": (
                    "每个 resolution_specs 必须至少规划一个以它为 dependency 的 hypothesis；"
                    "结论校验要求至少选择一个同题假设。"
                ),
                "component_id": "case_blueprint_planner",
                "failure_layer": "blueprint_semantics",
                "schema_id": "case-blueprint-v1",
                "ir_path": f"/resolution_specs/{resolution.local_key}",
            }
        )
    information_keys = {item.local_key for item in blueprint.information_units}
    for group in _blueprint_competition_groups(blueprint):
        covered: set[str] = set()
        for path in blueprint.reasoning_paths:
            if path.target_key in group:
                covered.update(path.required_information_keys)
        if information_keys and covered != information_keys:
            issues.append(
                {
                    "code": "competition_information_coverage_incomplete",
                    "path": "/reasoning_paths",
                    "message": (
                        "竞争假设路径的 required_information_keys 并集必须覆盖全部 "
                        "information_units，否则比较矩阵列不完整。"
                    ),
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "blueprint_semantics",
                    "schema_id": "case-blueprint-v1",
                    "ir_path": "/information_units",
                }
            )
    return issues


def _v16_blueprint_relationship_coverage_issues(
    blueprint: CaseBlueprintV1,
) -> list[dict[str, Any]]:
    """Require the plan itself to reserve every semantic entity edge."""

    entity_keys = {item.local_key for item in blueprint.entities}
    if len(entity_keys) < 2:
        return []

    issues: list[dict[str, Any]] = []
    planned_pairs: set[tuple[str, str]] = set()
    generic_terms = ("有关联", "关联关系", "共同参与", "同场出现", "事件关联")
    for relationship in blueprint.relationships:
        endpoints = sorted(set(relationship.dependency_keys) & entity_keys)
        if len(endpoints) != 2:
            issues.append(
                {
                    "code": "relationship_endpoint_plan_invalid",
                    "path": f"/relationships/{relationship.local_key}/dependency_keys",
                    "message": (
                        "每个 relationship 蓝图对象必须在 dependency_keys 中逐字列出"
                        "恰好两个实体端点，Story 才能据此生成 from_key 与 to_key。"
                    ),
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "relationship_coverage",
                    "schema_id": "case-blueprint-v1",
                    "ir_path": f"/relationships/{relationship.local_key}",
                }
            )
        else:
            planned_pairs.add((endpoints[0], endpoints[1]))
        normalized_title = "".join(relationship.title.split())
        if any(term in normalized_title for term in generic_terms):
            issues.append(
                {
                    "code": "generic_relationship_plan_title",
                    "path": f"/relationships/{relationship.local_key}/title",
                    "message": (
                        "relationship 的 title 必须是调查、操控、加害、盟友、亲属、"
                        "雇佣、成员等具体语义，不得使用泛化“有关联”或事件描述。"
                    ),
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "relationship_coverage",
                    "schema_id": "case-blueprint-v1",
                    "ir_path": f"/relationships/{relationship.local_key}",
                }
            )

    required_pairs: set[tuple[str, str]] = set()
    for event in blueprint.events:
        participants = sorted(set(event.dependency_keys) & entity_keys)
        for left_index, left_key in enumerate(participants):
            required_pairs.update(
                (left_key, right_key) for right_key in participants[left_index + 1 :]
            )
    for left_key, right_key in sorted(required_pairs - planned_pairs):
        issues.append(
            {
                "code": "relationship_plan_missing",
                "path": "/relationships",
                "message": (
                    f"关键事件中的实体 {left_key!r} 与 {right_key!r} 缺少 relationship "
                    "蓝图对象。请规划一条有剧情依据的具体语义关系，并把这两个 local_key "
                    "作为其 dependency_keys。"
                ),
                "component_id": "case_blueprint_planner",
                "failure_layer": "relationship_coverage",
                "schema_id": "case-blueprint-v1",
                "ir_path": "/relationships",
            }
        )

    connected_keys = {key for pair in planned_pairs for key in pair}
    for entity_key in sorted(entity_keys - connected_keys):
        issues.append(
            {
                "code": "isolated_entity_relationship_plan",
                "path": "/relationships",
                "message": (
                    f"实体 {entity_key!r} 在关系蓝图中完全孤立。请依据 Brief 中的身份、"
                    "目标、秘密或事件互动，为它规划至少一条具体实体关系。"
                ),
                "component_id": "case_blueprint_planner",
                "failure_layer": "relationship_coverage",
                "schema_id": "case-blueprint-v1",
                "ir_path": f"/entities/{entity_key}",
            }
        )
    return issues


def _blueprint_has_hypothesis_path(blueprint: CaseBlueprintV1 | None, hypothesis_key: str) -> bool:
    if blueprint is None:
        return True
    hypothesis_keys = {item.local_key for item in blueprint.hypotheses}
    return any(
        (set(path.dependency_keys) & hypothesis_keys) == {hypothesis_key}
        for path in blueprint.reasoning_paths
    )


def _blueprint_has_explicit_target_path(
    blueprint: CaseBlueprintV1 | None, hypothesis_key: str
) -> bool:
    if blueprint is None:
        return True
    return any(
        path.target_key == hypothesis_key and bool(path.required_information_keys)
        for path in blueprint.reasoning_paths
    )


def _evidence_assessment_issues(
    evidence: EvidenceLogicIRV2,
    *,
    strict_competition: bool = False,
    blueprint: CaseBlueprintV1 | None = None,
    use_explicit_targets: bool = False,
    include_matrix: bool = True,
) -> list[dict[str, Any]]:
    """Require a complete path-grounded matrix across competing hypotheses.

    Validation is staged so a broken prerequisite never floods the repair model
    with derived matrix-cell errors. Competitor peer sets come first, then the
    existence of an information-grounded path per competing hypothesis, and only
    then the exact matrix coverage. The deterministic-matrix runtime validates
    the graph first with ``include_matrix=False`` and joins the cells itself.
    """

    hypotheses_by_resolution = _hypotheses_by_resolution(evidence)
    competition_groups = [
        competitors for competitors in hypotheses_by_resolution.values() if len(competitors) >= 2
    ]
    used_information_by_hypothesis = _used_information_by_hypothesis(evidence)
    if strict_competition:
        peer_issues = _competition_peer_issues(hypotheses_by_resolution)
        if peer_issues:
            return peer_issues
        path_issues = _competition_path_issues(
            blueprint,
            competition_groups,
            used_information_by_hypothesis,
            use_explicit_targets=use_explicit_targets,
        )
        if path_issues:
            return path_issues
        if use_explicit_targets:
            reference_issues = _evidence_graph_reference_issues(evidence)
            if reference_issues:
                return reference_issues
    if not include_matrix:
        return []
    return _matrix_cell_issues(
        competition_groups,
        used_information_by_hypothesis,
        strict_competition=strict_competition,
    )


def _normalize_competing_hypothesis_closure(
    evidence: EvidenceLogicIRV2,
) -> EvidenceLogicIRV2:
    """Deterministically close each same-resolution group's competitor references.

    Hypotheses that target one Resolution compete by definition, so
    ``competing_hypothesis_keys`` is derived data: the server computes it from
    ``target_resolution_key`` instead of trusting the model to keep the N-way
    mutual references consistent across repair rounds.
    """

    groups: dict[str, list[str]] = {}
    for hypothesis in evidence.hypotheses:
        groups.setdefault(hypothesis.target_resolution_key, []).append(hypothesis.local_key)
    for hypothesis in evidence.hypotheses:
        expected = [
            key for key in groups[hypothesis.target_resolution_key] if key != hypothesis.local_key
        ]
        if hypothesis.competing_hypothesis_keys != expected:
            hypothesis.competing_hypothesis_keys = expected
    return evidence


def _evidence_from_output(
    value: Any,
    output_type: type[EvidenceLogicIRV1] | type[EvidenceLogicIRV2],
) -> EvidenceLogicIRV1 | EvidenceLogicIRV2:
    """Validate an Evidence output and derive competition closure server-side."""

    evidence = output_type.model_validate(value)
    if isinstance(evidence, EvidenceLogicIRV2):
        _normalize_competing_hypothesis_closure(evidence)
    return evidence


def _hypotheses_by_resolution(evidence: EvidenceLogicIRV2) -> dict[str, list[Any]]:
    """Group hypotheses by their target resolution, preserving singleton groups."""

    groups: dict[str, list[Any]] = {}
    for hypothesis in evidence.hypotheses:
        groups.setdefault(hypothesis.target_resolution_key, []).append(hypothesis)
    return groups


def _used_information_by_hypothesis(evidence: EvidenceLogicIRV2) -> dict[str, set[str]]:
    """Map each hypothesis to the information units its targeted paths input."""

    hypothesis_keys = {item.local_key for item in evidence.hypotheses}
    information_keys = {item.local_key for item in evidence.information_units}
    used: dict[str, set[str]] = {key: set() for key in hypothesis_keys}
    for path in evidence.reasoning_paths:
        if path.target_key not in hypothesis_keys:
            continue
        used[path.target_key].update(
            key for step in path.steps for key in step.input_keys if key in information_keys
        )
    return used


def _competition_peer_issues(
    hypotheses_by_resolution: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """Reject incomplete or cross-resolution competitor references."""

    issues: list[dict[str, Any]] = []
    for competitors in hypotheses_by_resolution.values():
        competitor_keys = {item.local_key for item in competitors}
        for hypothesis in competitors:
            expected_competitors = competitor_keys - {hypothesis.local_key}
            actual_competitors = set(hypothesis.competing_hypothesis_keys)
            if actual_competitors == expected_competitors:
                continue
            issues.append(
                {
                    "code": "competing_hypothesis_group_incomplete",
                    "path": f"/hypotheses/{hypothesis.local_key}/competing_hypothesis_keys",
                    "message": "竞争假设引用必须准确包含同一待解问题的全部其他假设。",
                    "component_id": "evidence_logic",
                    "failure_layer": "evidence_matrix",
                    "schema_id": "evidence-logic-ir-v2",
                    "ir_path": f"/hypotheses/{hypothesis.local_key}",
                }
            )
    return issues


def _competition_path_issues(
    blueprint: CaseBlueprintV1 | None,
    competition_groups: list[list[Any]],
    used_information_by_hypothesis: dict[str, set[str]],
    *,
    use_explicit_targets: bool,
) -> list[dict[str, Any]]:
    """Reject a competing hypothesis without an information-grounded targeted path."""

    issues: list[dict[str, Any]] = []
    for competitors in competition_groups:
        for hypothesis in competitors:
            if used_information_by_hypothesis[hypothesis.local_key]:
                continue
            planned = (
                _blueprint_has_explicit_target_path(blueprint, hypothesis.local_key)
                if use_explicit_targets
                else _blueprint_has_hypothesis_path(blueprint, hypothesis.local_key)
            )
            component_id = "evidence_logic" if planned else "case_blueprint_planner"
            issues.append(
                {
                    "code": "competing_hypothesis_path_missing",
                    "path": f"/reasoning_paths/{hypothesis.local_key}",
                    "message": "每个竞争假设必须有一条使用信息输入的对应推理路径。",
                    "component_id": component_id,
                    "failure_layer": (
                        "evidence_matrix"
                        if component_id == "evidence_logic"
                        else "blueprint_semantics"
                    ),
                    "schema_id": "evidence-logic-ir-v2",
                    "ir_path": f"/hypotheses/{hypothesis.local_key}",
                }
            )
    return issues


def _evidence_graph_reference_issues(evidence: EvidenceLogicIRV2) -> list[dict[str, Any]]:
    """Reject reasoning step outputs that are not claims or hypotheses."""

    allowed_targets = {item.local_key for item in evidence.claims} | {
        item.local_key for item in evidence.hypotheses
    }
    issues: list[dict[str, Any]] = []
    for path in evidence.reasoning_paths:
        for index, step in enumerate(path.steps):
            if step.output_key in allowed_targets:
                continue
            issues.append(
                {
                    "code": "reasoning_step_output_key_mismatch",
                    "path": f"/reasoning_paths/{path.local_key}/steps/{index}/output_key",
                    "message": "推理步骤 output_key 必须指向 claims 或 hypotheses 中的对象。",
                    "component_id": "evidence_logic",
                    "failure_layer": "evidence_matrix",
                    "schema_id": "evidence-logic-ir-v2",
                    "ir_path": f"/reasoning_paths/{path.local_key}",
                }
            )
    return issues


def _matrix_cell_issues(
    competition_groups: list[list[Any]],
    used_information_by_hypothesis: dict[str, set[str]],
    *,
    strict_competition: bool,
) -> list[dict[str, Any]]:
    """Reject missing, duplicate, or out-of-scope matrix cells."""

    issues: list[dict[str, Any]] = []
    for competitors in competition_groups:
        required_information = set().union(
            *(used_information_by_hypothesis[item.local_key] for item in competitors)
        )
        for hypothesis in competitors:
            assessments = list(getattr(hypothesis, "evidence_assessments", []))
            assessed_information = [item.information_key for item in assessments]
            for index, information_key in enumerate(assessed_information):
                if assessed_information.count(information_key) > 1:
                    issues.append(
                        {
                            "code": "duplicate_evidence_assessment",
                            "path": (
                                f"/hypotheses/{hypothesis.local_key}/"
                                f"evidence_assessments/{index}/information_key"
                            ),
                            "message": "同一假设不能重复评估同一信息。",
                            "component_id": "evidence_logic",
                            "failure_layer": "evidence_matrix",
                            "schema_id": "evidence-logic-ir-v2",
                            "ir_path": f"/hypotheses/{hypothesis.local_key}",
                        }
                    )
            for information_key in sorted(required_information - set(assessed_information)):
                issues.append(
                    {
                        "code": "missing_evidence_assessment",
                        "path": f"/hypotheses/{hypothesis.local_key}/evidence_assessments",
                        "message": f"竞争假设缺少对信息 {information_key!r} 的显式评估。",
                        "component_id": "evidence_logic",
                        "failure_layer": "evidence_matrix",
                        "schema_id": "evidence-logic-ir-v2",
                        "ir_path": f"/hypotheses/{hypothesis.local_key}",
                    }
                )
            if strict_competition:
                for information_key in sorted(set(assessed_information) - required_information):
                    issues.append(
                        {
                            "code": "unscoped_evidence_assessment",
                            "path": f"/hypotheses/{hypothesis.local_key}/evidence_assessments",
                            "message": (
                                f"信息 {information_key!r} 未被竞争组推理路径使用，"
                                "不应进入比较矩阵。"
                            ),
                            "component_id": "evidence_logic",
                            "failure_layer": "evidence_matrix",
                            "schema_id": "evidence-logic-ir-v2",
                            "ir_path": f"/hypotheses/{hypothesis.local_key}",
                        }
                    )
    return issues


def _v15_story_person_name_issues(
    story: StoryWorldIRV3,
) -> list[dict[str, Any]]:
    """Reject person entities whose name is still an identity role label.

    The v15 Story prompt requires a concrete personal name and explicitly
    bans role words such as 主角/嫌疑人/凶手 from the ``name`` field.  This
    deterministic gate turns the soft prompt contract into a recoverable
    quality-gate failure: the compile stage reports one issue per offending
    entity and the shared repair loop re-drafts only ``story_world`` with
    those targeted issues.
    """

    issues: list[dict[str, Any]] = []
    for entity in story.entities:
        if entity.entity_type != "person":
            continue
        normalized = "".join(entity.name.split())
        matched = next(
            (
                term
                for term in sorted(
                    _PERSON_ROLE_NAME_TERMS,
                    key=len,
                    reverse=True,
                )
                if term in normalized
            ),
            None,
        )
        if matched is None:
            continue
        issues.append(
            {
                "code": "person_name_role_label",
                "path": f"/entities/{entity.local_key}/name",
                "message": (
                    f"人物实体的 name 不能使用身份角色词“{matched}”。"
                    "请改为具体姓名（Brief 或 Blueprint 已给出姓名或化名时逐字沿用，"
                    "否则起一个与作品设定相符的简体中文姓名），"
                    "并把该身份写入 traits 或 description。"
                ),
                "component_id": "story_world",
                "failure_layer": "naming_contract",
                "schema_id": "story-world-ir-v3",
                "ir_path": f"/entities/{entity.local_key}",
            }
        )
    return issues


def _v16_story_relationship_coverage_issues(
    story: StoryWorldIRV3,
) -> list[dict[str, Any]]:
    """Require explicit semantic edges for meaningful entity interactions."""

    entity_keys = {entity.local_key for entity in story.entities}
    if len(entity_keys) < 2:
        return []

    existing_pairs = {
        tuple(sorted((relationship.from_key, relationship.to_key)))
        for relationship in story.relationships
        if relationship.from_key in entity_keys and relationship.to_key in entity_keys
    }
    required_pairs: set[tuple[str, str]] = set()
    for event in story.events:
        participants = sorted(set(event.participant_keys) & entity_keys)
        for left_index, left_key in enumerate(participants):
            required_pairs.update(
                (left_key, right_key) for right_key in participants[left_index + 1 :]
            )

    issues: list[dict[str, Any]] = []
    generic_terms = ("有关联", "关联关系", "共同参与", "同场出现", "事件关联")
    event_titles = {"".join(event.title.split()) for event in story.events}
    for relationship in story.relationships:
        normalized_title = "".join(relationship.title.split())
        if not (
            any(term in normalized_title for term in generic_terms)
            or normalized_title in event_titles
        ):
            continue
        issues.append(
            {
                "code": "generic_relationship_title",
                "path": f"/relationships/{relationship.local_key}/title",
                "message": (
                    f"关系 {relationship.local_key!r} 的 title 只描述了泛化关联或复用了事件标题。"
                    "请改为可直接展示在关系线上的具体语义，例如“调查”“操控”“加害”"
                    "“盟友”“父女”“雇佣”或“成员”；同时让 relationship_type 与之对应。"
                ),
                "component_id": "story_world",
                "failure_layer": "relationship_coverage",
                "schema_id": "story-world-ir-v3",
                "ir_path": f"/relationships/{relationship.local_key}",
            }
        )

    missing_pairs = sorted(required_pairs - existing_pairs)
    for from_key, to_key in missing_pairs:
        issues.append(
            {
                "code": "missing_semantic_relationship",
                "path": "/relationships",
                "message": (
                    f"实体 {from_key!r} 与 {to_key!r} 在同一关键事件中发生互动，"
                    "但缺少显式语义关系。请新增一条 RelationshipIR，from_key 与 to_key "
                    "逐字使用这两个 local_key；title 必须直接说明二者是什么关系"
                    "（例如调查、操控、加害、盟友、亲属、雇佣），不得使用“有关联”"
                    "“共同参与”或事件标题代替；relationship_type 使用对应的小写机器标识。"
                ),
                "component_id": "story_world",
                "failure_layer": "relationship_coverage",
                "schema_id": "story-world-ir-v3",
                "ir_path": "/relationships",
            }
        )

    connected_keys = {key for pair in existing_pairs for key in pair}
    required_keys = {key for pair in required_pairs for key in pair}
    for entity_key in sorted(entity_keys - connected_keys - required_keys):
        issues.append(
            {
                "code": "isolated_story_entity",
                "path": "/relationships",
                "message": (
                    f"核心实体 {entity_key!r} 在关系图中完全孤立。请依据 Brief、Blueprint、"
                    "实体目标或秘密，新增至少一条连接它与另一实体的具体语义关系；"
                    "不得虚构无依据事实，也不得使用“有关联”作为 title。"
                ),
                "component_id": "story_world",
                "failure_layer": "relationship_coverage",
                "schema_id": "story-world-ir-v3",
                "ir_path": "/relationships",
            }
        )
    return issues


def _v11_story_issues(
    story: StoryWorldIRV2,
    allowed_wgs84_coordinates: list[CoordinatePairV1],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    allowed = {(item.latitude, item.longitude) for item in allowed_wgs84_coordinates}
    for location in story.locations:
        position = location.spatial_position
        if (
            isinstance(position, Wgs84SpatialPositionIRV2)
            and (
                position.latitude,
                position.longitude,
            )
            not in allowed
        ):
            issues.append(
                {
                    "code": "wgs84_not_explicit_in_brief",
                    "path": f"/locations/{location.local_key}/spatial_position",
                    "message": "WGS84 坐标必须逐值命中冻结 Brief 的显式坐标白名单。",
                    "component_id": "story_world",
                    "failure_layer": "spatial_grounding",
                    "schema_id": "story-world-ir-v2",
                    "ir_path": f"/locations/{location.local_key}",
                }
            )

    relative_anchors: dict[str, str] = {}
    event_keys = {item.local_key for item in story.events}
    for event in story.events:
        if not isinstance(event.time, RelativeTemporalPositionIRV2):
            continue
        anchor = event.time.anchor_event_key
        relative_anchors[event.local_key] = anchor
        if anchor not in event_keys:
            issues.append(
                {
                    "code": "relative_time_anchor_unknown",
                    "path": f"/events/{event.local_key}/time/anchor_event_key",
                    "message": "相对时间锚点必须引用本 Story 输出中的事件。",
                    "component_id": "story_world",
                    "failure_layer": "temporal_grounding",
                    "schema_id": "story-world-ir-v2",
                    "ir_path": f"/events/{event.local_key}/time",
                }
            )
        elif anchor == event.local_key:
            issues.append(
                {
                    "code": "relative_time_self_anchor",
                    "path": f"/events/{event.local_key}/time/anchor_event_key",
                    "message": "事件不能把自身作为相对时间锚点。",
                    "component_id": "story_world",
                    "failure_layer": "temporal_grounding",
                    "schema_id": "story-world-ir-v2",
                    "ir_path": f"/events/{event.local_key}/time",
                }
            )

    for start in sorted(relative_anchors):
        seen: set[str] = set()
        current = start
        while current in relative_anchors:
            if current in seen:
                issues.append(
                    {
                        "code": "relative_time_cycle",
                        "path": f"/events/{start}/time/anchor_event_key",
                        "message": "相对事件时间不能形成循环锚定。",
                        "component_id": "story_world",
                        "failure_layer": "temporal_grounding",
                        "schema_id": "story-world-ir-v2",
                        "ir_path": f"/events/{start}/time",
                    }
                )
                break
            seen.add(current)
            current = relative_anchors[current]
    return issues


def _extract_allowed_wgs84_coordinates(brief: dict[str, Any]) -> list[CoordinatePairV1]:
    coordinates: set[tuple[float, float]] = set()

    def add(latitude: object, longitude: object) -> None:
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float, str))
            or not isinstance(longitude, (int, float, str))
        ):
            return
        try:
            pair = CoordinatePairV1(latitude=float(latitude), longitude=float(longitude))
        except (TypeError, ValueError):
            return
        coordinates.add((pair.latitude, pair.longitude))

    def visit(value: object) -> None:
        if isinstance(value, dict):
            latitude = next(
                (value[key] for key in ("latitude", "lat", "纬度") if key in value),
                None,
            )
            longitude = next(
                (value[key] for key in ("longitude", "lon", "lng", "经度") if key in value),
                None,
            )
            if latitude is not None and longitude is not None:
                add(latitude, longitude)
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, list):
            for nested in value:
                visit(nested)
            return
        if not isinstance(value, str):
            return
        for pattern in _LABELED_COORDINATE_PATTERNS:
            for match in pattern.finditer(value):
                add(match.group("lat"), match.group("lon"))

    visit(brief)
    return [
        CoordinatePairV1(latitude=latitude, longitude=longitude)
        for latitude, longitude in sorted(coordinates)
    ]


_PERSON_ROLE_NAME_TERMS = frozenset(
    {
        "男主人公",
        "女主人公",
        "主人公",
        "男主角",
        "女主角",
        "男主",
        "女主",
        "主角",
        "嫌疑人",
        "嫌疑犯",
        "凶手",
        "受害者",
        "被害人",
        "受害人",
        "目击者",
        "证人",
        "侦探",
        "警察",
        "刑警",
        "法医",
        "医生",
        "管家",
        "邻居",
        "神秘人",
        "黑衣人",
        "幕后黑手",
        "表层黑手",
        "黑手",
        "主谋",
        "帮凶",
        "共犯",
    }
)


_LABELED_COORDINATE_PATTERNS = (
    re.compile(
        r"(?:latitude|lat|纬度|北纬)\s*[:=：]?\s*"
        r"(?P<lat>-?\d{1,2}(?:\.\d+)?)\s*(?:°|度)?\s*[,，;；/\s]+"
        r"(?:longitude|lon|经度|东经)\s*[:=：]?\s*"
        r"(?P<lon>-?\d{1,3}(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:longitude|lon|经度|东经)\s*[:=：]?\s*"
        r"(?P<lon>-?\d{1,3}(?:\.\d+)?)\s*(?:°|度)?\s*[,，;；/\s]+"
        r"(?:latitude|lat|纬度|北纬)\s*[:=：]?\s*"
        r"(?P<lat>-?\d{1,2}(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:coordinates?|坐标)\s*[:=：]?\s*[（(]?\s*"
        r"(?P<lat>-?\d{1,2}(?:\.\d+)?)\s*[,，]\s*"
        r"(?P<lon>-?\d{1,3}(?:\.\d+)?)",
        re.IGNORECASE,
    ),
)


def _diagnostic_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "component_id": issue.get("component_id", "quality_repair_gate"),
        "failure_layer": issue.get("failure_layer", "quality_gate"),
        "schema_id": issue.get("schema_id", "casefile-v1"),
        "code": issue.get("code", "validation_failed"),
        "path": issue.get("path", ""),
        "message": issue.get("message", "候选未通过质量门禁。"),
    }


__all__ = [
    "_request_repair_issues",
    "_repair_issues_for_component",
    "_blueprint_path_plan_issues",
    "_blueprint_competition_groups",
    "_v11_blueprint_issues",
    "_v15_blueprint_path_issues",
    "_v16_blueprint_relationship_coverage_issues",
    "_blueprint_has_hypothesis_path",
    "_blueprint_has_explicit_target_path",
    "_evidence_assessment_issues",
    "_normalize_competing_hypothesis_closure",
    "_evidence_from_output",
    "_hypotheses_by_resolution",
    "_used_information_by_hypothesis",
    "_competition_peer_issues",
    "_competition_path_issues",
    "_evidence_graph_reference_issues",
    "_matrix_cell_issues",
    "_v15_story_person_name_issues",
    "_v16_story_relationship_coverage_issues",
    "_v11_story_issues",
    "_extract_allowed_wgs84_coordinates",
]
