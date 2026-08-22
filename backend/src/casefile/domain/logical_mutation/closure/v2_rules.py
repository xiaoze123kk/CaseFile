"""Policy-v2 closure rule registry."""

from __future__ import annotations

from casefile.domain.logical_mutation.closure.claims import evaluate_claim_rules
from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.closure.hypotheses import (
    evaluate_hypothesis_rules,
)
from casefile.domain.logical_mutation.closure.integration import (
    evaluate_integration_rules,
)
from casefile.domain.logical_mutation.closure.reasoning import (
    evaluate_reasoning_rules,
    evaluate_resolution_rules,
)
from casefile.domain.logical_mutation.closure.temporal import (
    evaluate_travel_time_rules,
)
from casefile.domain.logical_mutation.models import ClosureIssue


def evaluate_v2_rules(context: ClosureContext) -> list[ClosureIssue]:
    result: list[ClosureIssue] = []
    for evaluator in (
        evaluate_claim_rules,
        evaluate_hypothesis_rules,
        evaluate_reasoning_rules,
        evaluate_resolution_rules,
        evaluate_integration_rules,
        evaluate_travel_time_rules,
    ):
        result.extend(evaluator(context))
    return result
