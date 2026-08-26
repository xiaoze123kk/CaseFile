"""Deterministic author-language validation for CaseFile Chat public prose."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final

from casefile.agent_runtime.chat_validation_contracts import (
    ChatCompletionValidationError,
    ValidationIssue,
)
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult

PUBLIC_OUTPUT_POLICY_VIOLATION: Final = "public_output_policy_violation"
PUBLIC_OUTPUT_POLICY_FAILED: Final = "public_output_policy_failed"
PUBLIC_INTERNAL_REFUSAL: Final = (
    "我不能提供内部运行信息，但可以说明本次操作对作者可见的结果。"
)

_DISCLOSURE_ACTION = re.compile(
    r"逐字|原文|展示|列出|输出|告诉|提供|读取|查看|公开|披露|打印|显示"
)
_PROTECTED_INTERNAL_TARGET = re.compile(
    r"(?i)(?:"
    r"系统提示词|开发者消息|隐藏指令|内部(?:组件|对象|字段|协议|实现|运行信息)|"
    r"模型(?:服务)?密钥|密钥原文|API\s*key|原始\s*JSON|"
    r"TaskRun|result_jsonb|payload_jsonb|field_path|route_source|"
    r"task_run_id|model_id|模型\s*ID|对象\s*ID"
    r")"
)
_RESERVED_FIELD = re.compile(
    r"(?i)(?<![a-z0-9_])(?:"
    r"result_jsonb|payload_jsonb|field_path|operation_type|prompt_version|"
    r"schema_id|provider_id|model_id|component_id|component_steps|route_source|"
    r"reason_code|policy_key|policy_version|finding_key|finding_id|warning_key|"
    r"task_run_id|patch_set_id|operation_id|object_id|event_id|message_id|"
    r"draft_revision|object_revision|base_revision|input_hash|output_hash|ledger_hash|"
    r"toolset_version|agent_version"
    r")(?![a-z0-9_])"
)
_ENGINEERING_TERM = re.compile(
    r"(?i)(?<![a-z0-9_])(?:"
    r"TaskRun|TaskEvent|PatchOperation|VerificationEngine|V1EditingService|"
    r"System\s+Prompt|Developer\s+Message|Prompt|Schema|Provider|runtime|"
    r"Finalizer|Executor|Router|Worker|Safe\s+Patch\s+Registry|Frozen\s+Tool\s+Ledger"
    r")(?![a-z0-9_])|"
    r"(?<![a-z0-9_])(?:chat|analysis|audit|issue|edit|gate|clarify|scope)_finalizer"
    r"(?![a-z0-9_])|"
    r"(?<![a-z0-9_])(?:casefile-chat-v[0-9]+|casefile-single-agent-v[0-9]+|"
    r"casefile-chat-tools-v[0-9]+|public-language-v[0-9]+)(?![a-z0-9_])|"
    r"系统提示词|开发者消息|隐藏指令"
)
_JSON_POINTER = re.compile(
    r"(?<![A-Za-z0-9_:/])/(?:[A-Za-z_][A-Za-z0-9_~-]*)"
    r"(?:/(?:[A-Za-z0-9_~-]+))*"
)
_INTERNAL_ID = re.compile(
    r"(?i)(?<![a-z0-9_])(?:"
    r"(?:ent|evt|obj|claim|scene|loc|rel|clue|draft|task|patch|run|msg|thread)"
    r"_[a-z0-9][a-z0-9_-]{2,}|"
    r"(?:llm|policy|finding):[a-z0-9][a-z0-9._:-]{3,}|"
    r"(?:finding|发现|审计项)\s*[:#：]?\s*F[1-9][0-9]*|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|"
    r"[0-9a-f]{64}"
    r")(?![a-z0-9_])"
)
_EMBEDDED_JSON = re.compile(
    r"(?:^|[\s`])(?:"
    r"\{\s*[\"'][^\"'{}\r\n]{1,80}[\"']\s*:|"
    r"\[\s*\{\s*[\"'][^\"'{}\r\n]{1,80}[\"']\s*:"
    r")"
)
_JSON_FENCE = re.compile(r"(?i)```\s*json\b")


@dataclass(frozen=True, slots=True)
class _PublicText:
    path: str
    value: str


class PublicLanguageValidationError(ChatCompletionValidationError):
    """A repairable public-language violation or its terminal fail-closed form."""

    def __init__(
        self,
        *,
        issues: tuple[ValidationIssue, ...],
        terminal: bool = False,
    ) -> None:
        code = PUBLIC_OUTPUT_POLICY_FAILED if terminal else PUBLIC_OUTPUT_POLICY_VIOLATION
        super().__init__(code=code, issues=issues)
        if terminal:
            self.error_code = PUBLIC_OUTPUT_POLICY_FAILED

    def repair_feedback(self) -> str:
        return (
            "上一轮面向作者的文字包含不可公开的工程信息。"
            "仅重写 answer、finding 的 title/statement 与 suggestion 的 reason；"
            "保留作者语义、引用、修改目标和值，不得解释门禁或复述被拒绝内容。"
            "改用自然、清楚的中文创作者语言。"
        )


def validate_public_language(
    result: CaseFileChatResult,
    *,
    sensitive_values: Iterable[str] = (),
) -> None:
    """Reject public prose that exposes internal or currently sensitive values."""

    sensitive = tuple(
        value for value in sensitive_values if isinstance(value, str) and len(value) >= 4
    )
    issues: list[ValidationIssue] = []
    for public_text in _iter_public_text(result):
        rule_ids = public_language_rule_ids(public_text.value, sensitive_values=sensitive)
        if not rule_ids:
            continue
        issues.append(
            ValidationIssue(
                code=PUBLIC_OUTPUT_POLICY_VIOLATION,
                stage="schema",
                path=public_text.path,
                message="面向作者的文字包含不可公开的工程信息。",
                repairable=True,
                details={"rule_ids": rule_ids},
            )
        )
    if issues:
        raise PublicLanguageValidationError(issues=tuple(issues))


def normalize_internal_disclosure_refusal(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Project explicit internal-disclosure requests to one canonical public refusal."""

    message = request.message.strip()
    if not (
        _DISCLOSURE_ACTION.search(message)
        and _PROTECTED_INTERNAL_TARGET.search(message)
    ):
        return result
    request.emit(
        "public_language.internal_disclosure_normalized",
        "validating",
        {
            "reason_code": "protected_internal_disclosure_request",
            "projection": "fixed_refusal",
        },
    )
    candidate_update: dict[str, object] = {
        "answer": PUBLIC_INTERNAL_REFUSAL,
        "referenced_object_ids": [],
        "referenced_event_ids": [],
        "referenced_validation_issue_ids": [],
        "suggested_view": None,
        "suggestions": [],
    }
    if hasattr(result.candidate, "audit_findings"):
        candidate_update["audit_findings"] = []
    return replace(
        result,
        candidate=result.candidate.model_copy(update=candidate_update),
    )


