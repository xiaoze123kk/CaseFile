"""Shared helpers for v2 closure rules."""

from __future__ import annotations

from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import ClosureIssue


def issue(
    context: ClosureContext,
    code: str,
    level: str,
    title: str,
    message: str,
    object_ids: tuple[str, ...],
    repair_kinds: tuple[str, ...] = (),
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
        caused,
        repair_kinds=repair_kinds,
    )
