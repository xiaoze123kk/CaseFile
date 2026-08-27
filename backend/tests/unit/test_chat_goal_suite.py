import json

from casefile.benchmark.chat_goal_suite import (
    CHAT_GOAL_SUITE_VERSION,
    DEFAULT_CHAT_GOAL_SUITE,
    chat_goal_suite_fingerprint,
    load_chat_goal_suite,
    reference_decisions,
)


def test_chat_goal_suite_is_complete_and_deterministic() -> None:
    suite = load_chat_goal_suite()
    assert suite.suite_version == CHAT_GOAL_SUITE_VERSION
    assert DEFAULT_CHAT_GOAL_SUITE.parent.name == "v2"
    assert len(suite.tasks) == 24
    assert suite.trials_per_task == 3
    assert sum(task.expected_path == "goal" for task in suite.tasks) == 16
    assert sum(task.expected_path == "reject" for task in suite.tasks) == 4
    assert sum(task.expected_path == "single" for task in suite.tasks) == 4
    assert chat_goal_suite_fingerprint(suite) == chat_goal_suite_fingerprint(
        load_chat_goal_suite()
    )
    assert len(reference_decisions(suite)) == 24


def test_goal_v2_patch_tasks_are_solvable_and_oracle_graded() -> None:
    suite = load_chat_goal_suite()
    patch_tasks = [task for task in suite.tasks if task.patch_expectation == "required"]
    invalid_v1_targets = ("夜访", "林舟", "clue_decoy_1", "event_duplicate_1")

    assert len(patch_tasks) == 11
    assert all(task.oracle is not None for task in patch_tasks)
    assert not any(
        target in task.message for task in suite.tasks for target in invalid_v1_targets
    )


def test_single_update_oracle_targets_existing_fixture_event() -> None:
    suite = load_chat_goal_suite()
    task = next(item for item in suite.tasks if item.task_id == "goal_single_update")
    fixture_path = (
        DEFAULT_CHAT_GOAL_SUITE.parents[2]
        / "casefiles"
        / "general_mutation_dev_v2.casefile.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert task.patch_expectation == "required"
    assert task.oracle is not None
    assert any(
        event["id"] == "evt_restart_seven" and event["title"] == "系统第七次重启"
        for event in fixture["events"]
    )
