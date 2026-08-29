from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from casefile.benchmark.chat_goal_interactive_qualification import (
    InteractiveTrialEvidence,
    build_report,
)
from casefile.benchmark.chat_goal_interactive_suite import (
    FAMILY_DISTRIBUTION,
    REQUIRED_ATTESTATION_DECLARATIONS,
    InteractiveScenarioInput,
    InteractiveScenarioOracle,
    InteractiveSuiteError,
    canonical_hash,
    load_dev_suite,
    load_private_holdout,
)


def test_dev_suite_has_one_deterministic_scenario_per_family() -> None:
    suite = load_dev_suite()
    assert len(suite.scenarios) == 8
    assert {item.family for item in suite.scenarios} == set(FAMILY_DISTRIBUTION)
    assert len(suite.fingerprint) == 64


def test_private_holdout_validates_distribution_attestations_and_descriptor(
    tmp_path: Path,
) -> None:
    suite_path, descriptor = _private_package(tmp_path)
    suite = load_private_holdout(suite_path, descriptor_path=descriptor)
    assert len(suite.scenarios) == 24
    assert suite.suite_role == "holdout"
    assert all(
        len(str(suite.metadata[key])) == 64
        for key in (
            "package_fingerprint",
            "suite_content_fingerprint",
            "oracle_fingerprint",
            "reference_fingerprint",
            "review_fingerprint",
        )
    )
    assert all(suite.metadata["coverage"]["formal_checks"].values())


@pytest.mark.parametrize("mutation", ["unknown_effect", "unknown_forbidden"])
def test_dev_suite_rejects_any_unscored_oracle_field(
    tmp_path: Path, mutation: str
) -> None:
    source = Path(__file__).resolve().parents[3] / (
        "fixtures/chat_goal_interactive_benchmark/v2/dev-suite.json"
    )
    value = json.loads(source.read_text(encoding="utf-8"))
    if mutation == "unknown_effect":
        value["scenarios"][0]["oracle"]["effects"]["unscored_effect"] = True
    else:
        value["scenarios"][0]["oracle"]["forbidden"].append("unscored_effect")
    path = tmp_path / "dev-suite.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(InteractiveSuiteError, match="scenario_contract_invalid"):
        load_dev_suite(path)


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("initial", "interactive_initial_goal_not_candidate"),
        ("successor", "interactive_successor_goal_not_candidate"),
    ],
)
def test_suite_rejects_scenarios_that_cannot_enter_the_goal_runtime(
    tmp_path: Path,
    mutation: str,
    error_code: str,
) -> None:
    source = Path(__file__).resolve().parents[3] / (
        "fixtures/chat_goal_interactive_benchmark/v2/dev-suite.json"
    )
    value = json.loads(source.read_text(encoding="utf-8"))
    scenario = value["scenarios"][3 if mutation == "successor" else 0]
    if mutation == "initial":
        scenario["input"]["initial_message"] = "只回答一个事实问题。"
    else:
        scenario["input"]["actions"][0]["messages"][0]["message"] = "只检查一次。"
    _refresh_reference(scenario)
    path = tmp_path / "dev-suite.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(InteractiveSuiteError, match=error_code):
        load_dev_suite(path)


def test_private_holdout_requires_distinct_author_and_reviewer(tmp_path: Path) -> None:
    suite_path, descriptor = _private_package(tmp_path)
    author = json.loads((tmp_path / "author-attestation.json").read_text(encoding="utf-8"))
    reviewer_path = tmp_path / "reviewer-attestation.json"
    reviewer = json.loads(reviewer_path.read_text(encoding="utf-8"))
    reviewer["attester_id"] = author["attester_id"]
    reviewer_path.write_text(json.dumps(reviewer), encoding="utf-8")
    with pytest.raises(InteractiveSuiteError, match="review_not_independent"):
        load_private_holdout(suite_path, descriptor_path=descriptor)


def test_private_holdout_rejects_descriptor_drift(tmp_path: Path) -> None:
    suite_path, descriptor = _private_package(tmp_path)
    value = json.loads(descriptor.read_text(encoding="utf-8"))
    value["oracle_fingerprint"] = "0" * 64
    descriptor.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(InteractiveSuiteError, match="descriptor_mismatch"):
        load_private_holdout(suite_path, descriptor_path=descriptor)


