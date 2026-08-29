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
    InjectionPoint,
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
                    "source_excerpt": "分析时间线",
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计其中的问题",
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
                    "source_excerpt": "分析时间线",
                },
                {
                    "obligation_ref": "obl_2",
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计其中的问题",
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
