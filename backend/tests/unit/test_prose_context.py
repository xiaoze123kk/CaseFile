"""Projection preserves source evidence while removing redundant generation state."""

from copy import deepcopy

import pytest

from casefile.agent_runtime.prose_context import scene_generation_context
from casefile.agent_runtime.prose_continuity import ContinuityReview


def test_projection_scopes_state_without_mutation_or_text_truncation():
    row = {"subject_ref": {"object_id": "a"}, "location_ref": {"object_id": "room"}}
    other = {"subject_ref": {"object_id": "b"}, "location_ref": {"object_id": "elsewhere"}}
    source = {
        "participant_refs": [{"object_id": "a"}],
        "objective": "完整任务" * 2000,
        "state_before": {"locations": [row, row, other]},
        "expected_state_after": {"locations": [row, row, other]},
        "object_catalog": [{"label": "证据", "value": "完整原文" * 2000}],
    }
    frozen = deepcopy(source)
    view = scene_generation_context(source)
    assert source == frozen
    assert view["state_before"]["locations"] == [row]
    assert view["objective"] == source["objective"]
    assert view["object_catalog"] == source["object_catalog"]
    assert view["state_changes"] == {}


@pytest.mark.parametrize(
    "candidate",
    [
        {"verdict": "blocked", "issues": []},
        {
            "verdict": "pass",
            "issues": [{"scene_ids": ["s"], "reason": "冲突", "required_plan_change": "调整"}],
        },
    ],
)
def test_continuity_verdict_cannot_hide_findings(candidate):
    with pytest.raises(ValueError):
        ContinuityReview.model_validate(candidate)
