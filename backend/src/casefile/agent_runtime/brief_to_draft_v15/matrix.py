"""Deterministic competition-matrix scope, evaluation, and join for v15 Evidence.

The Evidence graph drafter owns information_units, claims, hypotheses, and
reasoning_paths. The matrix topology is never a model decision: the program
derives the exact (hypothesis, information) cells from the path-grounded
inputs, asks the model to judge each fixed cell, and deterministically joins
the judgments back. The model can therefore never add, drop, or duplicate
matrix columns; it can only judge a cell poorly, and a failed evaluation is
repaired with the previous output plus only the remaining cell issues.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

from casefile.agent_runtime.brief_to_draft_v8.compiler import LinkerValidationError
from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    EvidenceAssessmentIR,
    EvidenceLogicIRV2,
)
from casefile.agent_runtime.brief_to_draft_v15.contracts import (
    MatrixAssessmentIR,
    MatrixCellSpecIR,
    MatrixEvaluationOutputV1,
)
from casefile.agent_runtime.models import GenerationRequest

if TYPE_CHECKING:
    from casefile.agent_runtime.brief_to_draft_v8.workflow import ComponentCall


class ModelStep(Protocol):
    """The workflow's component model-call primitive, injected to avoid a cycle."""

    def __call__(
        self,
        request: GenerationRequest,
        call_component: ComponentCall,
        *,
        component_id: str,
        prompt_component: str,
        stage: str,
        output_type: type[BaseModel],
        input_payload: dict[str, Any],
        input_contract_id: str | None = None,
    ) -> Awaitable[tuple[dict[str, Any], dict[str, Any]]]: ...


def derive_matrix_cells(
    hypotheses_by_resolution: dict[str, list[Any]],
    used_information_by_hypothesis: dict[str, set[str]],
) -> list[MatrixCellSpecIR]:
    """Deterministically derive the matrix skeleton from path-grounded inputs.

    The model never decides which cells exist: the cells are exactly the
    hypothesis × information pairs of each competition group's path scope.
    """

    cells: list[MatrixCellSpecIR] = []
    for competitors in hypotheses_by_resolution.values():
        if len(competitors) < 2:
            continue
        matrix_information = set().union(
            *(used_information_by_hypothesis[item.local_key] for item in competitors)
        )
        for hypothesis in sorted(competitors, key=lambda item: item.local_key):
            for information_key in sorted(matrix_information):
                cells.append(
                    MatrixCellSpecIR(
                        hypothesis_key=hypothesis.local_key,
                        information_key=information_key,
                    )
                )
    return cells


def matrix_evaluation_issues(
    output: MatrixEvaluationOutputV1,
    cells: list[MatrixCellSpecIR],
) -> list[dict[str, Any]]:
    """Require exactly one judgment per deterministic cell and nothing else."""

    expected = {(cell.hypothesis_key, cell.information_key) for cell in cells}
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, assessment in enumerate(output.assessments):
        cell_key = (assessment.hypothesis_key, assessment.information_key)
        if cell_key in seen:
            issues.append(
                {
                    "code": "duplicate_matrix_assessment",
                    "path": f"/assessments/{index}",
                    "message": "同一矩阵格子不能重复判定。",
                    "component_id": "evidence_matrix",
                    "failure_layer": "evidence_matrix",
                    "schema_id": "matrix-evaluation-v1",
                    "ir_path": "/assessments",
                }
            )
            continue
        seen.add(cell_key)
        if cell_key not in expected:
            issues.append(
                {
                    "code": "unscoped_matrix_assessment",
                    "path": f"/assessments/{index}",
                    "message": (
                        f"格子 ({assessment.hypothesis_key!r}, "
                        f"{assessment.information_key!r}) 不在程序计算的矩阵范围内。"
                    ),
                    "component_id": "evidence_matrix",
                    "failure_layer": "evidence_matrix",
                    "schema_id": "matrix-evaluation-v1",
                    "ir_path": "/assessments",
                }
            )
    for cell in cells:
        if (cell.hypothesis_key, cell.information_key) in seen:
            continue
        issues.append(
            {
                "code": "missing_matrix_assessment",
                "path": f"/cells/{cell.hypothesis_key}/{cell.information_key}",
                "message": (
                    f"矩阵格子 ({cell.hypothesis_key!r}, {cell.information_key!r}) "
                    "缺少判定。"
                ),
                "component_id": "evidence_matrix",
                "failure_layer": "evidence_matrix",
                "schema_id": "matrix-evaluation-v1",
                "ir_path": "/assessments",
            }
        )
    return issues


