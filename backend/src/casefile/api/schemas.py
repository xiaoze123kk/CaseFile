"""Strict HTTP request models for the first typed write slice."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from casefile_contracts import PublicRoutingInterpretation
from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile.application.commands import ProjectCreate
from casefile.domain.logical_mutation import ACTIVE_APPLY_POLICY


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreateRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)

    def command(self) -> ProjectCreate:
        return ProjectCreate(self.title, self.description, self.profile)


class ProjectUpdateRequest(StrictRequest):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    profile: dict[str, Any] | None = None


class ProviderSettingRequest(StrictRequest):
    provider: Literal["openai", "deepseek"] = "openai"
    api_key: str = Field(min_length=8, max_length=512)
    model_id: str = Field(default="gpt-5.6-sol", min_length=1, max_length=160)
    model_is_custom: bool = False

    @model_validator(mode="after")
    def provider_default_model(self) -> Self:
        if (
            self.provider == "deepseek"
            and self.model_id == "gpt-5.6-sol"
            and not self.model_is_custom
        ):
            self.model_id = "deepseek-v4-flash"
        return self


class BriefUpdateRequest(StrictRequest):
    expected_revision: int = Field(ge=1)
    content: dict[str, Any]


class BriefConfirmRequest(StrictRequest):
    expected_revision: int = Field(ge=1)


class SourceRecordCreateRequest(StrictRequest):
    source_kind: Literal["human_original", "human_revision"]
    content_text: str = Field(min_length=1)
    parent_source_record_id: int | None = Field(default=None, ge=1)


class BriefIntakeSourceUpdateRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)
    content_text: str = Field(min_length=1, max_length=100_000)
    parent_source_record_id: int | None = Field(default=None, ge=1)


class BriefIntakeQuestionAnswerRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)
    answer_mode: Literal["answer", "suggestion", "pending"]
    answer_text: str | None = Field(default=None, max_length=20_000)
    suggestion_index: int | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def answer_payload_matches_mode(self) -> Self:
        if self.answer_mode == "answer":
            if self.answer_text is None or not self.answer_text.strip():
                raise ValueError("answer mode requires non-blank answer_text")
            if self.suggestion_index is not None:
                raise ValueError("answer mode does not accept suggestion_index")
        elif self.answer_mode == "suggestion":
            if self.suggestion_index is None:
                raise ValueError("suggestion mode requires suggestion_index")
            if self.answer_text is not None:
                raise ValueError("suggestion mode does not accept answer_text")
        elif self.answer_text is not None or self.suggestion_index is not None:
            raise ValueError("pending mode does not accept answer content")
        return self


class BriefIntakeCandidateCreateRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)
    content: dict[str, Any]
    parent_candidate_id: int | None = Field(default=None, ge=1)
    activate: bool = True


class BriefIntakeCandidateActionRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)


class BriefIntakeCandidateAdoptRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)
    expected_brief_revision: int = Field(ge=1)


class BriefIntakeQuestionsTaskRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"


class BriefIntakeSynthesizeTaskRequest(StrictRequest):
    expected_intake_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"
    base_candidate_id: int | None = Field(default=None, ge=1)
    instruction: str | None = Field(default=None, min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def dialogue_revision_has_base(self) -> Self:
        if self.instruction is not None and self.base_candidate_id is None:
            raise ValueError("instruction requires base_candidate_id")
        return self


class BriefPolishTaskRequest(StrictRequest):
    source_record_id: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"
    polish_mode: Literal["proofread", "rewrite", "narrative_enhance"] = "rewrite"


class BriefAnchorExtractTaskRequest(StrictRequest):
    expected_brief_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"
    mode: Literal["extract", "suggest_author_answer"] = "extract"
    content: dict[str, Any] | None = None


class BriefStrategyOptionsTaskRequest(StrictRequest):
    brief_version_id: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"
    refresh: bool = False


class GenerateTaskRequest(StrictRequest):
    brief_version_id: int = Field(ge=1)
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "openai"
    candidate_strategy: Literal[
        "balanced",
        "structure_first",
        "atmosphere_first",
        "reasoning_first",
    ] = "balanced"
    candidate_strategy_attempt: int = Field(default=1, ge=1)


class DraftCandidateAdoptRequest(StrictRequest):
    expected_current_draft_id: int = Field(ge=1)


class DraftActivateRequest(StrictRequest):
    expected_current_draft_id: int = Field(ge=1)


class ResumeGenerationTaskRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    expected_brief_revision: int = Field(ge=1)


class AgentThreadCreateRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)


class AgentThreadUpdateRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    is_pinned: bool | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def has_change(self) -> Self:
        concurrency_fields = {"expected_draft_id", "expected_draft_revision"}
        if not (self.model_fields_set - concurrency_fields):
            raise ValueError("at least one thread field is required")
        return self


class AgentChatFocus(StrictRequest):
    object_ids: list[str] = Field(default_factory=list, max_length=50)
    event_ids: list[str] = Field(default_factory=list, max_length=50)
    validation_issue_ids: list[str] = Field(default_factory=list, max_length=50)
    view: str | None = Field(default=None, max_length=64)


class AgentChatRoutingHint(StrictRequest):
    entrypoint: Literal["free_text", "preset", "issue_action"] = "free_text"
    preset_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def preset_id_matches_entrypoint(self) -> Self:
        if self.entrypoint == "preset":
            if self.preset_id is None or not self.preset_id.strip():
                raise ValueError("preset entrypoint requires preset_id")
        elif self.preset_id is not None:
            raise ValueError("preset_id is only allowed for the preset entrypoint")
        return self


class AgentMessageCreateRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=100_000)
    provider: Literal["openai", "deepseek"] = "openai"
    focus: AgentChatFocus | None = None
    routing_hint: AgentChatRoutingHint | None = None
    delivery_mode: Literal["new_goal", "steer", "follow_up", "replace"] | None = None
    expected_goal_id: int | None = Field(default=None, ge=1)
    expected_goal_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def goal_delivery_shape(self) -> Self:
        control_modes = {"steer", "follow_up", "replace"}
        if self.delivery_mode in control_modes:
            if self.expected_goal_id is None or self.expected_goal_revision is None:
                raise ValueError("steer, follow_up, and replace require expected Goal identity")
        elif self.expected_goal_id is not None or self.expected_goal_revision is not None:
            raise ValueError("expected Goal identity requires a control delivery mode")
        return self


class AgentRoutingFeedbackRequest(StrictRequest):
    interpretation: PublicRoutingInterpretation | None = None
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def has_feedback(self) -> Self:
        if self.interpretation is None and (self.note is None or not self.note.strip()):
            raise ValueError("interpretation or note is required")
        return self


class AgentPatchApplyRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    change_ids: list[int] | None = Field(default=None, max_length=200)
    confirmation_token: str | None = Field(default=None, min_length=1, max_length=256)
    accepted_warning_ids: list[str] = Field(default_factory=list, max_length=100)
    confirmation_note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def valid_public_confirmation(self) -> Self:
        if self.change_ids is not None and len(set(self.change_ids)) != len(
            self.change_ids
        ):
            raise ValueError("change_ids must be unique")
        if len(set(self.accepted_warning_ids)) != len(self.accepted_warning_ids):
            raise ValueError("accepted_warning_ids must be unique")
        if self.accepted_warning_ids and not (self.confirmation_note or "").strip():
            raise ValueError("confirmation_note is required")
        return self


class AgentPatchUndoRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class AgentPatchRedoRequest(AgentPatchUndoRequest):
    pass


class AgentPatchSimulateRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    base_revision: int = Field(ge=1)
    change_ids: list[int] | None = Field(default=None, max_length=200)
    accepted_warning_ids: list[str] = Field(default_factory=list, max_length=100)
    confirmation_note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def valid_public_confirmation(self) -> Self:
        if self.change_ids is not None and len(set(self.change_ids)) != len(
            self.change_ids
        ):
            raise ValueError("change_ids must be unique")
        if len(set(self.accepted_warning_ids)) != len(self.accepted_warning_ids):
            raise ValueError("accepted_warning_ids must be unique")
        if self.accepted_warning_ids and not (self.confirmation_note or "").strip():
            raise ValueError("confirmation_note is required")
        return self


class VerificationFindingReviewRequest(StrictRequest):
    decision: Literal["confirm", "resolve", "reopen", "dismiss"]
    note: str | None = Field(default=None, max_length=2_000)


class VerificationRerunRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    provider: Literal["openai", "deepseek"] = "deepseek"


class ObjectPatchRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    changes: dict[str, Any] = Field(min_length=1)


class ResolutionConclusionActionRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)


class LogicalNormalizationRequest(ResolutionConclusionActionRequest):
    pass


class MutationCreateOperationRequest(StrictRequest):
    operation_type: Literal["create_object"]
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    collection: str
    object_value: dict[str, Any]


class MutationUpdateOperationRequest(StrictRequest):
    operation_type: Literal["update_field"]
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    object_id: str
    field_path: str
    old_value: Any
    new_value: Any
    expected_object_revision: int | None = Field(default=None, ge=1)


class MutationDeleteOperationRequest(StrictRequest):
    operation_type: Literal["delete_object"]
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    object_id: str
    old_object_value: dict[str, Any] | None = None


MutationOperationRequest = Annotated[
    MutationCreateOperationRequest
    | MutationUpdateOperationRequest
    | MutationDeleteOperationRequest,
    Field(discriminator="operation_type"),
]


class LogicalMutationPreviewRequest(StrictRequest):
    mutation_set_id: str = Field(min_length=1, max_length=128)
    base_draft_id: int = Field(ge=1)
    base_revision: int = Field(ge=1)
    mode: Literal["normal", "restructure"] = "normal"
    closure_policy_version: str = Field(default=ACTIVE_APPLY_POLICY, max_length=64)
    operations: list[MutationOperationRequest] = Field(min_length=1, max_length=100)
    target_finding_keys: list[str] = Field(default_factory=list, max_length=100)
    accepted_debt_finding_keys: list[str] = Field(default_factory=list, max_length=100)
    debt_acceptance_reason: str | None = Field(default=None, max_length=2_000)


class LogicalMutationApplyRequest(LogicalMutationPreviewRequest):
    expected_candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class TimelineTimePreviewRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    proposed_time: dict[str, Any] = Field(min_length=1)


ExposureObjectType = Literal[
    "entity",
    "relationship",
    "location",
    "event",
    "information_unit",
    "claim",
    "hypothesis",
    "reasoning_path",
    "resolution_spec",
    "constraint",
    "structure_lock",
]


class ExposurePlanRefRequest(StrictRequest):
    object_type: ExposureObjectType
    object_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,127}$")


ExposurePlanningObligationLevel = Literal["hard", "soft"]


class ParticipantCoverageObligationRequest(StrictRequest):
    kind: Literal["participant_coverage"]
    obligation_key: str = Field(pattern=r"^obligation_[a-z0-9][a-z0-9_]{0,150}$")
    level: ExposurePlanningObligationLevel
    eligible_refs: list[ExposurePlanRefRequest] = Field(min_length=1, max_length=100)
    min_distinct: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def valid_participant_coverage(self) -> Self:
        keys = [(item.object_type, item.object_id) for item in self.eligible_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("eligible_refs must be unique")
        if self.min_distinct > len(keys):
            raise ValueError("min_distinct cannot exceed eligible_refs")
        return self


class BasisRefCoverageObligationRequest(StrictRequest):
    kind: Literal["basis_ref_coverage"]
    obligation_key: str = Field(pattern=r"^obligation_[a-z0-9][a-z0-9_]{0,150}$")
    level: ExposurePlanningObligationLevel
    required_refs: list[ExposurePlanRefRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_required_refs(self) -> Self:
        keys = [(item.object_type, item.object_id) for item in self.required_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("required_refs must be unique")
        return self


class HypothesisCoverageRefRequest(StrictRequest):
    object_type: Literal["hypothesis"]
    object_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,127}$")


class HypothesisCoverageObligationRequest(StrictRequest):
    kind: Literal["hypothesis_coverage"]
    obligation_key: str = Field(pattern=r"^obligation_[a-z0-9][a-z0-9_]{0,150}$")
    level: ExposurePlanningObligationLevel
    required_refs: list[HypothesisCoverageRefRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_required_refs(self) -> Self:
        ids = [item.object_id for item in self.required_refs]
        if len(ids) != len(set(ids)):
            raise ValueError("required_refs must be unique")
        return self


ExposurePlanningObligationRequest = Annotated[
    ParticipantCoverageObligationRequest
    | BasisRefCoverageObligationRequest
    | HypothesisCoverageObligationRequest,
    Field(discriminator="kind"),
]


class ExposurePlanEntryRequest(StrictRequest):
    entry_key: str = Field(
        pattern=r"^exposure_[a-z0-9][a-z0-9_]{0,150}$",
    )
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4_000)
    refs: list[ExposurePlanRefRequest] = Field(min_length=1, max_length=100)
    planning_obligations: list[ExposurePlanningObligationRequest] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def unique_refs(self) -> Self:
        keys = [(item.object_type, item.object_id) for item in self.refs]
        if len(keys) != len(set(keys)):
            raise ValueError("refs must be unique within one entry")
        obligation_keys = [item.obligation_key for item in self.planning_obligations]
        if len(obligation_keys) != len(set(obligation_keys)):
            raise ValueError("obligation_key must be unique within one entry")
        return self


class ExposurePlanPutRequest(StrictRequest):
    expected_draft_id: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    entries: list[ExposurePlanEntryRequest] = Field(max_length=2_000)

    @model_validator(mode="after")
    def unique_entry_keys(self) -> Self:
        keys = [item.entry_key for item in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("entry_key must be unique within one revision")
        obligation_keys = [
            obligation.obligation_key
            for entry in self.entries
            for obligation in entry.planning_obligations
        ]
        if len(obligation_keys) != len(set(obligation_keys)):
            raise ValueError("obligation_key must be unique within one revision")
        return self


class CompilerProfileCreateRequest(StrictRequest):
    profile_key: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
    )
    name: str = Field(min_length=1, max_length=160)
    schema_id: str = Field(min_length=3, max_length=160)
    payload: dict[str, Any]


class CompilerProfileVersionCreateRequest(StrictRequest):
    expected_current_version_id: int = Field(ge=1)
    schema_id: str = Field(min_length=3, max_length=160)
    payload: dict[str, Any]


class CompileRunCreateRequest(StrictRequest):
    mode: Literal["preview", "canonical"]
    expected_draft_id: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)
    canon_version_id: int | None = Field(default=None, ge=1)
    exposure_plan_revision_id: int | None = Field(default=None, ge=1)
    compiler_profile_version_id: int = Field(ge=1)
    planner_provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    )

    @model_validator(mode="after")
    def canon_matches_mode(self) -> Self:
        if self.mode == "preview" and self.canon_version_id is not None:
            raise ValueError("preview compile cannot bind canon_version_id")
        if self.mode == "canonical" and self.canon_version_id is None:
            raise ValueError("canonical compile requires canon_version_id")
        return self