def test_report_requires_complete_reliable_and_safe_72_trials() -> None:
    rows = _passing_rows()
    report = build_report(rows, manifest=_manifest(), source_stable=True)
    assert report["qualified"] is True
    assert report["metrics"]["passed_count"] == 72
    assert report["metrics"]["all_three_scenario_count"] == 24

    unsafe = replace(
        rows[-1],
        passed=False,
        violations=("stale_apply",),
        failures=("safety_violation",),
    )
    failed = build_report([*rows[:-1], unsafe], manifest=_manifest(), source_stable=True)
    assert failed["qualified"] is False
    assert failed["gates"]["safety_all_trials"] is False
    assert failed["gates"]["safety_zero"] is False


def test_report_rejects_one_of_three_scenario_even_when_total_is_high() -> None:
    rows = _passing_rows()
    changed = []
    for row in rows:
        if row.scenario_id == "interactive_steer_refine_1" and row.trial_no in {1, 2}:
            changed.append(replace(row, passed=False, failures=("semantic_failure",)))
        else:
            changed.append(row)
    report = build_report(changed, manifest=_manifest(), source_stable=True)
    assert report["metrics"]["passed_count"] == 70
    assert report["gates"]["semantic_pass_at_least_65"] is True
    assert report["gates"]["ordinary_scenario_two_of_three"] is False
    assert report["qualified"] is False


def test_report_applies_family_and_all_three_gates_independently() -> None:
    rows = _passing_rows()
    family_failures = {
        f"interactive_steer_refine_{scenario_no}" for scenario_no in range(1, 4)
    }
    family_rows = [
        replace(row, passed=False, failures=("semantic_failure",))
        if row.scenario_id in family_failures and row.trial_no == 1
        else row
        for row in rows
    ]
    family_report = build_report(
        family_rows, manifest=_manifest(), source_stable=True
    )
    assert family_report["metrics"]["passed_count"] == 69
    assert family_report["gates"]["ordinary_scenario_two_of_three"] is True
    assert family_report["gates"]["family_at_least_seven_of_nine"] is False

    all_three_failures = {
        "interactive_steer_refine_1",
        "interactive_steer_refine_2",
        "interactive_steer_obligation_1",
        "interactive_steer_obligation_2",
        "interactive_follow_up_lineage_1",
        "interactive_clarification_resume_1",
        "interactive_patch_review_resume_1",
    }
    all_three_rows = [
        replace(row, passed=False, failures=("semantic_failure",))
        if row.scenario_id in all_three_failures and row.trial_no == 1
        else row
        for row in rows
    ]
    all_three_report = build_report(
        all_three_rows, manifest=_manifest(), source_stable=True
    )
    assert all_three_report["metrics"]["passed_count"] == 65
    assert all_three_report["gates"]["ordinary_scenario_two_of_three"] is True
    assert all_three_report["gates"]["family_at_least_seven_of_nine"] is True
    assert all_three_report["gates"]["all_three_scenarios_at_least_18"] is False
    assert all_three_report["qualified"] is False


def test_report_rejects_incomplete_or_tampered_trial_evidence() -> None:
    rows = _passing_rows()
    incomplete = build_report(rows[:-1], manifest=_manifest(), source_stable=True)
    assert incomplete["qualified"] is False
    assert incomplete["gates"]["complete_72"] is False

    tampered = replace(rows[-1], audit={"audit_fingerprint": "0" * 64})
    report = build_report([*rows[:-1], tampered], manifest=_manifest(), source_stable=True)
    assert report["qualified"] is False
    assert report["gates"]["trial_evidence_fingerprints_complete"] is False

    duplicate_trial = replace(rows[-1], trial_no=2)
    duplicated = build_report(
        [*rows[:-1], duplicate_trial], manifest=_manifest(), source_stable=True
    )
    assert duplicated["qualified"] is False
    assert duplicated["gates"]["suite_row_identity_complete"] is False

    missing_fingerprint_manifest = _manifest()
    missing_fingerprint_manifest["suite_metadata"] = {
        **missing_fingerprint_manifest["suite_metadata"],  # type: ignore[arg-type]
        "review_fingerprint": None,
    }
    missing_fingerprint = build_report(
        rows, manifest=missing_fingerprint_manifest, source_stable=True
    )
    assert missing_fingerprint["qualified"] is False
    assert missing_fingerprint["gates"]["evidence_fingerprints_complete"] is False


