"""Stable Worker failure classification, redaction, and retry metadata helpers."""

from __future__ import annotations

import re
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from casefile.agent_runtime.providers import ProviderProtocolError
from casefile.contracts import ContractValidationError
from casefile.data_postgres.models import TaskRun


class TaskCancellationRequested(RuntimeError):
    """Raised when a running Worker observes an accepted cancellation."""


def error_code(error: Exception) -> str:
    explicit = getattr(error, "error_code", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    if error.__class__.__name__ == "MaxTurnsExceeded":
        return "max_turns_exceeded"
    if "structured_output_validation_failed" in str(error):
        return "structured_output_validation_failed"
    if "completion_validation" in str(error):
        return "completion_validation_failed"
    if isinstance(error, (ContractValidationError, ProviderProtocolError)):
        return "candidate_validation_failed"
    if isinstance(error, AuthenticationError):
        return "provider_authentication_failed"
    if isinstance(error, RateLimitError):
        return "provider_rate_limited"
    if isinstance(error, APITimeoutError):
        return "provider_timeout"
    if isinstance(error, APIConnectionError):
        return "provider_connection_failed"
    return "generation_failed"


def merge_numeric_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, int) and not isinstance(value, bool):
            merged[key] = int(merged.get(key, 0)) + value
        else:
            merged[key] = value
    return merged


def network_retries(task: TaskRun) -> int:
    retries = int(task.budget_jsonb.get("network_retries", 2))
    return max(0, min(retries, 5))


def failure_validation_issues(
    validation_errors: list[dict[str, Any]],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for batch in validation_errors:
        for raw_issue in batch.get("issues", []):
            if not isinstance(raw_issue, dict):
                continue
            issue = {
                "code": str(raw_issue.get("code", "validation_failed")),
                "path": str(raw_issue.get("path", "")),
                "message": str(raw_issue.get("message", "结构校验失败")),
            }
            key = (issue["code"], issue["path"], issue["message"])
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)
            if len(issues) == 20:
                return issues
    return issues


def safe_error_message(error: Exception, sensitive_values: tuple[str, ...]) -> str:
    message = str(error)
    for sensitive in sensitive_values:
        if sensitive:
            message = message.replace(sensitive, "[REDACTED]")
    message = re.sub(
        r"(?i)\b(?:bearer\s+)?sk-[a-z0-9._-]{8,}\b",
        "[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)(api[_ -]?key\s*[:=]\s*)\S+",
        r"\1[REDACTED]",
        message,
    )
    return message[:500] or type(error).__name__


__all__ = [
    "TaskCancellationRequested",
    "error_code",
    "failure_validation_issues",
    "merge_numeric_usage",
    "network_retries",
    "safe_error_message",
]
