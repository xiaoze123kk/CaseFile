"""Production Hook policies retain Chat repair and novel protocol semantics."""

from copy import deepcopy

import pytest

from casefile.agent_runtime.chat_completion_hooks import coordinate_chat_candidate_validation
from casefile.agent_runtime.chat_execution import ChatExecutionRunner
from casefile.agent_runtime.models import CaseFileChatResult
from casefile.agent_runtime.novel_compile_hooks import check_novel_boundary
from casefile.benchmark.chat_outcome_eval import _request_for_task, build_outcome_tasks
from casefile.domain.narrative_compiler import CompilerContractError, canonical_json_sha256


def test_chat_hook_rejection_reaches_existing_repair_and_blocks_completion():
    task = build_outcome_tasks()[0]
    candidate = task.reference_candidate
    invalid = candidate.model_copy(update={"referenced_object_ids": ["missing_object"]})

    class Provider:
        calls = 0

        def chat(self, request):
            self.calls += 1
            return CaseFileChatResult(
                candidate=invalid if self.calls == 1 else candidate,
                usage={"input_tokens": 1, "output_tokens": 1},
            )

    provider = Provider()
    completed = []
    result = ChatExecutionRunner(provider).run(
        _request_for_task(task),
        complete=completed.append,
    )
    assert provider.calls == 2
    assert len(completed) == 1
    assert result.attempts == 2
    checks = [r for r in result.diagnostics["hooks"] if r["handler_id"] == "chat_candidate"]
    assert [r["status"] for r in checks] == ["failed", "succeeded"]


def test_goal_shared_completion_uses_hooks_without_mutating_input():
    task = build_outcome_tasks()[0]
    result = CaseFileChatResult(candidate=task.reference_candidate, usage={})
    original = result.candidate.model_dump(mode="json")
    records = []
    actual = coordinate_chat_candidate_validation(
        _request_for_task(task),
        result,
        hook_records=records,
    )
    assert result.candidate.model_dump(mode="json") == original
    assert actual.usage == result.usage
    assert records[0]["handler_id"] == "chat_candidate"


def test_upstream_hash_failure_retains_domain_code_and_metadata():
    content = {"schema_id": "example", "text": "private novel"}
    records = []
    with pytest.raises(CompilerContractError) as caught:
        check_novel_boundary(
            "prose_upstream",
            {"artifacts": [{"content": content, "hash": "0" * 64}]},
            records=records,
        )
    assert caught.value.reason_code == "compiler_prose_upstream_hash_mismatch"
    assert records[0]["status"] == "failed"
    assert "private novel" not in repr(records)
    check_novel_boundary(
        "prose_upstream",
        {"artifacts": [{"content": content, "hash": canonical_json_sha256(content)}]},
    )


def test_continuity_literary_block_is_advisory_but_bad_references_are_rejected():
    payload = {
        "actual_binding": ("request", "input", "prompt"),
        "expected_binding": ("request", "input", "prompt"),
        "allowed_scene_ids": ["scene_1"],
        "candidate": {
            "verdict": "blocked",
            "issues": [
                {
                    "scene_ids": ["scene_1"],
                    "reason": "读者可能已经知道",
                    "required_plan_change": "建议调整揭露方式",
                }
            ],
        },
    }
    original = deepcopy(payload)
    check_novel_boundary("continuity", payload)
    assert payload == original
    payload["allowed_scene_ids"] = ["scene_2"]
    with pytest.raises(ValueError, match="continuity_scene_ref_invalid"):
        check_novel_boundary("continuity", payload)
    payload["actual_binding"] = ("wrong", "input", "prompt")
    with pytest.raises(ValueError, match="continuity_response_binding_invalid"):
        check_novel_boundary("continuity", payload)


def test_scene_repair_cannot_modify_an_unrelated_scene():
    context = {
        "error": {"scene_id": "scene_1"},
        "candidate": {
            "scenes": [
                {"scene_id": "scene_1", "text": "old"},
                {"scene_id": "scene_2", "text": "keep"},
            ]
        },
    }
    proposal = {
        "scenes": [
            {"scene_id": "scene_1", "text": "fixed"},
            {"scene_id": "scene_2", "text": "keep"},
        ]
    }
    check_novel_boundary("scene_repair", {"repair_context": context, "proposal": proposal})
    proposal["scenes"][1]["text"] = "unrelated change"
    with pytest.raises(CompilerContractError) as caught:
        check_novel_boundary("scene_repair", {"repair_context": context, "proposal": proposal})
    assert caught.value.reason_code == "compiler_scene_repair_preservation_failed"


def test_unknown_novel_boundary_fails_closed():
    with pytest.raises(ValueError, match="Unknown novel hook boundary"):
        check_novel_boundary("misspelled_stage", {})
