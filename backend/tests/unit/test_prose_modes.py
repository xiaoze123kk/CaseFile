"""Execution strategy is frozen separately from literary acceptance."""

import pytest

from casefile.agent_runtime.prose_runtime import prose_runtime_binding
from casefile.api.schemas import CompileRunCreateRequest
from casefile.domain.narrative_compiler import canonical_json_sha256


def test_mode_freezes_distinct_budgets_and_keeps_legacy_default():
    full = prose_runtime_binding(20)
    quick = prose_runtime_binding(20, "quick_draft")
    auto = prose_runtime_binding(20, "auto_edit")
    legacy_auto = prose_runtime_binding(20, "auto_edit", runtime_version="prose-shadow-runtime-v13")
    assert full == prose_runtime_binding(20, "full_polish")
    assert full["max_logical_calls"] == 460
    assert quick["max_logical_calls"] == 40
    assert quick["limits"]["judge_calls_per_scene"] == 0
    assert quick["limits"]["generation_repairs_per_call"] == 1
    assert auto["max_logical_calls"] == 160
    assert auto["limits"]["judge_calls_per_scene"] == 2
    assert auto["limits"]["rewrite_rounds"] == 1
    assert auto["limits"]["physical_requests_per_scene"] == 8
    assert auto["prompts"]["prose_auto_edit_judge"]["version"] == "prose-auto-edit-judge-v1"
    assert auto["version"] == "prose-shadow-runtime-v14"
    assert legacy_auto["version"] == "prose-shadow-runtime-v13"
    assert legacy_auto["limits"]["physical_requests_per_scene"] == 8
    assert canonical_json_sha256(quick) != canonical_json_sha256(full)
    assert CompileRunCreateRequest.model_fields["prose_mode"].default == "full_polish"


def test_unknown_mode_is_not_silently_interpreted_as_full():
    with pytest.raises(ValueError, match="compiler_prose_mode_invalid"):
        prose_runtime_binding(2, "unknown")  # type: ignore[arg-type]
