"""Versioned, explicitly annotated tasks; no inherited expectations on rewritten questions."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from casefile.agent_runtime.models import (
    CaseFileChatAuditFindingCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatSuggestionCandidateV2,
)
from casefile.benchmark.chat_outcome_eval import build_outcome_tasks
from casefile.benchmark.chat_outcome_suite import (
    _AUDIT_PRESET,
    _COMMON_META,
    _FREE_TEXT,
    ChatOutcomeExpectations,
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    _audit_candidate,
    _audit_suggestion,
    _candidate,
    _expected_suggestion,
    _finding,
    _focus,
    _planted_casefile,
    grade_chat_outcome,
)
from casefile.contracts import validate_casefile

ROOT = Path(__file__).resolve().parents[4]
SUITE_PATH = ROOT / "fixtures/chat_subagent_benchmark/v2/suite.json"
SPEC_PATH = SUITE_PATH.with_name("specification.json")
SUITE_VERSION = "casefile-chat-subagent-v2"
GRADER_VERSION = "chat-subagent-explicit-classification-v1"
LABELS = ("支持", "不支持", "信息不足", "事实冲突", "认知差异", "相容")


def _information(object_id: str, event_id: str, title: str, content: str) -> dict[str, Any]:
    return {
        **deepcopy(_COMMON_META),
        "id": object_id,
        "information_type": "system_log",
        "title": title,
        "content": content,
        "source_event_ref": {"object_type": "event", "object_id": event_id},
        "reliability": "high",
        "truth_status": "canon_true",
        "supports_claim_refs": [],
        "refutes_claim_refs": [],
        "classification": "key",
        "availability": {
            "perspective_refs": [],
            "acquisition_conditions": [],
            "alternative_path_refs": [],
        },
    }


def _large_case(spec: dict[str, Any], ordinal: int) -> tuple[ChatOutcomeTask, dict[str, Any]]:
    audit = spec["id"].startswith("audit")
    events: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    annotations: dict[str, Any] = {}
    questions: list[str] = []
    findings: list[CaseFileChatAuditFindingCandidate] = []
    ref_objects: list[str] = []
    ref_events: list[str] = []
    for i, (topic, statement, first, second, label, rationale) in enumerate(spec["branches"]):
        branch = chr(65 + i)
        event_id = f"evt_c{ordinal:02d}_{i + 1}"
        info_ids = [f"info_c{ordinal:02d}_{i + 1}_{part}" for part in (1, 2)]
        events.append(
            {
                "id": event_id,
                "title": "南侧码头装载" if spec.get("repair_title") else topic,
                "description": statement,
            }
        )
        records.extend(
            [
                _information(info_ids[0], event_id, f"{topic}现场记录", first),
                _information(info_ids[1], event_id, f"{topic}范围与规则记录", second),
            ]
        )
        ref_objects.extend(info_ids)
        ref_events.append(event_id)
        questions.append(f"【{branch}】{topic}（{event_id}）：{statement}")
        annotations[branch] = {
            "label": label,
            "rationale": rationale,
            "anchor": event_id,
            "required_evidence": info_ids,
            "independent_of": [chr(65 + j) for j in range(len(spec["branches"])) if j != i],
        }
        if audit and label == "事实冲突":
            findings.append(
                _finding(
                    f"F{len(findings) + 1}",
                    "contradiction",
                    title=f"{topic}的记录冲突",
                    statement=rationale,
                    object_ids=tuple(info_ids),
                    event_ids=(event_id,),
                    needs_manual_review=not bool(spec.get("repair_title")),
                )
            )
    # Coherent older batches are distractors, including similar labels and different values.
    # They are ordinary domain objects, not repeated padding paragraphs.
    for i in range(16 + (ordinal % 3) * 4):
        topic = spec["branches"][i % len(spec["branches"])][0]
        event_id = f"evt_c{ordinal:02d}_archive_{i:02d}"
        events.append(
            {
                "id": event_id,
                "title": f"{topic}历次批次{i + 1:02d}",
                "description": f"2041 年历史批次 {i + 1} 的封存记录，与本次 2042 年目标批次不同。",
                "time": {
                    "kind": "range",
                    "start": "2041-06-01T09:00",
                    "end": "2041-06-01T09:10",
                    "precision": "minute",
                },
            }
        )
        records.extend(
            [
                _information(
                    f"info_c{ordinal:02d}_archive_{i:02d}_1",
                    event_id,
                    f"{topic}历史观测{i + 1:02d}",
                    f"历史批次计数为 {11 + i}，09:05 完成封存。",
                ),
                _information(
                    f"info_c{ordinal:02d}_archive_{i:02d}_2",
                    event_id,
                    f"{topic}历史交接{i + 1:02d}",
                    "09:10 封存袋交给档案室，封条完整。",
                ),
            ]
        )
    rng = random.Random(20260916 + ordinal)
    rng.shuffle(events)
    rng.shuffle(records)
    document = _planted_casefile(
        f"case_subagent_v2_{ordinal:02d}",
        spec["title"],
        events=tuple(events),
        information_units=tuple(records),
    )
    validate_casefile(document)
    repair = spec.get("repair_title")
    edit_request = (
        f"请以现场签章记录为唯一依据，仅给 {ref_events[0]} 的 /title 字段提供修订建议，"
        f"标题统一为“{repair}”。不要直接应用，也不要修改其他字段。"
        if repair
        else "只读核对，不提出任何修改建议。"
    )
    goal = (
        "进行专项逻辑审计：区分世界事实冲突、角色认知差异、信息不足与可兼容叙述。"
        "audit_findings 只收录已确认的世界事实冲突，其他情况在回答中说明。"
        if audit
        else "分析下列说法是否得到记录支持；支持、明确反证和信息不足必须区分。"
    )
    message = (
        goal
        + edit_request
        + "仅限下列目标批次，不审计其他历史批次。\n"
        + "\n".join(questions)
        + "\n各项应读取其现场记录及范围/规则记录，解释理由并引用这两份记录及目标事件。"
        + "最后逐行以【A】结论这样的格式汇总，每项只选一个标签："
        + "、".join(LABELS)
        + "。这里的字母对应上述问题，不要复述全部标签选项。"
    )
    suggestions = (
        (
            _audit_suggestion(
                ref_events[0], "/title", repair, "以指定权威记录修订标题。", finding_ref="F1"
            ),
        )
        if repair
        else ()
    )
    expected = ChatOutcomeExpectations(
        expected_object_ids=tuple(ref_objects),
        expected_event_ids=tuple(ref_events),
        suggestion_count_range=(1, 1) if repair else (0, 0),
        required_suggestions=(_expected_suggestion(ref_events[0], "/title", repair),)
        if repair
        else (),
        requires_suggestion=bool(repair),
        simulate_suggestions=bool(repair),
        audit_finding_count_range=(len(findings), len(findings)) if audit else None,
        required_audit_finding_kinds=("contradiction",) if findings else (),
        forbidden_audit_finding_kinds=("contradiction",) if audit and not findings else (),
        required_audit_evidence_event_ids=tuple(
            finding.evidence_event_ids[0] for finding in findings
        ),
        required_audit_evidence_object_ids=tuple(
            object_id for finding in findings for object_id in finding.evidence_object_ids
        ),
    )
    answer = "\n".join(
        f"【{key}】{value['label']}。{value['rationale']}" for key, value in annotations.items()
    )
    reference = (
        _audit_candidate(
            answer,
            object_ids=tuple(ref_objects),
            event_ids=tuple(ref_events),
            findings=tuple(findings),
            suggestions=suggestions,
        )
        if audit
        else _candidate(answer, object_ids=tuple(ref_objects), event_ids=tuple(ref_events))
    )
    task = ChatOutcomeTask(
        task_id=spec["id"],
        message=message,
        hint=_AUDIT_PRESET if audit else _FREE_TEXT,
        expectations=expected,
        reference_candidate=reference,
        casefile=document,
        focus=_focus(event_ids=tuple(ref_events)),
        validation_issues=(),
        capability="logic_audit" if audit else "casefile_inspection",
    )
    annotation = {
        "task_id": task.task_id,
        "parallel_eligible": spec["parallel"],
        "branches": annotations,
        "edit_authorized": bool(repair),
        "object_count": len(events) + len(records),
        "independent_evidence_units": len(annotations) if spec["parallel"] else 1,
        "review_basis": "Hand-authored question/evidence contracts, frozen before model execution",
    }
    return task, annotation


def freeze_suite(path: Path = SUITE_PATH) -> None:
    if path.exists():
        raise FileExistsError("suite already frozen; use a new version rather than overwrite")
    spec = json.loads(SPEC_PATH.read_text("utf-8"))
    base = {task.task_id: task for task in build_outcome_tasks()}
    tasks = [
        replace(base[name], task_id=f"simple-{i:02d}")
        for i, name in enumerate(spec["simple_base_cases"], 1)
    ]
    annotations = [
        {
            "task_id": task.task_id,
            "parallel_eligible": False,
            "branches": {},
            "regression_base": name,
        }
        for task, name in zip(tasks, spec["simple_base_cases"], strict=True)
    ]
    for ordinal, item in enumerate(spec["cases"], 1):
        task, annotation = _large_case(item, ordinal)
        tasks.append(task)
        annotations.append(annotation)
    serialized = []
    for task in tasks:
        row = asdict(task)
        row["casefile"] = task.frozen_casefile
        row["validation_issues"] = list(task.frozen_validation_issues)
        row["reference_candidate"] = task.reference_candidate.model_dump(mode="json")
        serialized.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "suite_version": SUITE_VERSION,
                "grader_version": GRADER_VERSION,
                "tasks": serialized,
                "annotations": annotations,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text("utf-8"))
    if (
        payload["suite_version"] not in {SUITE_VERSION, "casefile-chat-subagent-v3"}
        or len(payload["tasks"]) != 24
    ):
        raise ValueError("invalid v2 frozen suite")
    return payload


def build_suite_tasks(path: Path = SUITE_PATH) -> tuple[ChatOutcomeTask, ...]:
    from casefile.benchmark.chat_outcome_suite import ExpectedSuggestion

    tasks = []
    for frozen in load_suite(path)["tasks"]:
        row = dict(frozen)
        expectations = dict(row["expectations"])
        for key, value in expectations.items():
            if isinstance(value, list):
                expectations[key] = tuple(value)
        expectations["required_suggestions"] = tuple(
            ExpectedSuggestion(**value) for value in expectations["required_suggestions"]
        )
        row["expectations"] = ChatOutcomeExpectations(**expectations)
        row["reference_candidate"] = CaseFileChatCandidateV2.model_validate(
            row["reference_candidate"]
        )
        row["history"] = tuple(row["history"])
        row["validation_issues"] = tuple(row["validation_issues"])
        tasks.append(ChatOutcomeTask(**row))
    return tuple(tasks)


def answer_classifications(answer: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    normalized = answer.replace("**", "").replace("`", "")
    for label, value in re.findall(
        r"【([A-Z])】\s*[:：]?\s*(不支持|支持|信息不足|事实冲突|认知差异|相容)", normalized
    ):
        result.setdefault(label, set()).add(value)
    return result


def grade_trial(task: ChatOutcomeTask, candidate: Any, **kwargs: Any) -> ChatOutcomeTrialVerdict:
    verdict = grade_chat_outcome(task, candidate, **kwargs)
    annotation = next(
        item for item in load_suite()["annotations"] if item["task_id"] == task.task_id
    )
    actual = answer_classifications(candidate.answer)
    failures = list(verdict.failures)
    for label, expected in annotation["branches"].items():
        if actual.get(label) != {expected["label"]}:
            failures.append(f"answer_classification:{label}")
    return replace(
        verdict,
        failures=tuple(failures),
        capability_passed=verdict.capability_passed and not failures,
        passed=verdict.passed and not failures,
    )


def calibration_report(
    tasks: tuple[ChatOutcomeTask, ...] | None = None,
    grader: Callable[..., ChatOutcomeTrialVerdict] | None = None,
) -> dict[str, Any]:
    """Positive controls and deliberately wrong outcomes, before spending model budget."""
    rows = []
    check = grader or grade_trial
    for task in tasks or build_suite_tasks():
        reference = task.reference_candidate
        reference_passed = check(task, reference, allow_suggestions=True).passed
        mutations: list[tuple[str, Any]] = []
        if not task.task_id.startswith("simple"):
            actual = answer_classifications(reference.answer)
            for label, values in actual.items():
                expected = next(iter(values))
                wrong = next(value for value in LABELS if value != expected)
                mutations.append(
                    (
                        f"wrong_classification_{label}",
                        reference.model_copy(
                            update={
                                "answer": reference.answer.replace(
                                    f"【{label}】{expected}", f"【{label}】{wrong}"
                                ),
                            }
                        ),
                    )
                )
            mutations.append(
                (
                    "missing_evidence",
                    reference.model_copy(
                        update={
                            "referenced_object_ids": [],
                            "referenced_event_ids": [],
                        }
                    ),
                )
            )
            if task.task_id == "audit-02":
                mutations.append(
                    (
                        "belief_as_contradiction",
                        reference.model_copy(
                            update={
                                "audit_findings": [
                                    _finding(
                                        "F1",
                                        "contradiction",
                                        object_ids=tuple(reference.referenced_object_ids),
                                        event_ids=tuple(reference.referenced_event_ids),
                                    )
                                ],
                            }
                        ),
                    )
                )
            if task.task_id == "audit-07":
                mutations.append(
                    (
                        "omit_explicit_repair",
                        reference.model_copy(
                            update={
                                "suggestions": [],
                            }
                        ),
                    )
                )
            else:
                mutations.append(
                    (
                        "unauthorized_edit",
                        reference.model_copy(
                            update={
                                "suggestions": [
                                    CaseFileChatSuggestionCandidateV2(
                                        object_id=reference.referenced_event_ids[0],
                                        path="/title",
                                        value_json=json.dumps("未经请求的改名"),
                                        reason="无授权的修改建议",
                                        finding_ref=None,
                                    )
                                ],
                            }
                        ),
                    )
                )
        misses = [
            name
            for name, candidate in mutations
            if check(task, candidate, allow_suggestions=True).passed
        ]
        rows.append(
            {
                "task_id": task.task_id,
                "reference_passed": reference_passed,
                "mutation_count": len(mutations),
                "mutation_misses": misses,
            }
        )
    return {
        "suite_version": SUITE_VERSION,
        "grader_version": GRADER_VERSION,
        "passed": all(row["reference_passed"] and not row["mutation_misses"] for row in rows),
        "reference_count": len(rows),
        "mutation_count": sum(row["mutation_count"] for row in rows),
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true", required=True)
    parser.parse_args()
    freeze_suite()
    print(SUITE_PATH)
