from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import pytest
from casefile.agent_runtime.goal.contracts import (
    GoalAmendmentOutput,
    GoalDecisionOutput,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.models import GenerationRequest, GenerationResult, ToolMetrics
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.benchmark.chat_goal_interactive_executor import (
    PostgresInteractiveGoalExecutor,
)
from casefile.benchmark.chat_goal_interactive_suite import (
    FAMILY_DISTRIBUTION,
    InjectionPoint,
    InteractiveAction,
    InteractiveExpectedMessageOutcome,
    canonical_hash,
    load_dev_suite,
)
from casefile.contracts import validate_casefile
from sqlalchemy import Engine

pytestmark = pytest.mark.postgres


class _InteractiveFakeProvider(FakeProvider):
    def __init__(self, document: dict[str, Any]) -> None:
        super().__init__(
            goal_understanding=_understanding(),
            goal_amendment=_amendment(),
            goal_decisions=(_decision("analyze", "obl_1"), _decision("audit", "obl_2"), _finish()),
        )
        self.document = document

    def generate(self, request: GenerationRequest) -> GenerationResult:
        candidate = deepcopy(self.document)
        candidate["casefile_id"] = request.casefile_id
        candidate["version"] = {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        }
        candidate["brief_ref"] = {
            "brief_id": request.brief_id,
            "version": request.brief_version,
        }
        for constraint in candidate.get("constraints", []):
            for scope_ref in constraint.get("scope_refs", []):
                if scope_ref.get("object_type") == "casefile":
                    scope_ref["object_id"] = request.casefile_id
        validate_casefile(candidate)
        return GenerationResult(
            candidate=candidate,
            usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tools=ToolMetrics(calls=1, valid_calls=1, successful_calls=1, adopted_results=1),
        )


class _InteractiveFamilyFakeProvider(_InteractiveFakeProvider):
    def __init__(self, document: dict[str, Any], family: str) -> None:
        super().__init__(document)
        self.family = family

    def understand_goal(self, request: Any) -> Any:
        understanding = (
            _clarification_understanding(request.chat.message)
            if self.family == "clarification_resume"
            else (
                _mutation_understanding(request.chat.message)
                if self.family
                in {"patch_review_resume", "stale_interrupt_safety"}
                else _understanding_for_message(request.chat.message)
            )
        )
        return FakeProvider(goal_understanding=understanding).understand_goal(request)

    def amend_goal(self, request: Any) -> Any:
        obligations = [
            {
                "obligation_ref": item.obligation_id,
                "kind": item.kind,
                "target_state": item.target_state,
                "source_excerpt": item.source_excerpt,
                "depends_on": item.depends_on,
            }
            for item in request.current_goal.obligations
        ]
        kind = "refine"
        if self.family == "steer_constraint":
            kind = "add_constraint"
        elif self.family == "steer_obligation":
            kind = "add_obligation"
            obligations.append(
                {
                    "obligation_ref": "new_3",
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": request.chat.message,
                    "depends_on": [request.current_goal.obligations[-1].obligation_id],
                }
            )
        amendment = GoalAmendmentOutput.model_validate(
            {
                "amendment_kind": kind,
                "goal": request.chat.message,
                "obligations": obligations,
            }
        )
        return FakeProvider(goal_amendment=amendment).amend_goal(request)

    def decide_goal(self, request: Any) -> Any:
        completed = {
            obligation_id
            for observation in request.observations
            if observation.status == "completed"
            for obligation_id in observation.obligation_ids
        }
        plan_items = [
            {
                "obligation_id": item.obligation_id,
                "status": "completed" if item.obligation_id in completed else "pending",
            }
            for item in request.goal.obligations
        ]
        pending = next(
            (
                item
                for item in request.goal.obligations
                if item.obligation_id not in completed
                and all(dependency in completed for dependency in item.depends_on)
            ),
            None,
        )
        if pending is None:
            candidate = GoalDecisionOutput.model_validate(
                {"plan_items": plan_items, "action": {"action": "finish"}}
            )
        else:
            capability = {
                "analysis": "analyze",
                "audit": "audit",
                "mutation_proposal": "propose_mutation",
            }[pending.kind]
            candidate = GoalDecisionOutput.model_validate(
                {
                    "plan_items": plan_items,
                    "action": {
                        "action": "invoke_capability",
                        "capability": capability,
                        "obligation_ids": [pending.obligation_id],
                        "target_state": pending.target_state,
                    },
                }
            )
        return FakeProvider(goal_decisions=(candidate,)).decide_goal(request)


@pytest.mark.parametrize(
    ("safe_point", "capability", "ordinal"),
    [
        ("before_controller", None, None),
        ("after_capability", "analyze", 1),
        ("before_finalizer", None, None),
    ],
)
def test_interactive_executor_injects_steer_at_real_safe_point(
    workflow_database: tuple[Engine, int, str],
    safe_point: Literal["before_controller", "after_capability", "before_finalizer"],
    capability: Literal["analyze", "audit", "propose_mutation"] | None,
    ordinal: int | None,
) -> None:
    engine, _actor_id, master_key = workflow_database
    source = load_dev_suite().scenarios[0]
    action = source.input.actions[0].model_copy(
        update={
            "at": InjectionPoint(
                kind="safe_point",
                safe_point=safe_point,
                capability=capability,
                ordinal=ordinal,
            )
        }
    )
    scenario = source.model_copy(
        update={"input": source.input.model_copy(update={"actions": [action]})}
    )
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        executor = PostgresInteractiveGoalExecutor(
            repo_root=scenario_path_root(),
            database_url=engine.url.render_as_string(hide_password=False),
            api_key="fake-interactive-secret",
            expected_model_id="deepseek-v4-pro",
            expected_prompt_version="casefile-chat-v18",
            provider_factory=lambda document, _secret: _InteractiveFakeProvider(document),
        )
        try:
            evidence = executor.execute_interactive_trial(scenario, trial_no=1)
        finally:
            executor.close()
    assert evidence["completed"] is True
    assert evidence["protocol_valid"] is True
    assert evidence["delivery_valid"] is True
    assert evidence["amendment_valid"] is True
    assert evidence["safe_point_consumed"] is True
    assert evidence["capability_starts_before_consumption"] == 0
    assert evidence["model_evidence_complete"] is True
    assert evidence["exact_model"] is True
    assert evidence["exact_prompt"] is True, (
        evidence["observed_task_prompt_versions"],
        evidence["observed_call_prompt_versions"],
    )
    audit = evidence["audit"]
    assert canonical_hash(
        {key: value for key, value in audit.items() if key != "audit_fingerprint"}
    ) == audit["audit_fingerprint"]
    assert audit["model_calls"]
    assert audit["attempts"]
    assert evidence["violations"] == ()


@pytest.mark.parametrize("family", tuple(FAMILY_DISTRIBUTION))
def test_public_dev_suite_executes_every_interactive_family(
    workflow_database: tuple[Engine, int, str],
    family: str,
) -> None:
    engine, _actor_id, master_key = workflow_database
    scenario = next(item for item in load_dev_suite().scenarios if item.family == family)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        executor = PostgresInteractiveGoalExecutor(
            repo_root=scenario_path_root(),
            database_url=engine.url.render_as_string(hide_password=False),
            api_key="fake-interactive-secret",
            expected_model_id="deepseek-v4-pro",
            expected_prompt_version="casefile-chat-v18",
            provider_factory=lambda document, _secret: _InteractiveFamilyFakeProvider(
                document, family
            ),
        )
        try:
            evidence = executor.execute_interactive_trial(scenario, trial_no=1)
        finally:
            executor.close()
    assert (
        evidence["completed"]
        and evidence["protocol_valid"]
        and evidence["delivery_valid"]
        and evidence["amendment_valid"]
        and evidence["invalidation_valid"]
        and evidence["final_state_valid"]
        and not evidence["violations"]
    ), {
        "completed": evidence["completed"],
        "quiescent": evidence["quiescent"],
        "failures": evidence["failures"],
        "violations": evidence["violations"],
        "goals": evidence["audit"]["goal_sessions"],
        "tasks": evidence["audit"]["task_runs"],
        "calls": evidence["audit"]["model_calls"],
        "deliveries": evidence["audit"]["deliveries"],
    }


@pytest.mark.parametrize("intervention", ("fifo", "early_follow_up"))
def test_interactive_executor_covers_queued_fifo_and_early_follow_up_rejection(
    workflow_database: tuple[Engine, int, str],
    intervention: str,
) -> None:
    engine, _actor_id, master_key = workflow_database
    source = load_dev_suite().scenarios[0]
    if intervention == "fifo":
        action = InteractiveAction.model_validate(
            {
                "at": {
                    "kind": "safe_point",
                    "safe_point": "after_capability",
                    "capability": "analyze",
                    "ordinal": 1,
                },
                "action": "messages",
                "messages": [
                    {"delivery_mode": "steer", "message": "先只核对死亡时间。"},
                    {"delivery_mode": "steer", "message": "随后补充审计证词顺序。"},
                ],
            }
        )
        outcomes = [
            InteractiveExpectedMessageOutcome(
                delivery_mode="steer",
                result="accepted",
                final_delivery_status="consumed",
            ),
            InteractiveExpectedMessageOutcome(
                delivery_mode="steer",
                result="accepted",
                final_delivery_status="consumed",
            ),
        ]
        effects = source.oracle.effects.model_copy(
            update={"revision_count_min": 3, "min_task_slices": 3}
        )
    else:
        action = InteractiveAction.model_validate(
            {
                "at": {
                    "kind": "safe_point",
                    "safe_point": "after_capability",
                    "capability": "analyze",
                    "ordinal": 1,
                },
                "action": "messages",
                "messages": [
                    {
                        "delivery_mode": "follow_up",
                        "message": "当前目标未完成时不要把这条跟进排入队列。",
                    }
                ],
            }
        )
        outcomes = [
            InteractiveExpectedMessageOutcome(
                delivery_mode="follow_up",
                result="rejected",
                error_code="agent_goal_state_conflict",
            )
        ]
        effects = source.oracle.effects.model_copy(
            update={
                "revision_count_min": 1,
                "amendment_kinds": [],
                "min_task_slices": 1,
            }
        )
    scenario = source.model_copy(
        update={
            "scenario_id": f"interactive_dev_{intervention}",
            "input": source.input.model_copy(update={"actions": [action]}),
            "oracle": source.oracle.model_copy(
                update={
                    "effects": effects,
                    "message_outcomes": outcomes,
                    "forbidden": [
                        "lost_delivery",
                        "reordered_delivery",
                        "duplicate_continuation",
                        "midrun_follow_up_queued",
                    ],
                }
            ),
        }
    )
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        executor = PostgresInteractiveGoalExecutor(
            repo_root=scenario_path_root(),
            database_url=engine.url.render_as_string(hide_password=False),
            api_key="fake-interactive-secret",
            expected_model_id="deepseek-v4-pro",
            expected_prompt_version="casefile-chat-v18",
            provider_factory=lambda document, _secret: _InteractiveFamilyFakeProvider(
                document, "steer_refine"
            ),
        )
        try:
            evidence = executor.execute_interactive_trial(scenario, trial_no=1)
        finally:
            executor.close()
    assert evidence["completed"] is True
    assert evidence["delivery_valid"] is True
    assert evidence["safe_point_consumed"] is True
    assert evidence["capability_starts_before_consumption"] == 0
    assert evidence["violations"] == ()


def scenario_path_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _understanding() -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并审计当前时间线",
            "confidence": 1.0,
            "ambiguous": False,
            "missing_info": [],
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析",
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计当前时间线",
                    "depends_on": [1],
                },
            ],
        }
    )


