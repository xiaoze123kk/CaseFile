from __future__ import annotations

import pytest
from casefile.agent_runtime.goal.contracts import (
    GoalAmendmentOutput,
    GoalDecisionOutput,
    GoalObservation,
    GoalUnderstandingOutput,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.goal.policy import (
    GoalBudget,
    GoalPolicyError,
    apply_goal_amendment,
    complete_goal,
    freeze_goal,
    goal_capability_message,
    normalize_decision_plan,
    qualify_goal,
    stable_hash,
    validate_decision,
)

SOURCE = "先分析时间线，再审计矛盾；如果确认有问题，把事件标题改成夜访，然后复查修改结果。"


def understanding() -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析、审计、提出修改并复查",
            "confidence": 0.96,
            "ambiguous": False,
            "missing_info": [],
            "obligations": [
                {"kind": "analysis", "target_state": "baseline", "source_excerpt": "分析时间线"},
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计矛盾",
                    "depends_on": [1],
                },
                {
                    "kind": "mutation_proposal",
                    "target_state": "baseline",
                    "source_excerpt": "把事件标题改成夜访",
                    "depends_on": [2],
                },
                {
                    "kind": "audit",
                    "target_state": "candidate",
                    "source_excerpt": "复查修改结果",
                    "depends_on": [3],
                },
            ],
        }
    )


def observation(index: int, obligation_id: str, capability: str, target: str, **extra: object):
    payload = {
        "observation_id": f"obs_{index}",
        "capability": capability,
        "obligation_ids": [obligation_id],
        "target_state": target,
        "status": "completed",
        "summary": f"完成 {obligation_id}",
        "action_hash": stable_hash(["action", index]),
        "input_hash": stable_hash(["input", index]),
        "output_hash": stable_hash(["output", index]),
    }
    payload.update(extra)
    return GoalObservation.model_validate(payload)


def test_freeze_is_deterministic_and_qualifies() -> None:
    first = freeze_goal(understanding(), SOURCE)
    second = freeze_goal(understanding(), SOURCE)
    assert first == second
    assert [item.obligation_id for item in first.obligations] == [
        "obl_1",
        "obl_2",
        "obl_3",
        "obl_4",
    ]
    assert qualify_goal(understanding(), first, budget=GoalBudget()).qualified


def test_freeze_rejects_non_verbatim_authorization_and_forward_dependency() -> None:
    payload = understanding().model_dump(mode="json")
    payload["obligations"][2]["source_excerpt"] = "模型自己想改"
    with pytest.raises(GoalPolicyError, match="goal_source_excerpt_invalid"):
        freeze_goal(GoalUnderstandingOutput.model_validate(payload), SOURCE)
    payload = understanding().model_dump(mode="json")
    payload["obligations"][0]["depends_on"] = [2]
    with pytest.raises(GoalPolicyError, match="goal_dependency_invalid"):
        freeze_goal(GoalUnderstandingOutput.model_validate(payload), SOURCE)


def test_amendment_assigns_stable_new_keys_and_validates_new_excerpt() -> None:
    current = freeze_goal(understanding(), SOURCE)
    source = "再增加一项：复查新的证据链。"
    amendment = GoalAmendmentOutput.model_validate(
        {
            "amendment_kind": "add_obligation",
            "goal": "分析、审计、提出修改并复查新的证据链",
            "obligations": [
                {
                    "obligation_ref": item.obligation_id,
                    "kind": item.kind,
                    "target_state": item.target_state,
                    "source_excerpt": item.source_excerpt,
                    "depends_on": item.depends_on,
                }
                for item in current.obligations
            ]
            + [
                {
                    "obligation_ref": "new_1",
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "复查新的证据链",
                    "depends_on": ["obl_2"],
                }
            ],
        }
    )

    amended = apply_goal_amendment(
        current, amendment, source, budget=GoalBudget(max_plan_items=6)
    )

    assert [item.obligation_id for item in amended.obligations] == [
        "obl_1",
        "obl_2",
        "obl_3",
        "obl_4",
        "obl_5",
    ]
    assert amended.obligations[-1].depends_on == ["obl_2"]

    invalid = amendment.model_copy(
        update={
            "obligations": amendment.obligations[:-1]
            + [
                amendment.obligations[-1].model_copy(
                    update={"source_excerpt": "模型自行增加的检查"}
                )
            ]
        }
    )
    with pytest.raises(GoalPolicyError, match="goal_amendment_excerpt_invalid"):
        apply_goal_amendment(current, invalid, source, budget=GoalBudget())


