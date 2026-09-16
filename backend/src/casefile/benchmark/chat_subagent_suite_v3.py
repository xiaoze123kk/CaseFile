"""V3 fixes fixture timestamp inheritance and unambiguous classification formatting."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from casefile.benchmark import chat_subagent_suite_v2 as v2
from casefile.benchmark.chat_outcome_suite import (
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    grade_chat_outcome,
)
from casefile.contracts import validate_casefile

SUITE_PATH = v2.SUITE_PATH.parent.parent / "v3/suite.json"
SUITE_VERSION = "casefile-chat-subagent-v3"
GRADER_VERSION = "chat-subagent-explicit-classification-v2"


def freeze_suite() -> None:
    if SUITE_PATH.exists():
        raise FileExistsError("v3 already frozen")
    payload = v2.load_suite()
    specification = json.loads(v2.SPEC_PATH.read_text("utf-8"))
    specs = {row["id"]: row for row in specification["cases"]}
    payload["suite_version"] = SUITE_VERSION
    payload["grader_version"] = GRADER_VERSION
    for row in payload["tasks"]:
        if row["task_id"].startswith("simple"):
            continue
        annotation = next(a for a in payload["annotations"] if a["task_id"] == row["task_id"])
        target_ids = {a["anchor"] for a in annotation["branches"].values()}
        for event in row["casefile"]["events"]:
            if event["id"] in target_ids:
                # No fabricated common timestamp. Actual observation times remain in the records.
                event["time"] = {"kind": "unknown"}
        choices = (
            "事实冲突、认知差异、信息不足、相容"
            if row["task_id"].startswith("audit")
            else "支持、不支持、信息不足"
        )
        row["message"] = row["message"].replace(
            "支持、不支持、信息不足、事实冲突、认知差异、相容", choices
        )
        row["message"] += (
            "\n事件结构时间未单独录入，核对时使用题面和原文明确给出的时间，"
            "不要将缺少结构时间理解为否定原文时间。"
        )
        for label, branch in annotation["branches"].items():
            branch["topic"] = specs[row["task_id"]]["branches"][ord(label) - 65][0]
        validate_casefile(row["casefile"])
    SUITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUITE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_suite() -> dict[str, Any]:
    return v2.load_suite(SUITE_PATH)


def build_suite_tasks() -> tuple[ChatOutcomeTask, ...]:
    return v2.build_suite_tasks(SUITE_PATH)


def answer_classifications(answer: str, branches: dict[str, Any]) -> dict[str, set[str]]:
    normalized = answer.replace("**", "").replace("`", "")
    result = {}
    for label, branch in branches.items():
        topic = re.escape(branch["topic"])
        pattern = (
            rf"【{label}】\s*(?:(?:结论|{topic})\s*[:：]\s*)?"
            r"(不支持|支持|信息不足|事实冲突|认知差异|相容)"
        )
        result[label] = set(re.findall(pattern, normalized))
    return result


def grade_trial(task: ChatOutcomeTask, candidate: Any, **kwargs: Any) -> ChatOutcomeTrialVerdict:
    verdict = grade_chat_outcome(task, candidate, **kwargs)
    annotation = next(a for a in load_suite()["annotations"] if a["task_id"] == task.task_id)
    actual = answer_classifications(candidate.answer, annotation["branches"])
    failures = list(verdict.failures)
    for label, expected in annotation["branches"].items():
        if actual.get(label) != {expected["label"]}:
            failures.append(f"answer_classification:{label}")
    return replace(
        verdict,
        failures=tuple(failures),
        passed=verdict.passed and not failures,
        capability_passed=verdict.capability_passed and not failures,
    )


def calibration_report() -> dict[str, Any]:
    tasks = build_suite_tasks()
    report = v2.calibration_report(tasks, grade_trial)
    format_checks = []
    for task in tasks[8:]:
        branches = next(
            a["branches"] for a in load_suite()["annotations"] if a["task_id"] == task.task_id
        )
        for style in ("结论", "topic"):
            answer = task.reference_candidate.answer
            for label, branch in branches.items():
                prefix = branch["topic"] if style == "topic" else style
                answer = answer.replace(f"【{label}】", f"【{label}】{prefix}：")
            candidate = task.reference_candidate.model_copy(update={"answer": answer})
            format_checks.append(grade_trial(task, candidate, allow_suggestions=True).passed)
    return {
        **report,
        "suite_version": SUITE_VERSION,
        "grader_version": GRADER_VERSION,
        "passed": report["passed"] and all(format_checks),
        "format_positive_controls": len(format_checks),
    }


if __name__ == "__main__":
    freeze_suite()
    print(SUITE_PATH)