def join_matrix_assessments(
    evidence: EvidenceLogicIRV2,
    output: MatrixEvaluationOutputV1,
    cells: list[MatrixCellSpecIR],
) -> EvidenceLogicIRV2:
    """Attach deterministic matrix judgments onto each hypothesis in cell order."""

    judgments = {
        (assessment.hypothesis_key, assessment.information_key): assessment
        for assessment in output.assessments
    }
    cells_by_hypothesis: dict[str, list[MatrixCellSpecIR]] = {}
    for cell in cells:
        cells_by_hypothesis.setdefault(cell.hypothesis_key, []).append(cell)
    hypotheses = []
    for hypothesis in evidence.hypotheses:
        ordered_cells = cells_by_hypothesis.get(hypothesis.local_key, [])
        hypotheses.append(
            hypothesis.model_copy(
                update={
                    "evidence_assessments": [
                        _assessment_for_cell(judgments, cell) for cell in ordered_cells
                    ]
                }
            )
        )
    return evidence.model_copy(update={"hypotheses": hypotheses})


def _assessment_for_cell(
    judgments: dict[tuple[str, str], MatrixAssessmentIR],
    cell: MatrixCellSpecIR,
) -> EvidenceAssessmentIR:
    judgment = judgments[(cell.hypothesis_key, cell.information_key)]
    return EvidenceAssessmentIR(
        information_key=cell.information_key,
        effect=judgment.effect,
        strength=judgment.strength,
        rationale=judgment.rationale,
    )


async def evaluate_evidence_matrix(
    request: GenerationRequest,
    call_component: ComponentCall,
    blueprint: CaseBlueprintV1,
    evidence: EvidenceLogicIRV2,
    *,
    context_payload: dict[str, Any],
    hypotheses_by_resolution: dict[str, list[Any]],
    used_information_by_hypothesis: dict[str, set[str]],
    model_step: ModelStep,
) -> tuple[EvidenceLogicIRV2, list[dict[str, Any]]]:
    """Judge program-computed cells, repair scoped failures, and join the result.

    Each call re-judges only the requested cells, and a failed evaluation is
    retried with its previous output plus only the remaining cell issues, so a
    matrix failure never regenerates information_units, claims, or hypotheses.
    """

    cells = derive_matrix_cells(hypotheses_by_resolution, used_information_by_hypothesis)
    if not cells:
        return evidence, []
    usage: list[dict[str, Any]] = []
    input_payload: dict[str, Any] = {
        "context_pack": context_payload,
        "blueprint": blueprint.model_dump(mode="json"),
        "evidence_graph": evidence.model_dump(mode="json"),
        "cells": [cell.model_dump(mode="json") for cell in cells],
    }
    output = MatrixEvaluationOutputV1(assessments=[])
    for attempt in range(3):
        if attempt:
            input_payload["previous_output"] = output.model_dump(mode="json")
        raw, step_usage = await model_step(
            request,
            call_component,
            component_id="evidence_matrix",
            prompt_component="matrix",
            stage="matrix_evaluation",
            output_type=MatrixEvaluationOutputV1,
            input_payload=input_payload,
            input_contract_id=None,
        )
        usage.append(step_usage)
        output = MatrixEvaluationOutputV1.model_validate(raw)
        issues = matrix_evaluation_issues(output, cells)
        if not issues:
            break
        input_payload["targeted_repair_issues"] = issues
    else:
        raise LinkerValidationError(matrix_evaluation_issues(output, cells))
    return join_matrix_assessments(evidence, output, cells), usage


__all__ = [
    "derive_matrix_cells",
    "evaluate_evidence_matrix",
    "join_matrix_assessments",
    "matrix_evaluation_issues",
]