def _understanding_for_message(message: str) -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": message,
            "confidence": 1.0,
            "ambiguous": False,
            "missing_info": [],
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": message,
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": message,
                    "depends_on": [1],
                },
            ],
        }
    )


def _clarification_understanding(message: str) -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "明确人物动机修改方向",
            "confidence": 0.95,
            "ambiguous": True,
            "missing_info": ["需要明确动机方向"],
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": message,
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": message,
                    "depends_on": [1],
                }
            ],
        }
    )


def _mutation_understanding(message: str) -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并提出安全修改",
            "confidence": 1.0,
            "ambiguous": False,
            "missing_info": [],
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": message,
                },
                {
                    "kind": "mutation_proposal",
                    "target_state": "baseline",
                    "source_excerpt": message,
                    "depends_on": [1],
                },
            ],
        }
    )


def _amendment() -> GoalAmendmentOutput:
    return GoalAmendmentOutput.model_validate(
        {
            "amendment_kind": "refine",
            "goal": "只关注死亡时间并完成既定义务",
            "obligations": [
                {
                    "obligation_ref": "obl_1",
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析",
                },
                {
                    "obligation_ref": "obl_2",
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计当前时间线",
                    "depends_on": ["obl_1"],
                },
            ],
        }
    )


def _decision(capability: str, obligation_id: str) -> GoalDecisionOutput:
    return GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": "obl_1", "status": "pending"},
                {"obligation_id": "obl_2", "status": "pending"},
            ],
            "action": {
                "action": "invoke_capability",
                "capability": capability,
                "obligation_ids": [obligation_id],
                "target_state": "baseline",
            },
        }
    )


def _finish() -> GoalDecisionOutput:
    return GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": "obl_1", "status": "completed"},
                {"obligation_id": "obl_2", "status": "completed"},
            ],
            "action": {"action": "finish"},
        }
    )
