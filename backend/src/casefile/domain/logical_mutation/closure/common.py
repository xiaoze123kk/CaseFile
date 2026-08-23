"""Shared helpers for v2 closure rules."""

from __future__ import annotations

from typing import cast

from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import (
    ClosureIssue,
    ClosureObjectRef,
    ClosureObjectRole,
)


def issue(
    context: ClosureContext,
    code: str,
    level: str,
    title: str,
    message: str,
    object_ids: tuple[str, ...],
    repair_kinds: tuple[str, ...] = (),
    *,
    object_roles: tuple[str, ...],
) -> ClosureIssue:
    targets = set(object_ids)
    caused = tuple(
        operation.operation_id
        for operation in context.mutation_set.operations
        if operation.object_id in targets
    )
    return ClosureIssue(
        code,
        level,  # type: ignore[arg-type]
        title,
        message,
        object_ids,
        tuple(
            ClosureObjectRef(object_id, cast(ClosureObjectRole, role))
            for object_id, role in zip(object_ids, object_roles, strict=True)
        ),
        caused,
        repair_kinds=repair_kinds,
    )
