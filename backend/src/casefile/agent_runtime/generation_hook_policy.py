"""Frozen v17 hook bindings; no handler imports or registration side effects."""

from casefile.agent_runtime.generation_hooks import HookBinding, HookEvent

VALIDATOR_IDS = (
    "final_candidate_issues",
    "_blueprint_path_plan_issues",
    "_v16_blueprint_relationship_coverage_issues",
    "_blueprint_creator_chinese_issues",
    "temporal_plan_issues",
    "_evidence_assessment_issues",
    "_v11_story_issues",
    "temporal_story_issues",
    "_v15_story_person_name_issues",
    "_v16_story_relationship_coverage_issues",
    "_brief_quality_requirement_issues",
    "_creator_chinese_issues",
)
V17_HOOKS = (
    HookBinding("prerequisites", "1", HookEvent.BEFORE_STAGE),
    *(HookBinding(name, "1", HookEvent.AFTER_ARTIFACT, component=name) for name in VALIDATOR_IDS),
    HookBinding("observe_stage", "1", HookEvent.STAGE_FINISHED, required=False),
    HookBinding("observe_stage", "1", HookEvent.STAGE_FAILED, required=False),
)
