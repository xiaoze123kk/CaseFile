"""Regression controls for incidental fixture time and valid answer formatting."""

from __future__ import annotations

from casefile.benchmark.chat_subagent_suite_v3 import (
    answer_classifications,
    build_suite_tasks,
    calibration_report,
    load_suite,
)


def test_calibration_includes_valid_format_variants() -> None:
    report = calibration_report()
    assert report["passed"]
    assert report["reference_count"] == 24
    assert report["mutation_count"] == 69
    assert report["format_positive_controls"] == 32


def test_target_events_never_inherit_twenty_oclock_fixture_time() -> None:
    tasks = {task.task_id: task for task in build_suite_tasks()}
    for annotation in load_suite()["annotations"][8:]:
        task = tasks[annotation["task_id"]]
        anchors = {value["anchor"] for value in annotation["branches"].values()}
        for event in task.casefile["events"]:
            if event["id"] in anchors:
                assert event["time"] == {"kind": "unknown"}
        if task.task_id.startswith("audit"):
            assert "每项只选一个标签：事实冲突、认知差异、信息不足、相容" in task.message


def test_format_compatibility_does_not_ignore_a_wrong_classification() -> None:
    branches = {"A": {"topic": "甲"}}
    assert answer_classifications("【A】结论：支持", branches) == {"A": {"支持"}}
    assert answer_classifications("【A】甲：信息不足", branches) == {"A": {"信息不足"}}
    assert answer_classifications("【A】支持\n【A】结论：不支持", branches) == {
        "A": {"支持", "不支持"}
    }