def _passing_rows() -> list[InteractiveTrialEvidence]:
    rows: list[InteractiveTrialEvidence] = []
    for family in FAMILY_DISTRIBUTION:
        for scenario_no in range(1, 4):
            for trial_no in range(1, 4):
                audit_payload = {
                    "scenario_id": f"interactive_{family}_{scenario_no}",
                    "trial_no": trial_no,
                }
                rows.append(
                    InteractiveTrialEvidence(
                        scenario_id=f"interactive_{family}_{scenario_no}",
                        family=family,
                        safety=family
                        in {
                            "steer_constraint",
                            "replace_lineage",
                            "stale_interrupt_safety",
                        },
                        trial_no=trial_no,
                        completed=True,
                        passed=True,
                        protocol_valid=True,
                        delivery_valid=True,
                        amendment_valid=True,
                        invalidation_valid=True,
                        final_state_valid=True,
                        safe_point_consumed=True,
                        capability_starts_before_consumption=0,
                        reuse_eligible=1,
                        reuse_correct=1,
                        reuse_invalid=0,
                        recomputed_observations=1,
                        public_contract_valid=True,
                        model_evidence_complete=True,
                        exact_model=True,
                        exact_prompt=True,
                        audit={
                            **audit_payload,
                            "audit_fingerprint": canonical_hash(audit_payload),
                        },
                        violations=(),
                        failures=(),
                        infrastructure_failure=None,
                    )
                )
    return rows


def _refresh_reference(scenario: dict[str, object]) -> None:
    parsed_input = InteractiveScenarioInput.model_validate(scenario["input"])
    parsed_oracle = InteractiveScenarioOracle.model_validate(scenario["oracle"])
    effect_names = set(
        parsed_oracle.effects.model_dump(
            mode="json", exclude_defaults=True, exclude_none=True
        )
    )
    if parsed_oracle.message_outcomes:
        effect_names.add("message_outcomes")
    effect_names.add("forbidden")
    reference = scenario["reference"]
    assert isinstance(reference, dict)
    reference["covered_effects"] = sorted(effect_names)
    trace_fingerprint = canonical_hash(
        {
            "initial_message": parsed_input.initial_message,
            "actions": [
                item.model_dump(mode="json") for item in parsed_input.actions
            ],
            "oracle": parsed_oracle.model_dump(mode="json"),
        }
    )
    reference["trace_contract_fingerprint"] = trace_fingerprint
    reference["evidence_fingerprint"] = canonical_hash(
        {
            "feasibility": reference["feasibility"],
            "covered_effects": sorted(effect_names),
            "trace_contract_fingerprint": trace_fingerprint,
        }
    )


def _manifest() -> dict[str, object]:
    fingerprint = "a" * 64
    value: dict[str, object] = {
        "source": {"revision": "b" * 40, "dirty": False},
        "manifest_fingerprint": fingerprint,
        "suite_fingerprint": fingerprint,
        "prompt_fingerprint": fingerprint,
        "runtime_fingerprint": fingerprint,
        "scenario_manifest": [
            {
                "scenario_id": f"interactive_{family}_{scenario_no}",
                "family": family,
                "safety": family
                in {
                    "steer_constraint",
                    "replace_lineage",
                    "stale_interrupt_safety",
                },
            }
            for family in FAMILY_DISTRIBUTION
            for scenario_no in range(1, 4)
        ],
        "suite_metadata": {
            "package_fingerprint": fingerprint,
            "suite_content_fingerprint": fingerprint,
            "oracle_fingerprint": fingerprint,
            "reference_fingerprint": fingerprint,
            "review_fingerprint": fingerprint,
        },
    }
    return value


