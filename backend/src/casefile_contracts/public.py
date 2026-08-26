# generated from contracts/schemas; DO NOT EDIT BY HAND.

from ._internal import (
    AgentGenerateRequest,
    AgentGenerateResult,
    ArtifactKind,
    BriefIntakeQuestion,
    BriefIntakeQuestionSet,
    CanonBinding,
    CompileInputManifest,
    CompileMode,
    CompilerArtifactRef,
    CompilerDiagnostic,
    CompilerProfileBinding,
    CompilerSourceRef,
    ExposureBinding,
    NovelPlanCandidate,
    NovelPlanIR,
    PlanningProblem,
    PlanSkeleton,
    SceneCompilerInputBundle,
    SceneCompilerModelView,
    ScenePlanCandidate,
    SceneSemanticFillProposal,
    SemanticFillProposal,
    SkeletonProposal,
    SnapshotBinding,
    StoryPlanStructuralPatch,
    TaskEvent,
    TaskRun,
)
from .brief import Schema as Brief
from .brief_intake import Schema as BriefIntakeCandidate
from .casefile import Schema as CaseFile
from .narrative_ir import Schema as NarrativeIR
from .novel_profile import Schema as NovelProfile
from .patch_candidate import Schema as PatchCandidate
from .planner_input import Schema as PlannerInputBundle
from .planner_input_v2 import Schema as PlannerInputBundleV2
from .planner_input_v3 import Schema as PlannerInputBundleV3
from .planner_model_view_v3 import Schema as PlannerModelViewV3
from .planner_model_view_v4 import Schema as PlannerModelViewV4
from .scene_compiler import Schema as SceneCompilerInputBundleV2
from .scene_plan import Schema as ScenePlanIR
from .scene_plan_v2 import Schema as ScenePlanIRV2
from .validation_issue import Schema as ValidationIssue

ScenePlan = ScenePlanIR

__all__ = [
    "AgentGenerateRequest",
    "AgentGenerateResult",
    "ArtifactKind",
    "Brief",
    "BriefIntakeCandidate",
    "BriefIntakeQuestion",
    "BriefIntakeQuestionSet",
    "CaseFile",
    "CanonBinding",
    "CompileInputManifest",
    "CompileMode",
    "PlanSkeleton",
    "PlanningProblem",
    "CompilerArtifactRef",
    "CompilerDiagnostic",
    "CompilerProfileBinding",
    "CompilerSourceRef",
    "ExposureBinding",
    "NovelPlanCandidate",
    "NovelPlanIR",
    "StoryPlanStructuralPatch",
    "SemanticFillProposal",
    "SceneCompilerInputBundle",
    "SceneCompilerInputBundleV2",
    "SceneCompilerModelView",
    "ScenePlanCandidate",
    "SceneSemanticFillProposal",
    "ScenePlan",
    "ScenePlanIR",
    "ScenePlanIRV2",
    "SkeletonProposal",
    "NovelProfile",
    "PlannerInputBundle",
    "PlannerInputBundleV2",
    "PlannerInputBundleV3",
    "PlannerModelViewV3",
    "PlannerModelViewV4",
    "NarrativeIR",
    "PatchCandidate",
    "TaskEvent",
    "TaskRun",
    "SnapshotBinding",
    "ValidationIssue",
]