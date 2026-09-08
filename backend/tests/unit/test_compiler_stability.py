from types import SimpleNamespace

from casefile.application.compiler.stability import compiler_stability


def test_task_success_without_novel_delivery_is_failure():
    result = compiler_stability(
        task_status="succeeded",
        attempt_count=1,
        prose_requested=True,
        shadow_status="inconclusive_infrastructure",
        schema_ids={"compiler.scene-plan.v2"},
        calls=[],
    )
    assert result["outcome"] == "failed"
    assert not result["novel_ready"] and not result["first_pass_success"]


def test_repaired_success_keeps_failed_call_and_is_not_first_pass():
    calls = [
        SimpleNamespace(
            prompt_component_id="scene_semantic_fill.batch.001", call_no=1, status="failed"
        ),
        SimpleNamespace(
            prompt_component_id="scene_semantic_fill.batch.10001", call_no=10001, status="succeeded"
        ),
    ]
    result = compiler_stability(
        task_status="succeeded",
        attempt_count=1,
        prose_requested=True,
        shadow_status="succeeded",
        schema_ids={"compiler.novel-candidate.v1"},
        calls=calls,
    )
    assert result["novel_ready"] and not result["first_pass_success"]
    assert result["repair_successes"] == result["repair_attempts"] == 1
    assert result["failure_stages"] == {"场景细化": 1}