def test_amendment_rejects_remove_shape_and_dependency_cycle() -> None:
    current = freeze_goal(understanding(), SOURCE)
    payload = {
        "amendment_kind": "refine",
        "goal": current.goal,
        "obligations": [
            {
                "obligation_ref": item.obligation_id,
                "kind": item.kind,
                "target_state": item.target_state,
                "source_excerpt": item.source_excerpt,
                "depends_on": item.depends_on,
            }
            for item in current.obligations[:-1]
        ],
    }
    with pytest.raises(GoalPolicyError, match="goal_amendment_shape_invalid"):
        apply_goal_amendment(
            current,
            GoalAmendmentOutput.model_validate(payload),
            "保持原目标",
            budget=GoalBudget(),
        )

    payload["amendment_kind"] = "remove_obligation"
    payload["removal_source_excerpt"] = "明确放弃最后一项"
    payload["obligations"][0]["depends_on"] = ["obl_2"]
    payload["obligations"][1]["depends_on"] = ["obl_1"]
    with pytest.raises(GoalPolicyError, match="goal_dependency_cycle"):
        apply_goal_amendment(
            current,
            GoalAmendmentOutput.model_validate(payload),
            "明确放弃最后一项",
            budget=GoalBudget(),
        )

    payload["obligations"][0]["depends_on"] = []
    payload["obligations"][1]["depends_on"] = ["obl_1"]
    payload["removal_source_excerpt"] = "模型虚构的放弃说明"
    with pytest.raises(GoalPolicyError, match="goal_amendment_excerpt_invalid"):
        apply_goal_amendment(
            current,
            GoalAmendmentOutput.model_validate(payload),
            "明确放弃最后一项",
            budget=GoalBudget(),
        )


def test_qualification_rejects_candidate_without_mutation_and_budget_overflow() -> None:
    payload = understanding().model_dump(mode="json")
    payload["obligations"] = [payload["obligations"][0], payload["obligations"][3]]
    payload["obligations"][1]["depends_on"] = [1]
    output = GoalUnderstandingOutput.model_validate(payload)
    frozen = freeze_goal(output, SOURCE)
    result = qualify_goal(output, frozen, budget=GoalBudget())
    assert not result.qualified
    assert "goal_candidate_before_mutation" in result.reason_codes


def test_decision_cannot_reference_unknown_or_wrong_capability() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    decision = GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": item.obligation_id, "status": "pending"}
                for item in frozen.obligations
            ],
            "action": {
                "action": "invoke_capability",
                "capability": "audit",
                "obligation_ids": ["obl_1"],
                "target_state": "baseline",
            },
        }
    )
    with pytest.raises(GoalPolicyError, match="goal_capability_blocked"):
        validate_decision(frozen, decision, ())


def test_capability_message_contains_only_authorized_obligation_excerpt() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    action = InvokeCapabilityAction(
        capability="propose_mutation",
        obligation_ids=["obl_3"],
        target_state="baseline",
    )

    assert goal_capability_message(frozen, action) == "把事件标题改成夜访"

    unknown = action.model_copy(update={"obligation_ids": ["obl_99"]})
    with pytest.raises(GoalPolicyError, match="goal_action_invalid"):
        goal_capability_message(frozen, unknown)


