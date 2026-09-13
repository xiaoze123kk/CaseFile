"""Bounded chapter orchestration over shared prose roles; never auto-adopts text."""

from collections.abc import Callable
from typing import Any

from casefile.agent_runtime.novel_chapter_review import chapter_change_evidence
from casefile.agent_runtime.novel_prose import (
    ChapterProseProvider,
    evidence,
    phase_prompt,
    select_pair,
    validate_checklist,
    validate_critic,
    validate_decision,
    validate_judge,
    validate_text,
)
from casefile.agent_runtime.prose_judge import judge_evidence_repair_baseline
from casefile.agent_runtime.prose_quality_critic import parse_quality_pairwise
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.handlers.novel_model_calls import (
    NovelCallError,
    NovelModelJournal,
    NovelProtocolError,
)

LABELS = {
    "checklist": "检查清单",
    "judge": "Judge 逐项审核",
    "revision": "编辑决策",
    "rewriter": "Rewriter 修订",
    "quality_critic": "Quality Critic 文笔与节奏",
    "polisher": "Polisher 润色",
    "pairwise": "双稿比较",
}


class ChapterProseWorkflow:
    def __init__(
        self, journal: NovelModelJournal, context: dict[str, Any], key: str, model: str,
        provider: Any = None,
    ):
        self.journal, self.context, self.key, self.model = journal, context, key, model
        self.provider = provider or ChapterProseProvider()
        self.stages: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = []
        self.reports: list[dict[str, Any]] = []
        self.text = context["target"]
        self.revision_count = 0
        self.checks: list[dict[str, Any]] = []
        self.revision_failed = False

    def call[T](self, phase: str, payload: dict[str, Any], validate: Callable[[Any], T]) -> T:
        stage = {
            "phase": phase,
            "label": LABELS[phase],
            "status": "incomplete",
            "summary": "调用尚未完成",
            "findings": [],
            "candidate_hash": canonical_json_sha256(self.text),
        }
        self.stages.append(stage)
        self.journal.ctx.emit(
            self.journal.ctx.task,
            "novel.activity",
            phase,
            {"text": "正在进行" + LABELS[phase] + "…"},
        )
        repair = None
        rejected = None
        protected: dict[str, Any] | None = None

        def validate_result(result: Any) -> T:
            nonlocal rejected
            rejected = result.candidate
            if (
                protected is not None
                and judge_evidence_repair_baseline(
                    rejected, [item["check_id"] for item in self.checks]
                )
                != protected
            ):
                raise ValueError("novel_judge_repair_changed_decision")
            return validate(result.candidate)

        for attempt in range(2):
            data = {
                **payload,
                "protocol_repair": repair,
                "prose_prompt_versions": self.context.get("prose_prompt_versions", {}),
            }
            try:
                def invoke(data: dict[str, Any] = data) -> Any:
                    return self.provider.invoke(phase, data, self.key, self.model)

                result = self.journal.call(
                    phase,
                    phase_prompt(phase, self.context),
                    data,
                    invoke,
                    validate_result,
                )
                stage.update(status="completed", summary=LABELS[phase] + "完成")
                self.trace.append({"phase": phase, "input": data, "output": result})
                return result
            except NovelProtocolError as error:
                stage["summary"] = "响应协议或证据绑定未通过"
                if attempt:
                    raise
                repair = (
                    {"error": str(error), "rejected_candidate": rejected}
                    if phase == "checklist"
                    else str(error)
                )
                if phase == "judge":
                    try:
                        protected = judge_evidence_repair_baseline(
                            rejected, [item["check_id"] for item in self.checks]
                        )
                    except ValueError:
                        protected = None
                    if protected is not None:
                        repair = {
                            "error": str(error),
                            "original_candidate": rejected,
                            "allowed_change": "evidence_ids_only",
                        }
        raise RuntimeError("novel_prose_unreachable")

    def judge(self) -> dict[str, Any]:
        result = self.call(
            "judge",
            {
                **self.context,
                "checklist": self.checks,
                "candidate": self.text,
                "server_evidence_catalog": evidence(self.text),
                "change_evidence": chapter_change_evidence(self.context["target"], self.text),
            },
            lambda candidate: validate_judge(candidate, self.checks, self.text),
        )
        self.stages[-1]["findings"] = [
            item["check_id"] + " · " + item["verdict"] + "：" + item["rationale"]
            for item in result["assessments"]
        ]
        return result

    def decide(self, judged: dict[str, Any], exhausted: bool) -> dict[str, Any]:
        result = self.call(
            "revision",
            {
                **self.context,
                "checklist": self.checks,
                "candidate": self.text,
                "judge": judged,
                "repair_budget_exhausted": exhausted,
            },
            lambda candidate: validate_decision(candidate, self.checks, exhausted),
        )
        self.stages[-1]["summary"] = result["action"] + "：" + result["rationale"]
        self.stages[-1]["findings"] = [
            item["check_id"] + "：" + item["reason"] for item in result["findings"]
        ]
        self.report(judged, result)
        return result

    def report(self, judged: dict[str, Any], decision: dict[str, Any]) -> None:
        checks = {item["check_id"]: item for item in self.checks}
        decisions = {item["check_id"]: item for item in decision["findings"]}
        findings = []
        for assessment in judged["assessments"]:
            check = checks[assessment["check_id"]]
            finding = decisions[assessment["check_id"]]
            findings.append(
                {
                    "category": check["category"],
                    "severity": "major"
                    if finding["severity"] == "fatal"
                    else "warning"
                    if assessment["verdict"] != "pass"
                    else "info",
                    "message": (check["requirement"] + "：" + assessment["rationale"])[:1500],
                    "source_quote": check["source_quote"][:1000]
                    if check["source_field"] == "target"
                    else "",
                    "candidate_quote": (
                        assessment["evidence"][0]["text"] if assessment["evidence"] else ""
                    ),
                    "suggestion": finding["reason"][:1500],
                }
            )
        passed = all(item["verdict"] == "pass" for item in judged["assessments"])
        action = (
            "revise"
            if decision["action"] in {"local_revision", "full_rewrite"}
            else "accept"
            if passed and decision["action"] == "retain"
            else "needs_author"
        )
        self.reports.append(
            {
                "round": len(self.reports),
                "candidate_hash": judged["candidate_hash"],
                "review": {
                    "summary": decision["rationale"][:2000],
                    "action": action,
                    "revision_plan": decision["revision_plan"],
                    "findings": findings,
                },
            }
        )

    def rewrite(self) -> str:
        for round_no in range(3):
            judged = self.judge()
            decision = self.decide(judged, exhausted=round_no == 2)
            if decision["action"] in {"retain", "stop"}:
                return "编辑决策完成；请结合逐项审核决定是否采纳。"
            revised = self.call(
                "rewriter",
                {
                    **self.context,
                    "checklist": self.checks,
                    "candidate": self.text,
                    "judge": judged,
                    "decision": decision,
                },
                validate_text,
            )
            if revised == self.text:
                self.stages[-1].update(status="incomplete", summary="未产生正文变化")
                self.revision_failed = True
                return "修订未产生正文变化，仍有事项需要处理。"
            self.text = revised
            self.revision_count += 1
            self.stages[-1]["candidate_hash"] = canonical_json_sha256(self.text)
        raise RuntimeError("novel_prose_revision_bound_invalid")

    def polish(self) -> str:
        original = self.text
        critic = self.call(
            "quality_critic",
            {**self.context, "candidate": original, "server_evidence_catalog": evidence(original)},
            lambda candidate: validate_critic(candidate, original),
        )
        self.stages[-1]["findings"] = [item["description"] for item in critic["findings"]]
        if not critic["findings"]:
            return "文学质量审核未发现需要润色的问题，保留原章。"
        polished = self.call(
            "polisher",
            {
                **self.context,
                "candidate": original,
                "checklist": self.checks,
                "quality_findings": critic,
            },
            validate_text,
        )
        if polished == original:
            return "润色未产生正文变化，保留原章。"
        self.text = polished
        self.stages[-1]["candidate_hash"] = canonical_json_sha256(polished)
        judged = self.judge()
        decision = self.decide(judged, exhausted=True)
        if decision["action"] != "retain" or any(
            item["verdict"] != "pass" for item in judged["assessments"]
        ):
            self.text = original
            return "润色稿仍有保真或要求问题，保留原章及审阅意见。"
        pairs = []
        for a, b in ((original, polished), (polished, original)):
            # Comparison excludes history, source labels and the critic verdict.
            pair = self.call(
                "pairwise",
                {
                    "instruction": self.context["instruction"],
                    "requirements": self.context["requirements"],
                    "a": a,
                    "b": b,
                },
                parse_quality_pairwise,
            )
            pairs.append(pair)
            self.stages[-1]["summary"] = "本次比较偏好：" + pair["overall_preference"]
            self.stages[-1]["findings"] = [
                item["dimension"] + "：" + item["preference"]
                for item in pair["dimension_preferences"]
            ]
        accepted, reason = select_pair(*pairs)
        self.text = polished if accepted else original
        self.trace.append(
            {
                "phase": "selection",
                "reason": reason,
                "candidate_hash": canonical_json_sha256(self.text),
            }
        )
        return (
            "两次交换顺序比较均支持润色稿，供你审阅。"
            if accepted
            else "双稿比较未稳定支持润色稿，保留原章。"
        )

    def run(self) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        status = "completed"
        try:
            # The checklist can cite only these sources. Future chapters and setting notes
            # must not become invented quotations from the current chapter.
            checklist_context = {
                key: self.context[key]
                for key in ("instruction", "requirements", "target", "mode", "chapter_title")
            }
            checklist = self.call(
                "checklist",
                checklist_context,
                lambda value: validate_checklist(value, self.context),
            )
            self.checks = checklist["checks"]
            self.stages[-1]["findings"] = [
                item["check_id"] + "：" + item["requirement"] for item in self.checks
            ]
            message = self.polish() if self.context["mode"] == "polish" else self.rewrite()
            if self.revision_failed:
                status = "revision_failed"
        except NovelCallError:
            status = "incomplete"
            message = "流程未完整完成，已保留现有文本与阶段记录，请核对后决定。"
            if self.context["mode"] == "polish":
                self.text = self.context["target"]
        candidate = {"text": self.text, "message": message, "reason": message}
        review = {
            "status": status,
            "message": message,
            "candidate_hash": canonical_json_sha256(self.text),
            "revision_count": self.revision_count,
            "reports": self.reports,
            "stages": self.stages,
        }
        return candidate, review, self.trace