def public_language_rule_ids(
    value: str,
    *,
    sensitive_values: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return stable rule identifiers without retaining the inspected prose.

    Qualification and public projectors can use this read-only surface to
    prove that already-projected author text obeys the same policy as model
    output.  Sensitive values are compared in memory and never included in
    the returned diagnostics.
    """

    sensitive = tuple(item for item in sensitive_values if isinstance(item, str) and len(item) >= 4)
    return tuple(_violated_rules(value, sensitive))


def terminal_public_language_error(
    violation: PublicLanguageValidationError,
) -> PublicLanguageValidationError:
    """Convert a repeated violation into the stable non-repairable failure."""

    issues = tuple(
        ValidationIssue(
            code=PUBLIC_OUTPUT_POLICY_FAILED,
            stage=issue.stage,
            path=issue.path,
            message="面向作者的文字未通过公开语言门禁。",
            repairable=False,
            details=dict(issue.details),
        )
        for issue in violation.issues
    )
    return PublicLanguageValidationError(issues=issues, terminal=True)


def _iter_public_text(result: CaseFileChatResult) -> Iterable[_PublicText]:
    candidate = result.candidate
    yield _PublicText(path="/answer", value=candidate.answer)
    for index, finding in enumerate(getattr(candidate, "audit_findings", ())):
        yield _PublicText(
            path=f"/audit_findings/{index}/title",
            value=finding.title,
        )
        yield _PublicText(
            path=f"/audit_findings/{index}/statement",
            value=finding.statement,
        )
    for index, suggestion in enumerate(candidate.suggestions):
        reason = getattr(suggestion, "reason", None)
        if isinstance(reason, str):
            yield _PublicText(path=f"/suggestions/{index}/reason", value=reason)


def _violated_rules(value: str, sensitive_values: tuple[str, ...]) -> list[str]:
    rules: list[str] = []
    if any(sensitive in value for sensitive in sensitive_values):
        rules.append("current_sensitive_value")
    if _RESERVED_FIELD.search(value):
        rules.append("reserved_field")
    if _ENGINEERING_TERM.search(value):
        rules.append("engineering_term")
    if _JSON_POINTER.search(value):
        rules.append("json_pointer")
    if _INTERNAL_ID.search(value):
        rules.append("internal_id")
    if _JSON_FENCE.search(value) or _EMBEDDED_JSON.search(value) or _is_raw_json(value):
        rules.append("raw_json")
    return rules


def _is_raw_json(value: str) -> bool:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, (dict, list))


__all__ = [
    "PUBLIC_OUTPUT_POLICY_FAILED",
    "PUBLIC_OUTPUT_POLICY_VIOLATION",
    "PUBLIC_INTERNAL_REFUSAL",
    "PublicLanguageValidationError",
    "normalize_internal_disclosure_refusal",
    "public_language_rule_ids",
    "terminal_public_language_error",
    "validate_public_language",
]
