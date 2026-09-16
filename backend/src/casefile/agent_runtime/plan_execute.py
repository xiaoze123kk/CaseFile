"""Shared Plan-Execute contracts and deterministic Nag reminder state.

The model owns literary planning and reconciliation.  This module only binds
those outputs to immutable source hashes, validates references, and rebuilds
reminder state from ordered successful calls.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile_contracts import SceneRenderCandidate

PlanBranch = Literal["story_world", "evidence_logic", "scene_prose"]
CheckinStatus = Literal["fulfilled", "not_fulfilled", "maintained", "plan_change_needed"]
ReconciliationStatus = Literal["fulfilled", "partial", "not_fulfilled", "unknown"]

NAG_THRESHOLD = 2
PLAN_EXECUTE_POLICY_VERSION = "plan-execute-v1"
NAG_POLICY_VERSION = "plan-nag-v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionGoalCandidate(_StrictModel):
    goal_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    objective: str = Field(min_length=1, max_length=1000)
    verification: str = Field(min_length=1, max_length=1000)
    owner_branch: PlanBranch
    source_refs: list[str] = Field(min_length=1, max_length=32)
    depends_on_goal_ids: list[str] = Field(default_factory=list, max_length=32)
    preserve_constraints: list[str] = Field(default_factory=list, max_length=16)
    applies_from: int = Field(default=1, ge=1)
    applies_until: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> ExecutionGoalCandidate:
        if self.applies_until is not None and self.applies_until < self.applies_from:
            raise ValueError("plan_goal_range_invalid")
        if self.goal_id in self.depends_on_goal_ids:
            raise ValueError("plan_goal_self_dependency")
        return self


class ExecutionPlanCandidate(_StrictModel):
    schema_id: Literal["casefile.execution-plan-candidate.v1"] = (
        "casefile.execution-plan-candidate.v1"
    )
    goals: list[ExecutionGoalCandidate] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_graph(self) -> ExecutionPlanCandidate:
        _validate_goal_graph(self.goals)
        return self


class ExecutionGoal(ExecutionGoalCandidate):
    pass


class ExecutionPlan(_StrictModel):
    schema_id: Literal["casefile.execution-plan.v1"] = "casefile.execution-plan.v1"
    policy_version: Literal["plan-execute-v1"] = "plan-execute-v1"
    source_schema_id: str = Field(min_length=1, max_length=160)
    source_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    goals: list[ExecutionGoal] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_graph(self) -> ExecutionPlan:
        _validate_goal_graph(self.goals)
        return self


class PlanCheckinItem(_StrictModel):
    goal_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: CheckinStatus
    evidence_paths: list[str] = Field(default_factory=list, max_length=32)
    reason: str = Field(min_length=1, max_length=1000)


class PlanCheckinCandidate(_StrictModel):
    schema_id: Literal["casefile.plan-checkin-candidate.v1"] = (
        "casefile.plan-checkin-candidate.v1"
    )
    branch: PlanBranch
    items: list[PlanCheckinItem] = Field(max_length=4096)


class SceneProsePlanOutput(_StrictModel):
    schema_id: Literal["compiler.scene-prose-plan-output.v1"] = (
        "compiler.scene-prose-plan-output.v1"
    )
    artifact: SceneRenderCandidate
    plan_checkin: dict[str, Any] | None = None


class PlanCheckin(_StrictModel):
    schema_id: Literal["casefile.plan-checkin.v1"] = "casefile.plan-checkin.v1"
    policy_version: Literal["plan-execute-v1"] = "plan-execute-v1"
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: PlanBranch
    call_key: str = Field(min_length=1, max_length=200)
    items: list[PlanCheckinItem] = Field(max_length=4096)


class PlanCallRecord(_StrictModel):
    schema_id: Literal["casefile.plan-call-record.v1"] = "casefile.plan-call-record.v1"
    policy_version: Literal["plan-execute-v1"] = "plan-execute-v1"
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: PlanBranch
    sequence_no: int = Field(ge=1)
    call_key: str = Field(min_length=1, max_length=200)
    checkin: PlanCheckin | None = None


class UnresolvedPlanItem(_StrictModel):
    goal_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: CheckinStatus | Literal["missing_checkin"]
    reason: str = Field(min_length=1, max_length=1000)
    call_key: str = Field(min_length=1, max_length=200)


class PlanContext(_StrictModel):
    schema_id: Literal["casefile.plan-context.v1"] = "casefile.plan-context.v1"
    policy_version: Literal["plan-execute-v1"] = "plan-execute-v1"
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: PlanBranch
    sequence_no: int = Field(ge=1)
    applicable_goals: list[ExecutionGoal] = Field(max_length=4096)
    unresolved_goal_ids: list[str] = Field(max_length=4096)
    unresolved_items: list[UnresolvedPlanItem] = Field(default_factory=list, max_length=4096)
    nag_reminder: str | None = Field(default=None, max_length=4000)
    nag_policy_version: Literal["plan-nag-v1"] = "plan-nag-v1"


class ReconciliationItem(_StrictModel):
    goal_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: ReconciliationStatus
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    reason: str = Field(min_length=1, max_length=1000)
    suggested_plan_change: str | None = Field(default=None, max_length=1000)


class PlanReconciliationReport(_StrictModel):
    schema_id: Literal["casefile.plan-reconciliation.v1"] = (
        "casefile.plan-reconciliation.v1"
    )
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: list[ReconciliationItem] = Field(max_length=4096)


class PlanningSummary(_StrictModel):
    status: Literal["completed", "unavailable"]
    fulfilled: int = Field(ge=0)
    partial: int = Field(ge=0)
    not_fulfilled: int = Field(ge=0)
    unknown: int = Field(ge=0)
    unresolved_items: list[str] = Field(default_factory=list, max_length=128)
    suggested_plan_changes: list[str] = Field(default_factory=list, max_length=128)


def bind_execution_plan(
    candidate: ExecutionPlanCandidate | Mapping[str, Any],
    *,
    source_schema_id: str,
    source_plan: Mapping[str, Any],
    allowed_source_refs: Iterable[str],
) -> ExecutionPlan:
    """Bind a model-authored plan to an immutable authoritative plan."""

    parsed = ExecutionPlanCandidate.model_validate(candidate)
    allowed = set(allowed_source_refs)
    for goal in parsed.goals:
        if any(ref not in allowed for ref in goal.source_refs):
            raise ValueError("plan_goal_source_ref_invalid")
    return ExecutionPlan(
        source_schema_id=source_schema_id,
        source_plan_hash=canonical_json_sha256(dict(source_plan)),
        goals=[ExecutionGoal.model_validate(item.model_dump()) for item in parsed.goals],
    )


def derive_scene_execution_plan(scene_plan: Mapping[str, Any]) -> ExecutionPlan:
    """Project only explicit ScenePlan obligations into prose execution goals."""

    scenes = scene_plan.get("scenes")
    beats = scene_plan.get("beats")
    if not isinstance(scenes, list) or not scenes or not isinstance(beats, list):
        raise ValueError("scene_execution_plan_source_invalid")
    beats_by_scene: dict[str, list[dict[str, Any]]] = {}
    for beat in beats:
        if isinstance(beat, dict) and isinstance(beat.get("scene_id"), str):
            beats_by_scene.setdefault(beat["scene_id"], []).append(beat)
    ordered = sorted(scenes, key=lambda item: int(item["discourse_order"]))
    goal_ids = {
        scene["scene_id"]: (
            f"scene_{int(scene['discourse_order']):03d}_"
            f"{canonical_json_sha256(scene)[:8]}"
        )
        for scene in ordered
    }
    goals: list[ExecutionGoal] = []
    for scene in ordered:
        scene_id = str(scene["scene_id"])
        ordinal = int(scene["discourse_order"])
        scene_beats = sorted(beats_by_scene.get(scene_id, []), key=lambda item: item["ordinal"])
        setup_keys = sorted(
            {
                key
                for beat in scene_beats
                for key in beat.get("setup_keys", [])
                if isinstance(key, str)
            }
        )
        payoff_keys = sorted(
            {
                key
                for beat in scene_beats
                for key in beat.get("payoff_keys", [])
                if isinstance(key, str)
            }
        )
        reveals = [
            f"{item['action']}:{item['entry_key']}"
            for item in scene.get("allowed_reveals", [])
            if isinstance(item, dict)
        ]
        resolutions = [
            f"{item['action']}:{item['resolution_ref']['object_id']}"
            for item in scene.get("resolution_actions", [])
            if isinstance(item, dict)
            and isinstance(item.get("resolution_ref"), dict)
        ]
        verification_parts = [
            *(["按场景目标完成主要动作"] if scene.get("objective") else []),
            *(["落实信息释放 " + "、".join(reveals)] if reveals else []),
            *(["建立铺垫 " + "、".join(setup_keys)] if setup_keys else []),
            *(["回收铺垫 " + "、".join(payoff_keys)] if payoff_keys else []),
            *(["处理结论 " + "、".join(resolutions)] if resolutions else []),
        ]
        dependencies = [
            goal_ids[dependency]
            for dependency in scene.get("prerequisite_scene_ids", [])
            if dependency in goal_ids
        ]
        goals.append(
            ExecutionGoal(
                goal_id=goal_ids[scene_id],
                objective=str(scene.get("objective") or f"完成场景 {scene_id}"),
                verification="；".join(verification_parts) or "正文与场景计划保持一致",
                owner_branch="scene_prose",
                source_refs=[scene_id, *[str(beat["beat_id"]) for beat in scene_beats]],
                depends_on_goal_ids=dependencies,
                preserve_constraints=[
                    f"不得提前揭露 {key}"
                    for key in scene.get("forbidden_reveal_entry_keys", [])
                    if isinstance(key, str)
                ],
                applies_from=ordinal,
                applies_until=ordinal,
            )
        )
    scene_ordinals = {str(scene["scene_id"]): int(scene["discourse_order"]) for scene in ordered}
    setup_beats: dict[str, dict[str, Any]] = {}
    payoff_beats: dict[str, dict[str, Any]] = {}
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        for key in beat.get("setup_keys", []):
            if isinstance(key, str):
                setup_beats[key] = beat
        for key in beat.get("payoff_keys", []):
            if isinstance(key, str):
                payoff_beats[key] = beat
    for key in sorted(setup_beats):
        setup = setup_beats[key]
        payoff = payoff_beats.get(key)
        start = scene_ordinals[str(setup["scene_id"])]
        end = (
            scene_ordinals[str(payoff["scene_id"])]
            if payoff is not None
            else len(ordered)
        )
        refs = [str(setup["beat_id"])]
        if payoff is not None:
            refs.append(str(payoff["beat_id"]))
        goals.append(
            ExecutionGoal(
                goal_id=f"setup_{canonical_json_sha256({'key': key, 'refs': refs})[:12]}",
                objective=f"铺设并在计划位置回收伏笔 {key}",
                verification=(
                    f"正文在第 {start} 场建立伏笔，并在第 {end} 场完成来源支持的回收"
                ),
                owner_branch="scene_prose",
                source_refs=refs,
                depends_on_goal_ids=[],
                preserve_constraints=[f"第 {end} 场以前不得提前明示伏笔答案"],
                applies_from=start,
                applies_until=end,
            )
        )
    return ExecutionPlan(
        source_schema_id=str(scene_plan.get("schema_id") or "compiler.scene-plan.v2"),
        source_plan_hash=canonical_json_sha256(dict(scene_plan)),
        goals=goals,
    )


def plan_hash(plan: ExecutionPlan) -> str:
    return canonical_json_sha256(plan.model_dump(mode="json"))


def applicable_goals(
    plan: ExecutionPlan,
    *,
    branch: PlanBranch,
    sequence_no: int,
) -> tuple[ExecutionGoal, ...]:
    return tuple(
        goal
        for goal in plan.goals
        if goal.owner_branch == branch
        and goal.applies_from <= sequence_no
        and (goal.applies_until is None or sequence_no <= goal.applies_until)
    )


def bind_checkin(
    candidate: PlanCheckinCandidate | Mapping[str, Any] | None,
    *,
    plan: ExecutionPlan,
    branch: PlanBranch,
    sequence_no: int,
    artifact: Mapping[str, Any],
    call_key: str,
) -> PlanCheckin | None:
    """Return None for a missing/incomplete check-in without rejecting the artifact."""

    if candidate is None:
        return None
    try:
        parsed = PlanCheckinCandidate.model_validate(candidate)
    except ValueError:
        return None
    expected = {
        goal.goal_id
        for goal in applicable_goals(plan, branch=branch, sequence_no=sequence_no)
    }
    if parsed.branch != branch or {item.goal_id for item in parsed.items} != expected:
        return None
    if len({item.goal_id for item in parsed.items}) != len(parsed.items):
        return None
    artifact_json = dict(artifact)
    for item in parsed.items:
        if item.status in {"fulfilled", "maintained"} and not item.evidence_paths:
            return None
        if any(
            _resolve_json_pointer(artifact_json, path) is _MISSING
            for path in item.evidence_paths
        ):
            return None
    return PlanCheckin(
        plan_hash=plan_hash(plan),
        artifact_hash=canonical_json_sha256(artifact_json),
        branch=branch,
        call_key=call_key,
        items=parsed.items,
    )


def build_plan_call_record(
    *,
    plan: ExecutionPlan,
    branch: PlanBranch,
    sequence_no: int,
    artifact: Mapping[str, Any],
    call_key: str,
    checkin: PlanCheckin | None,
) -> PlanCallRecord:
    artifact_hash = canonical_json_sha256(dict(artifact))
    bound_plan_hash = plan_hash(plan)
    if checkin is not None and (
        checkin.plan_hash != bound_plan_hash
        or checkin.artifact_hash != artifact_hash
        or checkin.branch != branch
        or checkin.call_key != call_key
    ):
        raise ValueError("plan_call_record_checkin_binding_invalid")
    return PlanCallRecord(
        plan_hash=bound_plan_hash,
        artifact_hash=artifact_hash,
        branch=branch,
        sequence_no=sequence_no,
        call_key=call_key,
        checkin=checkin,
    )


@dataclass(slots=True)
class NagLedger:
    """Per-branch omission counters rebuilt from immutable successful calls."""

    threshold: int = NAG_THRESHOLD
    omissions: dict[tuple[PlanBranch, str], int] = field(default_factory=dict)
    omission_refs: dict[tuple[PlanBranch, str], list[str]] = field(default_factory=dict)
    seen_calls: set[str] = field(default_factory=set)
    unresolved: set[tuple[PlanBranch, str]] = field(default_factory=set)
    unresolved_details: dict[tuple[PlanBranch, str], UnresolvedPlanItem] = field(
        default_factory=dict
    )

    def context(
        self,
        plan: ExecutionPlan,
        *,
        branch: PlanBranch,
        sequence_no: int,
    ) -> PlanContext:
        goals = applicable_goals(plan, branch=branch, sequence_no=sequence_no)
        nagged = [
            goal
            for goal in goals
            if self.omissions.get((branch, goal.goal_id), 0) >= self.threshold
        ]
        return PlanContext(
            plan_hash=plan_hash(plan),
            branch=branch,
            sequence_no=sequence_no,
            applicable_goals=list(goals),
            unresolved_goal_ids=[
                goal.goal_id for goal in goals if (branch, goal.goal_id) in self.unresolved
            ],
            unresolved_items=[
                self.unresolved_details[(branch, goal.goal_id)]
                for goal in goals
                if (branch, goal.goal_id) in self.unresolved_details
            ],
            nag_reminder=(
                _nag_message(nagged, self.omission_refs) if nagged else None
            ),
        )

    def record_success(
        self,
        *,
        call_key: str,
        plan: ExecutionPlan,
        branch: PlanBranch,
        sequence_no: int,
        checkin: PlanCheckin | None,
    ) -> None:
        if call_key in self.seen_calls:
            return
        self.seen_calls.add(call_key)
        by_goal = {} if checkin is None else {item.goal_id: item for item in checkin.items}
        for goal in applicable_goals(plan, branch=branch, sequence_no=sequence_no):
            key = (branch, goal.goal_id)
            item = by_goal.get(goal.goal_id)
            if item is None:
                self.omissions[key] = self.omissions.get(key, 0) + 1
                refs = self.omission_refs.setdefault(key, [])
                if call_key not in refs:
                    refs.append(call_key)
                    del refs[:-self.threshold]
                self.unresolved.add(key)
                self.unresolved_details[key] = UnresolvedPlanItem(
                    goal_id=goal.goal_id,
                    status="missing_checkin",
                    reason="最近一次相关产物没有提供完整有效的计划核对。",
                    call_key=call_key,
                )
                continue
            self.omissions[key] = 0
            self.omission_refs.pop(key, None)
            if item.status == "fulfilled":
                self.unresolved.discard(key)
                self.unresolved_details.pop(key, None)
            else:
                self.unresolved.add(key)
                self.unresolved_details[key] = UnresolvedPlanItem(
                    goal_id=goal.goal_id,
                    status=item.status,
                    reason=item.reason,
                    call_key=call_key,
                )


def validate_reconciliation(
    report: PlanReconciliationReport | Mapping[str, Any],
    *,
    plan: ExecutionPlan,
    evidence: Mapping[str, Any],
) -> PlanReconciliationReport:
    parsed = PlanReconciliationReport.model_validate(report)
    if parsed.plan_hash != plan_hash(plan):
        raise ValueError("plan_reconciliation_hash_mismatch")
    expected = {goal.goal_id for goal in plan.goals}
    if {item.goal_id for item in parsed.items} != expected or len(parsed.items) != len(expected):
        raise ValueError("plan_reconciliation_coverage_invalid")
    for item in parsed.items:
        if item.status in {"fulfilled", "partial"} and not item.evidence_refs:
            raise ValueError("plan_reconciliation_evidence_missing")
        if any(_resolve_json_pointer(evidence, path) is _MISSING for path in item.evidence_refs):
            raise ValueError("plan_reconciliation_evidence_invalid")
        if item.status != "unknown" and item.suggested_plan_change and not item.reason:
            raise ValueError("plan_reconciliation_reason_missing")
    return parsed


def planning_summary(report: PlanReconciliationReport | None) -> PlanningSummary:
    if report is None:
        return PlanningSummary(
            status="unavailable",
            fulfilled=0,
            partial=0,
            not_fulfilled=0,
            unknown=0,
        )
    counts = {status: 0 for status in ("fulfilled", "partial", "not_fulfilled", "unknown")}
    for item in report.items:
        counts[item.status] += 1
    return PlanningSummary(
        status="completed",
        fulfilled=counts["fulfilled"],
        partial=counts["partial"],
        not_fulfilled=counts["not_fulfilled"],
        unknown=counts["unknown"],
        unresolved_items=[
            item.reason for item in report.items if item.status != "fulfilled"
        ],
        suggested_plan_changes=[
            item.suggested_plan_change
            for item in report.items
            if item.suggested_plan_change is not None
        ],
    )


def _validate_goal_graph(goals: Sequence[ExecutionGoalCandidate]) -> None:
    ids = [goal.goal_id for goal in goals]
    if len(ids) != len(set(ids)):
        raise ValueError("plan_goal_id_duplicate")
    known = set(ids)
    if any(dep not in known for goal in goals for dep in goal.depends_on_goal_ids):
        raise ValueError("plan_goal_dependency_unknown")
    visiting: set[str] = set()
    visited: set[str] = set()
    edges = {goal.goal_id: goal.depends_on_goal_ids for goal in goals}

    def visit(goal_id: str) -> None:
        if goal_id in visited:
            return
        if goal_id in visiting:
            raise ValueError("plan_goal_dependency_cycle")
        visiting.add(goal_id)
        for dependency in edges[goal_id]:
            visit(dependency)
        visiting.remove(goal_id)
        visited.add(goal_id)

    for goal_id in ids:
        visit(goal_id)


def _nag_message(
    goals: Sequence[ExecutionGoal],
    omission_refs: Mapping[tuple[PlanBranch, str], Sequence[str]],
) -> str:
    labels = []
    for goal in goals:
        refs = "、".join(omission_refs.get((goal.owner_branch, goal.goal_id), ()))
        labels.append(f"“{goal.objective}”（最近遗漏：{refs or '无引用'}）")
    prefix = "最近两次相关生成尚未有效核对"
    suffix = (
        "。请结合本次产物说明落实程度并引用依据；若尚未落实或需要调整计划，请如实说明原因。"
        "不得为了完成条目提前揭露信息。"
    )
    available = 4000 - len(prefix) - len(suffix)
    selected = "、".join(labels)
    if len(selected) > available:
        selected = selected[: max(0, available - 1)] + "…"
    return prefix + selected + suffix


class _Missing:
    pass


_MISSING = _Missing()


def _resolve_json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        return _MISSING
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return _MISSING
    return current


__all__ = [
    "ExecutionGoal",
    "ExecutionGoalCandidate",
    "ExecutionPlan",
    "ExecutionPlanCandidate",
    "NAG_POLICY_VERSION",
    "NAG_THRESHOLD",
    "NagLedger",
    "PLAN_EXECUTE_POLICY_VERSION",
    "PlanCheckin",
    "PlanCheckinCandidate",
    "PlanCheckinItem",
    "PlanContext",
    "PlanReconciliationReport",
    "PlanningSummary",
    "ReconciliationItem",
    "SceneProsePlanOutput",
    "applicable_goals",
    "bind_checkin",
    "bind_execution_plan",
    "derive_scene_execution_plan",
    "plan_hash",
    "planning_summary",
    "validate_reconciliation",
]
