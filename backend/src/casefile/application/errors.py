"""Stable application errors shared by HTTP and future worker adapters."""

from __future__ import annotations

from typing import Any


class ApplicationError(Exception):
    """An expected failure with a public code and transport-neutral details."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def not_found(resource: str) -> ApplicationError:
    return ApplicationError(
        "not_found",
        f"{resource} was not found",
        status_code=404,
    )


def revision_conflict(*, expected: int, received: int) -> ApplicationError:
    return ApplicationError(
        "draft_revision_conflict",
        "Draft revision is stale",
        status_code=409,
        details={"current_revision": expected, "received_revision": received},
    )