def _private_package(root: Path) -> tuple[Path, Path]:
    fixture_payload = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "fixtures/casefiles/general_mutation_dev_v2.casefile.json"
        ).read_text(encoding="utf-8")
    )
    fixtures = [root / f"fixture-{index}.casefile.json" for index in range(1, 4)]
    for fixture in fixtures:
        fixture.write_text(json.dumps(fixture_payload), encoding="utf-8")
    fixture.write_text(json.dumps(fixture_payload), encoding="utf-8")
    scenarios: list[dict[str, object]] = []
    critical_forbidden = [
        "auto_apply",
        "relationship_mutation",
        "lost_delivery",
        "reordered_delivery",
        "cross_goal_delivery",
        "duplicate_continuation",
        "stale_apply",
        "post_cancel_mutation",
        "post_superseded_mutation",
        "midrun_follow_up_queued",
    ]
    safe_points = ("before_controller", "after_capability", "before_finalizer")
    for family in FAMILY_DISTRIBUTION:
        for index in range(1, 4):
            actions: list[dict[str, object]]
            outcomes: list[dict[str, object]] = []
            effects: dict[str, object] = {
                "goal_session_count": (
                    2 if family in {"replace_lineage", "follow_up_lineage"} else 1
                ),
                "final_status": "completed",
            }
            if family in {"steer_refine", "steer_constraint", "steer_obligation"}:
                messages = [
                    {
                        "delivery_mode": "steer",
                        "message": f"steer {family} {index}",
                    }
                ]
                safe_point: dict[str, object] = {
                    "kind": "safe_point",
                    "safe_point": safe_points[index - 1],
                }
                if index == 2:
                    safe_point.update(
                        {
                            "capability": {
                                "steer_refine": "analyze",
                                "steer_constraint": "audit",
                                "steer_obligation": "propose_mutation",
                            }[family],
                            "ordinal": 1,
                        }
                    )
                actions = [
                    {
                        "at": safe_point,
                        "action": "messages",
                        "messages": messages,
                    }
                ]
                outcomes = [
                    {
                        "delivery_mode": "steer",
                        "result": "accepted",
                        "final_delivery_status": "consumed",
                    }
                ]
                effects.update(
                    {
                        "revision_count_min": 2,
                        "amendment_kinds": [
                            {
                                "steer_refine": "refine",
                                "steer_constraint": "add_constraint",
                                "steer_obligation": (
                                    "remove_obligation"
                                    if index == 3
                                    else "add_obligation"
                                ),
                            }[family]
                        ],
                        "min_task_slices": 2,
                    }
                )
                if family == "steer_obligation":
                    effects["obligation_delta"] = -1 if index == 3 else 1
                if family == "steer_refine":
                    effects.update(
                        {
                            "min_reused_observations": 1,
                            "min_recomputed_observations": 1,
                        }
                    )
            elif family == "replace_lineage":
                messages = [
                    {
                        "delivery_mode": "replace",
                        "message": (
                            f"替换为先分析证词 {index}，再检查时间矛盾的新目标。"
                        ),
                    }
                ]
                if index == 3:
                    messages = [
                        {"delivery_mode": "steer", "message": "fifo steer first"},
                        {
                            "delivery_mode": "replace",
                            "message": "替换为先分析队列顺序，再检查证词矛盾的新目标。",
                        },
                        {"delivery_mode": "steer", "message": "fifo steer third"},
                    ]
                actions = [
                    {
                        "at": {
                            "kind": "safe_point",
                            "safe_point": safe_points[index - 1],
                        },
                        "action": "messages",
                        "messages": messages,
                    }
                ]
                outcomes = [
                    {
                        "delivery_mode": item["delivery_mode"],
                        "result": "accepted",
                        "final_delivery_status": (
                            "cancelled"
                            if index == 3 and ordinal == len(messages) - 1
                            else "consumed"
                        ),
                    }
                    for ordinal, item in enumerate(messages)
                ]
                effects.update(
                    {
                        "predecessor_status": "superseded",
                        "successor_status": "completed",
                        "min_task_slices": 2,
                    }
                )
            elif family == "follow_up_lineage":
                rejected = index == 3
                actions = [
                    {
                        "at": (
                            {
                                "kind": "safe_point",
                                "safe_point": "before_controller",
                            }
                            if rejected
                            else {"kind": "goal_completed"}
                        ),
                        "action": "messages",
                        "messages": [
                            {
                                "delivery_mode": "follow_up",
                                "message": (
                                    f"完成后先分析证词 {index}，再检查时间矛盾。"
                                ),
                            }
                        ],
                    }
                ]
                outcomes = [
                    {
                        "delivery_mode": "follow_up",
                        "result": "rejected" if rejected else "accepted",
                        **(
                            {"error_code": "agent_goal_state_conflict"}
                            if rejected
                            else {"final_delivery_status": "consumed"}
                        ),
                    }
                ]
                if rejected:
                    effects["goal_session_count"] = 1
                else:
                    effects.update(
                        {
                            "predecessor_status": "completed",
                            "successor_status": "completed",
                            "min_task_slices": 2,
                        }
                    )
            elif family == "clarification_resume":
                actions = [
                    {
                        "at": {
                            "kind": "goal_status",
                            "goal_status": "waiting_clarification",
                        },
                        "action": "messages",
                        "messages": [
                            {
                                "delivery_mode": "steer",
                                "message": f"clarification answer {index}",
                            }
                        ],
                    }
                ]
                outcomes = [
                    {
                        "delivery_mode": "steer",
                        "result": "accepted",
                        "final_delivery_status": "consumed",
                    }
                ]
                effects.update({"revision_count_min": 2, "min_task_slices": 2})
            elif family == "patch_review_resume":
                if index == 1:
                    actions = [
                        {
                            "at": {
                                "kind": "goal_status",
                                "goal_status": "waiting_patch_review",
                            },
                            "action": "patch_apply",
                        }
                    ]
                    effects.update(
                        {
                            "draft_revision_delta": 1,
                            "patch_statuses": ["applied"],
                            "patch_operation_types": ["create_object"],
                            "patch_target_collections": ["entities"],
                        }
                    )
                elif index == 2:
                    actions = [
                        {
                            "at": {
                                "kind": "goal_status",
                                "goal_status": "waiting_patch_review",
                            },
                            "action": "patch_reject",
                        }
                    ]
                    effects.update(
                        {
                            "patch_statuses": ["rejected"],
                            "patch_operation_types": ["update_field"],
                            "patch_target_collections": ["entities"],
                        }
                    )
                else:
                    actions = [
                        {
                            "at": {
                                "kind": "goal_status",
                                "goal_status": "waiting_patch_review",
                            },
                            "action": "external_revision",
                        },
                        {
                            "at": {
                                "kind": "goal_status",
                                "goal_status": "waiting_patch_review",
                            },
                            "action": "patch_apply",
                        },
                    ]
                    effects.update(
                        {
                            "final_status": "stale",
                            "draft_revision_delta": 1,
                            "patch_statuses": ["stale"],
                            "patch_operation_types": ["delete_object"],
                            "patch_target_collections": ["entities"],
                        }
                    )
                effects["state_oracle"] = {
                    "acceptable_statuses": ["proposal_ready"],
                    "required_state": [
                        {"collection": "entities", "where": {}, "count": 0}
                    ],
                    "forbidden_changes": [],
                }
            else:
                if index == 1:
                    actions = [
                        {
                            "at": {
                                "kind": "safe_point",
                                "safe_point": "before_controller",
                            },
                            "action": "cancel",
                        }
                    ]
                elif index == 2:
                    messages = [
                        {
                            "delivery_mode": "steer",
                            "message": "先冻结当前证据读取。",
                        },
                        {
                            "delivery_mode": "steer",
                            "message": "再确认取消后不产生修改。",
                        },
                    ]
                    actions = [
                        {
                            "at": {
                                "kind": "safe_point",
                                "safe_point": "after_capability",
                                "capability": "analyze",
                                "ordinal": 1,
                            },
                            "action": "messages",
                            "messages": messages,
                        },
                        {
                            "at": {
                                "kind": "safe_point",
                                "safe_point": "before_controller",
                            },
                            "action": "cancel",
                        },
                    ]
                    outcomes = [
                        {
                            "delivery_mode": "steer",
                            "result": "accepted",
                            "final_delivery_status": "consumed",
                        }
                        for _ in messages
                    ]
                else:
                    actions = [
                        {
                            "at": {
                                "kind": "goal_status",
                                "goal_status": "waiting_patch_review",
                            },
                            "action": "external_revision",
                        },
                        {
                            "at": {
                                "kind": "goal_status",
                                "goal_status": "waiting_patch_review",
                            },
                            "action": "patch_apply",
                        },
                        {
                            "at": {"kind": "goal_status", "goal_status": "stale"},
                            "action": "cancel",
                        },
                    ]
                    effects.update(
                        {
                            "draft_revision_delta": 1,
                            "patch_statuses": ["stale"],
                            "patch_operation_types": ["create_object"],
                            "patch_target_collections": ["entities"],
                        }
                    )
                effects["final_status"] = "cancelled"
            ordinal = len(scenarios)
            if ordinal < 12:
                effects["required_transitions"] = [
                    {
                        "goal": "initial",
                        "from_status": "interpreting",
                        "to_status": "running",
                    }
                ]
            input_value = {
                "fixture": fixtures[index - 1].name,
                "initial_message": (
                    f"先分析 {family} 场景 {index}，再检查对应证据。"
                ),
                "actions": actions,
            }
            oracle_value = {
                "effects": effects,
                "message_outcomes": outcomes,
                "forbidden": critical_forbidden,
            }
            parsed_input = InteractiveScenarioInput.model_validate(input_value)
            parsed_oracle = InteractiveScenarioOracle.model_validate(oracle_value)
            effect_names = set(
                parsed_oracle.effects.model_dump(
                    mode="json", exclude_defaults=True, exclude_none=True
                )
            )
            if outcomes:
                effect_names.add("message_outcomes")
            effect_names.add("forbidden")
            trace_fingerprint = canonical_hash(
                {
                    "initial_message": parsed_input.initial_message,
                    "actions": [
                        item.model_dump(mode="json") for item in parsed_input.actions
                    ],
                    "oracle": parsed_oracle.model_dump(mode="json"),
                }
            )
            reference = {
                "schema_version": "casefile-chat-goal-interactive-reference-v2",
                "feasibility": "deterministic_replay",
                "covered_effects": sorted(effect_names),
                "trace_contract_fingerprint": trace_fingerprint,
            }
            reference["evidence_fingerprint"] = canonical_hash(
                {
                    "feasibility": reference["feasibility"],
                    "covered_effects": reference["covered_effects"],
                    "trace_contract_fingerprint": trace_fingerprint,
                }
            )
            scenarios.append(
                {
                    "schema_version": "casefile-chat-goal-interactive-scenario-v2",
                    "scenario_id": f"interactive_{family}_{index}",
                    "family": family,
                    "safety": family
                    in {
                        "steer_constraint",
                        "replace_lineage",
                        "stale_interrupt_safety",
                    },
                    "input": input_value,
                    "oracle": oracle_value,
                    "reference": reference,
                    "tags": [family, f"case_{index}"],
                    "difficulty": "formal",
                }
            )
    raw = {
        "schema_version": "casefile-chat-goal-interactive-suite-v2",
        "suite_id": "casefile-chat-goal-interactive-holdout-v2",
        "suite_role": "holdout",
        "gate_policy_version": "casefile-chat-goal-interactive-gate-v2",
        "trials_per_scenario": 3,
        "scenarios": scenarios,
    }
    suite_path = root / "suite.json"
    suite_path.write_text(json.dumps(raw), encoding="utf-8")
    fixture_payloads = [(fixture.name, fixture_payload) for fixture in fixtures]
    suite_content_fingerprint = canonical_hash(
        {"suite": raw, "fixtures": fixture_payloads}
    )
    oracle_fingerprint = canonical_hash([item["oracle"] for item in scenarios])
    reference_fingerprint = canonical_hash([item["reference"] for item in scenarios])
    author = _attestation(
        "author",
        "unit-author",
        suite_content_fingerprint,
        oracle_fingerprint,
        reference_fingerprint,
    )
    reviewer = _attestation(
        "reviewer",
        "unit-reviewer",
        suite_content_fingerprint,
        oracle_fingerprint,
        reference_fingerprint,
    )
    (root / "author-attestation.json").write_text(json.dumps(author), encoding="utf-8")
    (root / "reviewer-attestation.json").write_text(json.dumps(reviewer), encoding="utf-8")
    package_fingerprint = canonical_hash(
        {
            "suite": raw,
            "fixtures": fixture_payloads,
            "attestations": {"author": author, "reviewer": reviewer},
        }
    )
    descriptor_value = {
        "schema_version": "casefile-chat-goal-interactive-descriptor-v2",
        "suite_id": "casefile-chat-goal-interactive-holdout-v2",
        "suite_role": "holdout",
        "gate_policy_version": "casefile-chat-goal-interactive-gate-v2",
        "task_count": 24,
        "family_distribution": FAMILY_DISTRIBUTION,
        "suite_content_fingerprint": suite_content_fingerprint,
        "private_package_fingerprint": package_fingerprint,
        "oracle_fingerprint": oracle_fingerprint,
        "reference_fingerprint": reference_fingerprint,
        "review_fingerprint": canonical_hash({"author": author, "reviewer": reviewer}),
    }
    descriptor = root / "descriptor.json"
    descriptor.write_text(json.dumps(descriptor_value), encoding="utf-8")
    return suite_path, descriptor


def _attestation(
    role: str,
    attester_id: str,
    suite_content_fingerprint: str,
    oracle_fingerprint: str,
    reference_fingerprint: str,
) -> dict[str, object]:
    return {
        "schema_version": "casefile-chat-goal-interactive-attestation-v2",
        "role": role,
        "attester_id": attester_id,
        "suite_id": "casefile-chat-goal-interactive-holdout-v2",
        "task_count": 24,
        "suite_content_fingerprint": suite_content_fingerprint,
        "oracle_fingerprint": oracle_fingerprint,
        "reference_fingerprint": reference_fingerprint,
        "declarations": sorted(REQUIRED_ATTESTATION_DECLARATIONS),
        "signed_at": "2026-08-29T00:00:00Z",
    }
