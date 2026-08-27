"""Public read models for workflow entities and failures."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from casefile.data_postgres.models import (
    AgentMessage,
    AgentThread,
    Brief,
    BriefVersion,
    SourceRecord,
    TaskEvent,
    TaskRun,
    UserProviderSetting,
)

_FAILURE_MESSAGES = {
    "agent_component_failed": "深稿生成部件未通过门禁，可从失败阶段恢复。",
    "candidate_validation_failed": "模型输出未通过 CaseFile 结构校验，已停止写入草稿。",
    "public_output_policy_failed": "本次回复未通过安全检查，未生成修改建议，请重新表述后再试。",
    "provider_connection_failed": "无法连接模型服务，网络重试已耗尽。",
    "provider_timeout": "模型服务响应超时，网络重试已耗尽。",
    "provider_rate_limited": "模型服务当前限流，请稍后重试。",
    "provider_authentication_failed": "模型服务认证失败，请检查 API 密钥与模型权限。",
    "generation_failed": "Agent 生成失败，草稿未被修改。",
}
_COMPILER_FAILURE_MESSAGE = "编译冻结输入校验失败，本次构建已安全停止。"
_RETRYABLE_FAILURES = frozenset(
    {
        "candidate_validation_failed",
        "agent_component_failed",
        "provider_connection_failed",
        "provider_timeout",
        "provider_rate_limited",
    }
)


def provider_view(setting: UserProviderSetting) -> dict[str, Any]:
    if setting.secret_last_four is None or setting.credential_status == "deleted":
        raise RuntimeError("Deleted provider credentials do not have a public view")
    return {
        "provider": setting.provider,
        "model_id": setting.model_id,
        "model_is_custom": setting.model_is_custom,
        "config_version": setting.config_version,
        "credential_status": setting.credential_status,
        "masked_api_key": f"••••••••{setting.secret_last_four}",
        "validated_at": time_view(setting.validated_at),
        "validation_error_code": setting.validation_error_code,
        "default_budget": setting.default_budget_jsonb,
    }


def brief_view(brief: Brief) -> dict[str, Any]:
    return {
        "brief_id": brief.id,
        "public_id": brief.public_id,
        "draft_revision": brief.draft_revision,
        "content": brief.draft_jsonb,
        "current_version_id": brief.current_version_id,
        "updated_at": time_view(brief.updated_at),
    }


def brief_version_view(version: BriefVersion, public_id: str) -> dict[str, Any]:
    return {
        "brief_version_id": version.id,
        "brief_id": version.brief_id,
        "public_id": public_id,
        "version_no": version.version_no,
        "content": version.content_jsonb,
        "content_hash": version.content_hash,
        "confirmed_at": time_view(version.confirmed_at),
    }


def source_view(source: SourceRecord) -> dict[str, Any]:
    return {
        "source_record_id": source.id,
        "source_kind": source.source_kind,
        "content_text": source.content_text,
        "content_hash": source.content_hash,
        "parent_source_record_id": source.parent_source_record_id,
        "generated_by_task_run_id": source.generated_by_task_run_id,
        "created_at": time_view(source.created_at),
    }


def agent_thread_view(thread: AgentThread) -> dict[str, Any]:
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "title_source": thread.title_source,
        "is_pinned": thread.is_pinned,
        "status": thread.status,
        "last_message_at": time_view(thread.last_message_at),
        "created_at": time_view(thread.created_at),
        "updated_at": time_view(thread.updated_at),
    }


def agent_message_view(
    message: AgentMessage,
    *,
    task: TaskRun | None = None,
    patch_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "message_id": message.id,
        "thread_id": message.thread_id,
        "sequence_no": message.sequence_no,
        "role": message.role,
        "status": message.status,
        "content": message.content_text,
        "task": None if task is None else task_view(task),
        "referenced_object_ids": (
            []
            if task is None or not isinstance(task.result_jsonb, dict)
            else list(task.result_jsonb.get("referenced_object_ids", []))
        ),
        "referenced_event_ids": (
            []
            if task is None or not isinstance(task.result_jsonb, dict)
            else list(task.result_jsonb.get("referenced_event_ids", []))
        ),
        "referenced_validation_issue_ids": (
            []
            if task is None or not isinstance(task.result_jsonb, dict)
            else list(task.result_jsonb.get("referenced_validation_issue_ids", []))
        ),
        "suggested_view": (
            None
            if task is None or not isinstance(task.result_jsonb, dict)
            else task.result_jsonb.get("suggested_view")
        ),
        "patch_set": patch_set,
        "created_at": time_view(message.created_at),
        "updated_at": time_view(message.updated_at),
    }


def task_view(task: TaskRun) -> dict[str, Any]:
    return {
        "task_run_id": task.id,
        "project_id": task.project_id,
        "task_type": task.task_type,
        "prompt_version": task.prompt_version,
        "status": task.status,
        "stage": task.stage,
        "provider": task.provider,
        "model_id": task.model_id,
        "input_draft_revision": task.input_draft_revision,
        "input_brief_revision": task.input_brief_revision,
        "input_source_record_id": task.input_source_record_id,
        "input_brief_intake_id": task.brief_intake_id,
        "input_brief_intake_revision": task.input_brief_intake_revision,
        "base_brief_intake_candidate_id": task.base_brief_intake_candidate_id,
        "agent_thread_id": task.agent_thread_id,
        "input_message_id": task.input_message_id,
        "output_message_id": task.output_message_id,
        "input_hash": task.input_hash,
        "candidate_strategy": (
            task.input_jsonb.get("candidate_strategy")
            if task.task_type == "brief_to_draft"
            and task.input_jsonb.get("candidate_strategy")
            in {"structure_first", "atmosphere_first", "reasoning_first", "balanced"}
            else None
        ),
        "attempt_count": task.attempt_count,
        "usage": task.usage_jsonb,
        "result_snapshot_id": task.result_snapshot_id,
        "result": task.result_jsonb,
        "error_code": task.error_code,
        "failure": task_failure_from_row(task),
        "component_steps": [component_step_view(step) for step in task.component_step_runs],
        "created_at": time_view(task.created_at),
        "updated_at": time_view(task.updated_at),
    }


def component_step_view(step: Any) -> dict[str, Any]:
    diagnostic = step.diagnostic_jsonb if isinstance(step.diagnostic_jsonb, dict) else {}
    raw_issues = diagnostic.get("issues", [])
    issues = [
        {
            "component_id": str(issue.get("component_id") or step.component_id),
            "failure_layer": str(
                issue.get("failure_layer") or diagnostic.get("failure_layer") or "unknown"
            ),
            "schema_id": issue.get("schema_id") or step.ir_schema_id,
            "code": str(issue.get("code") or "validation_failed"),
            "path": str(issue.get("path") or ""),
            "message": str(issue.get("message") or "部件执行失败。")[:240],
        }
        for issue in raw_issues
        if isinstance(issue, dict)
    ]
    return {
        "step_run_id": step.id,
        "attempt_no": step.task_attempt.attempt_no if hasattr(step, "task_attempt") else 1,
        "component_id": step.component_id,
        "parent_component_id": step.parent_component_id,
        "execution_no": step.execution_no,
        "status": step.status,
        "schema_id": step.ir_schema_id,
        "input_hash": step.input_hash,
        "output_hash": step.output_hash,
        "failure_layer": diagnostic.get("failure_layer"),
        "issues": issues,
        "recoverable": bool(diagnostic.get("recoverable")),
        "resumed_from_step_run_id": step.resumed_from_step_run_id,
    }


def task_failure_view(
    error_code: str | None,
    *,
    issues: list[dict[str, str]] | None = None,
    network_retries: int | None = None,
) -> dict[str, Any] | None:
    if error_code is None:
        return None
    message = (
        _COMPILER_FAILURE_MESSAGE
        if error_code.startswith("compiler_")
        else _FAILURE_MESSAGES.get(error_code, _FAILURE_MESSAGES["generation_failed"])
    )
    if network_retries is not None and error_code in {
        "provider_connection_failed",
        "provider_timeout",
    }:
        message = f"{message}（已自动重试 {network_retries} 次）"
    return {
        "code": error_code,
        "message": message,
        "retryable": error_code in _RETRYABLE_FAILURES,
        "issues": list(issues or []),
    }


def task_failure_from_row(task: TaskRun) -> dict[str, Any] | None:
    stored = task.error_details_jsonb.get("public_failure")
    if isinstance(stored, dict):
        return stored
    if task.status != "failed":
        return None
    return task_failure_view(task.error_code)


def event_view(event: TaskEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "task_run_id": event.task_run_id,
        "sequence_no": event.sequence_no,
        "event_type": event.event_type,
        "stage": event.stage,
        "payload": event.payload_jsonb,
        "occurred_at": time_view(event.occurred_at),
    }


def time_view(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


__all__ = [
    "agent_message_view",
    "agent_thread_view",
    "brief_version_view",
    "brief_view",
    "event_view",
    "provider_view",
    "source_view",
    "task_failure_view",
    "task_view",
    "time_view",
]
