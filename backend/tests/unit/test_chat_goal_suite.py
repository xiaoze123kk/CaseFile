from casefile.benchmark.chat_goal_suite import (
    chat_goal_suite_fingerprint,
    load_chat_goal_suite,
    reference_decisions,
)


def test_chat_goal_suite_is_complete_and_deterministic() -> None:
    suite = load_chat_goal_suite()
    assert len(suite.tasks) == 24
    assert suite.trials_per_task == 3
    assert sum(task.expected_path == "goal" for task in suite.tasks) == 16
    assert sum(task.expected_path == "reject" for task in suite.tasks) == 4
    assert sum(task.expected_path == "single" for task in suite.tasks) == 4
    assert chat_goal_suite_fingerprint(suite) == chat_goal_suite_fingerprint(
        load_chat_goal_suite()
    )
    assert len(reference_decisions(suite)) == 24