def test_non_authoritative_plan_items_are_rebuilt_from_server_facts() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    decision = GoalDecisionOutput.model_validate(
        {
            "plan_items": [{"obligation_id": "obl_1", "status": "pending"}],
            "action": {
                "action": "invoke_capability",
                "capability": "audit",
                "obligation_ids": ["obl_2"],
                "target_state": "baseline",
            },
        }
    )
    observations = (observation(1, "obl_1", "analyze", "baseline"),)

    normalized = normalize_decision_plan(frozen, decision, observations)

    assert [item.model_dump(mode="json") for item in normalized.plan_items] == [
        {"obligation_id": "obl_1", "status": "completed"},
        {"obligation_id": "obl_2", "status": "in_progress"},
        {"obligation_id": "obl_3", "status": "pending"},
        {"obligation_id": "obl_4", "status": "pending"},
    ]


def test_non_authoritative_action_is_rebuilt_from_first_ready_obligation() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    invalid = GoalDecisionOutput.model_validate(
        {
            "plan_items": [{"obligation_id": "obl_2", "status": "in_progress"}],
            "action": {
                "action": "invoke_capability",
                "capability": "audit",
                "obligation_ids": ["obl_2"],
                "target_state": "baseline",
            },
        }
    )

    normalized = normalize_decision_plan(frozen, invalid, ())

    assert normalized.action == InvokeCapabilityAction(
        capability="analyze",
        obligation_ids=["obl_1"],
        target_state="baseline",
    )
    validate_decision(frozen, normalized, ())


def test_completed_server_observations_override_repeated_model_action() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    repeated = GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": item.obligation_id, "status": "completed"}
                for item in frozen.obligations
            ],
            "action": {
                "action": "invoke_capability",
                "capability": "propose_mutation",
                "obligation_ids": ["obl_3"],
                "target_state": "baseline",
            },
        }
    )
    observations = tuple(
        observation(index, item.obligation_id, capability, item.target_state)
        for index, (item, capability) in enumerate(
            zip(
                frozen.obligations,
                ("analyze", "audit", "propose_mutation", "audit"),
                strict=True,
            ),
            start=1,
        )
    )

    normalized = normalize_decision_plan(frozen, repeated, observations)

    assert normalized.action.action == "finish"


def test_non_ambiguous_missing_info_text_does_not_veto_actionable_goal() -> None:
    output = understanding().model_copy(
        update={"missing_info": ["模型附带的非阻断说明"]}
    )
    frozen = freeze_goal(output, SOURCE)

    assert qualify_goal(output, frozen, budget=GoalBudget()).qualified is True

    ambiguous = output.model_copy(update={"ambiguous": True})
    result = qualify_goal(ambiguous, frozen, budget=GoalBudget())
    assert result.qualified is False
    assert "goal_missing_info" in result.reason_codes


def test_completion_requires_proof_and_candidate_hash_binding() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    candidate_hash = stable_hash("candidate")
    observations = (
        observation(1, "obl_1", "analyze", "baseline"),
        observation(2, "obl_2", "audit", "baseline"),
        observation(
            3,
            "obl_3",
            "propose_mutation",
            "baseline",
            candidate_hash=candidate_hash,
            mutation_proof_ref="planner:abc",
        ),
        observation(4, "obl_4", "audit", "candidate", candidate_hash=candidate_hash),
    )
    complete = complete_goal(frozen, observations)
    assert complete.allowed
    assert complete.missing_obligation_ids == []
    mismatched = observations[:-1] + (
        observation(4, "obl_4", "audit", "candidate", candidate_hash=stable_hash("other")),
    )
    assert "goal_candidate_hash_mismatch" in complete_goal(frozen, mismatched).reason_codes


def test_completion_detects_frozen_obligation_tampering() -> None:
    frozen = freeze_goal(understanding(), SOURCE)
    result = complete_goal(frozen, (), expected_obligations_hash=stable_hash("tampered"))
    assert not result.allowed
    assert "goal_obligations_hash_mismatch" in result.reason_codes
