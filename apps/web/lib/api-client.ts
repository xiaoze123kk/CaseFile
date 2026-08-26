import type { CaseFile, CoreMetadata, NarrativeIR } from "@casefile/contracts";

export interface LegacyTemporalPositionV1 {
  start: string;
  end: string | null;
  precision: "second" | "minute" | "hour" | "day" | "approximate" | "unknown";
}

export type LegacyCaseFileEventV1 = CoreMetadata & {
  id: CaseFile["events"][number]["id"];
  title: CaseFile["events"][number]["title"];
  truth_status: CaseFile["events"][number]["truth_status"];
  time: LegacyTemporalPositionV1;
  participant_refs: CaseFile["events"][number]["participant_refs"];
  location_ref: CaseFile["events"][number]["location_ref"];
  cause_refs: CaseFile["events"][number]["cause_refs"];
  effect_refs: CaseFile["events"][number]["effect_refs"];
  observed_by_refs: CaseFile["events"][number]["observed_by_refs"];
};

export type LegacyCaseFileV1 = Omit<CaseFile, "schema_version" | "events"> & {
  schema_version: "1.0";
  events: LegacyCaseFileEventV1[];
};

export type CaseFileDocument = CaseFile | LegacyCaseFileV1;
export type TimelineTemporalPosition = CaseFile["events"][number]["time"];

export const API_ROOT =
  process.env.NEXT_PUBLIC_CASEFILE_API_URL ?? "http://127.0.0.1:8000/api/v1";

export interface ApiErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface CompileArtifactContentView {
  artifact_id: number;
  compile_run_id: number;
  artifact_kind: "input_manifest" | "narrative_ir";
  artifact_key: string;
  schema_id: string;
  content_hash: string;
  agent_step_run_id: number;
  content: Record<string, unknown> | NarrativeIR;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: ApiErrorBody,
  ) {
    super(
      /[\u3400-\u9fff]/u.test(body.message)
        ? body.message
        : "请求未能完成，请稍后重试。",
    );
  }
}

export type ProviderName = "openai" | "deepseek";
export type TaskType =
  | "brief_polish"
  | "brief_anchor_extract"
  | "brief_intake_questions"
  | "brief_intake_synthesize"
  | "brief_strategy_options"
  | "brief_to_draft"
  | "casefile_chat";
export type ResolutionMode =
  | "author_anchored"
  | "agent_proposed"
  | "open";
export type ConclusionMode =
  | "unique"
  | "finite_multiple"
  | "optimal"
  | "probabilistic"
  | "open_interpretation"
  | "multiple_endings"
  | "undetermined";
export type ConstraintStrength = "hard" | "soft";
export type SourceKind =
  | "human_original"
  | "agent_polish_proposal"
  | "human_revision";

export interface ProjectView {
  id: number;
  title: string;
  description?: string | null;
  profile: Record<string, unknown>;
  status: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  casefile_id: number;
  current_draft_id: number;
  draft: {
    id: number;
    title: string;
    revision: number;
    schema_version: string;
    status: string;
  };
}

export interface ProviderSettingView {
  provider: ProviderName;
  model_id: string;
  model_is_custom: boolean;
  config_version: number;
  credential_status: string;
  masked_api_key: string;
  validated_at: string | null;
  validation_error_code: string | null;
  default_budget: Record<string, unknown>;
}

export interface BriefView {
  brief_id: number;
  public_id: string;
  draft_revision: number;
  content: BriefContent | Record<string, never>;
  current_version_id: number | null;
  current_version_no?: number | null;
}

export interface BriefContent {
  source_record_ids: number[];
  creative_intent: string;
  reasoning_proposition: string;
  resolution_mode: ResolutionMode;
  conclusion_mode: ConclusionMode;
  author_answer: string | null;
  author_anchors: BriefAnchor[];
  boundary_text: string | null;
  creative_constraints: CreativeConstraint[];
  core_selling_points?: string[];
  content_outline?: string[];
  scope_estimate?: string | null;
  risk_notes?: string[];
}

export type BriefIntakeStage =
  | "idea"
  | "questions"
  | "confirmation"
  | "brief_review";
export type BriefIntakeFieldSource =
  | "user_original"
  | "user_confirmed"
  | "agent_suggestion"
  | "unresolved";
export type BriefIntakeAnswerStatus =
  | "unanswered"
  | "user_answered"
  | "suggestion_accepted"
  | "pending";
export type BriefIntakeConstraintCategory =
  | "must_keep"
  | "must_avoid"
  | "scope"
  | "cast"
  | "duration"
  | "content_scale"
  | "other";

export interface BriefIntakeFieldSources {
  concept: BriefIntakeFieldSource;
  core_selling_points: BriefIntakeFieldSource;
  content_outline: BriefIntakeFieldSource;
  reasoning_goal: BriefIntakeFieldSource;
  resolution_mode: BriefIntakeFieldSource;
  conclusion_mode: BriefIntakeFieldSource;
  author_answer: BriefIntakeFieldSource;
  constraints: BriefIntakeFieldSource;
  scope_estimate: BriefIntakeFieldSource;
  risk_notes: BriefIntakeFieldSource;
}

export interface BriefIntakeConstraint {
  constraint_key: string;
  category: BriefIntakeConstraintCategory;
  statement: string;
  strength: ConstraintStrength;
  confirmed: boolean;
  source: BriefIntakeFieldSource;
}

export interface BriefIntakePendingDecision {
  decision_key: string;
  prompt: string;
  impact: string;
  source: "unresolved";
}

export interface BriefIntakeCandidateContent {
  concept: string;
  core_selling_points: string[];
  content_outline: string[];
  reasoning_goal: string;
  resolution_mode: ResolutionMode;
  conclusion_mode: ConclusionMode;
  author_answer: string | null;
  constraints: BriefIntakeConstraint[];
  pending_decisions: BriefIntakePendingDecision[];
  scope_estimate: string | null;
  risk_notes: string[];
  field_sources: BriefIntakeFieldSources;
}

export interface BriefIntakeQuestionView {
  question_key: string;
  ordinal: number;
  prompt: string;
  impact: string;
  required: boolean;
  suggestions: string[];
  answer_status: BriefIntakeAnswerStatus;
  answer_text: string | null;
  answer_source: BriefIntakeFieldSource | null;
}

export interface BriefIntakeCandidateView {
  candidate_id: number;
  parent_candidate_id: number | null;
  generated_by_task_run_id: number | null;
  origin:
    | "agent_synthesis"
    | "dialogue_revision"
    | "manual_edit"
    | "legacy_import";
  basis_input_hash: string;
  content_hash: string;
  content: BriefIntakeCandidateContent;
  is_current: boolean;
  is_adopted: boolean;
  is_saved: boolean;
  is_stale: boolean;
  can_activate: boolean;
  saved_at: string | null;
  created_at: string | null;
}

export interface BriefIntakeView {
  brief_intake_id: number;
  project_id: number;
  revision: number;
  stage: BriefIntakeStage;
  current_source: SourceRecordView | null;
  current_questions_task_run_id: number | null;
  questions: BriefIntakeQuestionView[];
  hard_questions_resolved: boolean;
  current_candidate_id: number | null;
  adopted_candidate_id: number | null;
  candidates: BriefIntakeCandidateView[];
  pending_decisions: BriefIntakePendingDecision[];
  brief: {
    brief_id: number;
    draft_revision: number;
    current_version_id: number | null;
    has_content: boolean;
  };
  updated_at: string | null;
}

export interface BriefIntakeAdoptionView {
  intake: BriefIntakeView;
  brief: BriefView;
}

export interface BriefAnchor {
  anchor_id: string;
  statement: string;
}

export interface CreativeConstraint {
  constraint_id: string;
  statement: string;
  strength: ConstraintStrength;
}

export interface SourceRecordView {
  source_record_id: number;
  source_kind: SourceKind;
  content_text: string;
  content_hash: string;
  parent_source_record_id: number | null;
  generated_by_task_run_id: number | null;
  created_at: string;
}

export type PolishMode = "proofread" | "rewrite" | "narrative_enhance";
export type AnchorExtractMode = "extract" | "suggest_author_answer";

export interface BriefPolishResult {
  input_hash: string;
  polished_text: string;
  preserved_intent_summary: string;
  ambiguities: string[];
  introduced_details?: string[];
  polish_mode?: PolishMode;
  proposal_source_record: SourceRecordView;
}

export interface BriefAnchorExtractResult {
  input_hash: string;
  suggested_author_answer?: string;
  author_anchors: Array<{ statement: string }>;
  creative_constraints: Array<{
    statement: string;
    suggested_strength: ConstraintStrength;
  }>;
  warnings: string[];
}

export interface BriefIntakeQuestionsResult {
  input_hash: string;
  questions: Array<{
    question_key: string;
    ordinal: number;
    prompt: string;
    impact: string;
    required: boolean;
    suggestions: string[];
  }>;
  stale: boolean;
}

export interface BriefIntakeSynthesizeResult {
  input_hash: string;
  candidate_id: number;
  content_hash: string;
  origin: "agent_synthesis" | "dialogue_revision";
  stale: boolean;
}

export interface BriefVersionView {
  brief_version_id: number;
  version_no: number;
  content: BriefContent;
}

export interface TaskFailureIssue {
  code: string;
  path: string;
  message: string;
}

export interface TaskFailure {
  code: string;
  message: string;
  retryable: boolean;
  issues: TaskFailureIssue[];
}

export interface AgentDiagnosticIssue {
  component_id: string;
  failure_layer: string;
  schema_id: string | null;
  code: string;
  path: string;
  message: string;
}

export interface AgentComponentStepView {
  step_run_id: number;
  attempt_no: number;
  component_id: string;
  parent_component_id: string | null;
  execution_no: number;
  status: "pending" | "running" | "succeeded" | "failed" | "reused" | "skipped";
  schema_id: string;
  input_hash: string;
  output_hash: string | null;
  failure_layer: string | null;
  issues: AgentDiagnosticIssue[];
  recoverable: boolean;
  resumed_from_step_run_id: number | null;
}

export interface GenerationCandidateSummary {
  candidate_strategy: CandidateStrategy;
  candidate_strategy_version: string;
  candidate_strategy_label: string;
  title: string;
  content_hash: string;
  object_counts: Record<string, number>;
  reasoning_questions: string[];
  constraint_statements: string[];
}

export type CandidateStrategy =
  | "balanced"
  | "structure_first"
  | "atmosphere_first"
  | "reasoning_first";

export interface TaskView {
  task_run_id: number;
  project_id: number;
  task_type: TaskType;
  prompt_version?: string;
  status:
    | "queued"
    | "running"
    | "cancelling"
    | "succeeded"
    | "failed"
    | "cancelled";
  stage: string;
  provider: ProviderName;
  model_id: string;
  input_draft_revision: number;
  input_brief_revision: number | null;
  input_source_record_id: number | null;
  input_brief_intake_id: number | null;
  input_brief_intake_revision: number | null;
  base_brief_intake_candidate_id: number | null;
  agent_thread_id: number | null;
  input_message_id: number | null;
  output_message_id: number | null;
  input_hash: string;
  candidate_strategy: CandidateStrategy | null;
  attempt_count: number;
  usage: Record<string, unknown>;
  result_snapshot_id: number | null;
  result:
    | BriefPolishResult
    | BriefAnchorExtractResult
    | BriefIntakeQuestionsResult
    | BriefIntakeSynthesizeResult
    | BriefStrategyOptionsResult
    | GenerationCandidateSummary
    | AgentChatTaskResult
    | null;
  error_code: string | null;
  failure: TaskFailure | null;
  component_steps: AgentComponentStepView[];
  created_at: string | null;
  updated_at: string | null;
}

export interface DraftCandidateView extends GenerationCandidateSummary {
  task_run_id: number;
  brief_version_no: number;
  is_current_brief: boolean;
  is_current: boolean;
  is_adopted: boolean;
  can_adopt: boolean;
  provider: ProviderName;
  model_id: string;
  candidate_strategy_attempt: number;
  attempt_count: number;
  created_at: string | null;
  completed_at: string | null;
}

/** A validated candidate loaded without replacing or mutating Current Draft. */
export interface DraftCandidatePreviewView extends DraftCandidateView {
  preview: true;
  read_only: true;
  content: CaseFileDocument;
}

export interface DraftCandidateAdoption {
  task_run_id: number;
  draft_id: number;
  revision: number;
  title: string;
  content_hash: string;
  adopted: true;
}

export interface TaskEventView {
  event_id: number;
  task_run_id: number;
  sequence_no: number;
  event_type: string;
  stage: string;
  payload: Record<string, unknown>;
  occurred_at: string;
}

export interface AgentChatFocus {
  object_ids: string[];
  event_ids: string[];
  validation_issue_ids: string[];
  view: string | null;
}

export type AgentChatEntrypoint = "free_text" | "preset" | "issue_action";

export interface AgentChatRoutingHint {
  entrypoint: AgentChatEntrypoint;
  preset_id?: string;
}

export type AgentRoutingCorrectIntent =
  | "question"
  | "analysis"
  | "explain_issue"
  | "edit_request"
  | "validate_request"
  | "logic_audit"
  | "unsupported_action"
  | "clarify"
  | "out_of_scope";

export interface AgentRoutingFeedbackResult {
  message_id: number;
  task_run_id: number;
  acknowledged: true;
}

export interface AgentChatRoutingSummary {
  router_version?: string;
  route_hash?: string;
  route_source?: string;
  intent?: string | null;
  rewrite_strategy?: string;
  suggestion_policy?: string;
  suppressed_count?: number;
  tool_metrics?: Record<string, unknown>;
}

export interface AgentAuditFindingView {
  finding_id: string;
  kind: AgentAuditFindingKind;
  severity: AgentAuditFindingSeverity;
  title: string;
  statement: string;
  needs_manual_review: boolean;
  evidence_object_ids: string[];
  evidence_event_ids: string[];
  evidence_validation_issue_ids: string[];
  impact_refs?: Array<{
    object_type?: string;
    object_id?: string;
  }>;
}

export type AgentAuditFindingKind =
  | "dangling_ref"
  | "contradiction"
  | "temporal"
  | "motivation_gap"
  | "scope_gap";

export type AgentAuditFindingSeverity = "S1" | "S2" | "S3";

export interface AgentChatTaskResult {
  answer: string;
  referenced_object_ids: string[];
  referenced_event_ids: string[];
  referenced_validation_issue_ids: string[];
  suggested_view: AgentSuggestedView | null;
  patch_set_id: number | null;
  stale: boolean;
  audit_findings?: AgentAuditFindingView[];
  verification_run_id?: number | null;
  routing?: AgentChatRoutingSummary;
  tool_metrics?: Record<string, unknown>;
}

export type AgentSuggestedView =
  | "timeline"
  | "relations"
  | "reasoning"
  | "map"
  | "export"
  | "compile"
  | "evidence";

export interface AgentThreadView {
  thread_id: number;
  title: string;
  title_source: "auto" | "user";
  is_pinned: boolean;
  status: "active" | "archived";
  last_message_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentPatchOperationView {
  operation_id: number;
  operation_key: string;
  ordinal: number;
  object_id: string | null;
  object_type: string | null;
  target_collection?: string;
  operation_type: "add" | "remove" | "replace" | "field_update" | "create_object" | "update_field" | "delete_object";
  field_path: string;
  expected_object_revision: number | null;
  old_value: unknown;
  new_value: unknown;
  reason: string;
  decision: string | null;
  reviewed_at: string | null;
  finding_ids?: number[];
}

export interface AgentPatchSetView {
  patch_set_id: number;
  thread_id: number;
  source_message_id: number;
  task_run_id: number | null;
  base_draft_revision: number;
  closure_policy_version?: string;
  mutation_mode?: "normal" | "restructure";
  baseline_hash?: string | null;
  candidate_hash?: string | null;
  reason_summary: string | null;
  status: "pending" | "stale" | "applied" | "undone" | "rejected";
  is_stale: boolean;
  applied_from_revision: number | null;
  applied_to_revision: number | null;
  undone_to_revision: number | null;
  operations: AgentPatchOperationView[];
  validation_warning: boolean;
  validator_issues: Record<string, unknown>[];
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentPatchSimulationView {
  valid: boolean;
  can_apply: boolean;
  reason_code: string | null;
  closure_policy_version?: string;
  baseline_hash?: string;
  candidate_hash?: string;
  fixed_finding_keys: string[];
  introduced_finding_keys?: string[];
  worsened_finding_keys?: string[];
  residual_target_finding_keys?: string[];
  authorization_required_finding_keys?: string[];
  residual_finding_keys?: string[];
  new_finding_keys?: string[];
  pending_recheck_finding_keys?: string[];
  structure_lock_conflicts?: string[];
  impact?: { collections: string[]; counts: Record<string, number>; full_rebuild: boolean; reasons: string[] };
  normalized_mutation: {
    mutation_set_id: string;
    mode: "normal" | "restructure";
    actor: "author" | "agent" | "import" | "system";
    operation_ids: string[];
    mechanical_operations: Array<{
      operation_id: string;
      operation_type: "update_field";
      reason_code: string;
      object_id: string;
      field_path: string;
      old_value: unknown;
      new_value: unknown;
    }>;
  } | null;
  impact_cone: {
    root_object_ids: string[];
    direct_object_ids: string[];
    transitive_object_ids: string[];
    affected_resolution_ids: string[];
    dependency_paths: string[][];
    cycles: Array<{ relation: string; object_ids: string[] }>;
  } | null;
}

export interface AgentPatchSimulationResult {
  patch_set_id: number;
  draft_id: number;
  base_revision: number;
  can_apply: boolean;
  simulation: AgentPatchSimulationView;
}

export interface AgentMessageView {
  message_id: number;
  thread_id: number;
  sequence_no: number;
  role: "user" | "assistant" | "system";
  status: "pending" | "completed" | "failed";
  content: string | null;
  task: TaskView | null;
  referenced_object_ids: string[];
  referenced_event_ids: string[];
  referenced_validation_issue_ids: string[];
  suggested_view: AgentSuggestedView | null;
  patch_set: AgentPatchSetView | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentSendMessageView {
  thread: AgentThreadView;
  user_message: AgentMessageView;
  assistant_message: AgentMessageView;
  task: TaskView;
}

export interface AgentPatchApplyResult extends AgentPatchSetView {
  draft_revision: number;
  pre_apply_verification_run_id?: number | null;
  post_apply_verification_run_id?: number | null;
  simulation?: AgentPatchSimulationView;
}

export interface DraftView {
  project_id: number;
  casefile_id: number;
  draft_id: number;
  title: string;
  revision: number;
  schema_version: string;
  status: string;
  document_status: string;
  brief_version_id: number | null;
  created_at: string;
  updated_at: string;
  content: CaseFileDocument | null;
}

export interface TimelineTimePreviewView {
  draft_id: number;
  base_revision: number;
  event_id: string;
  before_time: TimelineTemporalPosition;
  proposed_time: TimelineTemporalPosition;
  can_confirm: boolean;
  order_change: {
    from_index: number | null;
    to_index: number | null;
    crossed_event_ids: string[];
  };
  relative_dependent_event_ids: string[];
  affected_event_ids: string[];
  validation: {
    status: "passed" | "failed";
    issue_count: number;
    issues: Array<{
      code: string;
      path: string;
      message: string;
    }>;
  };
}

export interface ExposurePlanRefView {
  object_type: string;
  object_id: string;
}

export interface ExposurePlanEntryView {
  entry_key: string;
  sequence_no: number;
  title: string;
  note: string | null;
  refs: ExposurePlanRefView[];
}

export interface ExposurePlanView {
  plan_id: number;
  draft_id: number;
  revision: number;
  updated_at: string;
  entries: ExposurePlanEntryView[];
}

export interface DraftSummaryView {
  draft_id: number;
  title: string;
  revision: number;
  schema_version: string;
  status: string;
  document_status: string;
  brief_version_id: number | null;
  brief_version_no: number | null;
  has_content: boolean;
  is_current: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkbenchValidationIssueView {
  issue_id: string;
  code: string;
  path: string;
  message: string;
  severity: "error" | "S1" | "S2";
  target: {
    object_ref: {
      object_type: string;
      object_id: string;
    } | null;
    field_path: string;
  };
  evidence_refs?: Array<{
    object_type: string;
    object_id: string;
  }>;
  impact_refs?: Array<{
    object_type: string;
    object_id: string;
  }>;
  fix_hint?: string | null;
  explanation?: string | null;
}

export interface WorkbenchValidationView {
  status: "passed" | "failed" | "unavailable";
  validator: "casefile.contracts.validate_casefile";
  schema_version: string;
  issue_count: number;
  issues: WorkbenchValidationIssueView[];
  reason: "draft_has_no_confirmed_brief" | null;
}

export interface VerificationFindingRefView {
  ref_kind: "object" | "event" | "validation_issue" | "patch_operation" | "related";
  ref_key: string;
  role: "evidence" | "target" | "related";
}

export interface VerificationFindingView {
  finding_id: number;
  verification_run_id: number;
  finding_key: string;
  kind: "deterministic" | "llm";
  severity: "info" | "warning" | "error" | "blocker";
  status: "open" | "resolved" | "reopened" | "dismissed";
  title: string;
  message: string;
  suggested_fix: string | null;
  rule_code: string;
  confidence: number | null;
  draft_revision: number;
  refs: VerificationFindingRefView[];
  payload: Record<string, unknown>;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
}

export interface VerificationRunView {
  verification_run_id: number;
  project_id: number;
  casefile_id: number;
  draft_id: number;
  source_task_run_id: number | null;
  patch_set_id: number | null;
  trigger: "chat" | "manual" | "pre_apply" | "post_apply";
  profile: "fast" | "balanced" | "strict";
  engine_version: string;
  draft_revision: number;
  input_hash: string;
  status: "running" | "succeeded" | "failed";
  started_at: string;
  completed_at: string | null;
  finding_count: number;
  deterministic_finding_count: number;
  llm_finding_count: number;
  findings: VerificationFindingView[];
}

export interface VerificationReadModelView {
  enabled: boolean;
  latest_run: VerificationRunView | null;
  findings: VerificationFindingView[];
}

export interface WorkbenchSourceView {
  trace_id: string;
  source_table: "source_records";
  source_record_id: number;
  source_kind: SourceKind;
  content_text: string;
  content_hash: string;
  parent_source_record_id: number | null;
  generated_by_task_run_id: number | null;
  created_by_user_id: number;
  created_at: string;
}

export interface WorkbenchContractSourceRefView {
  source_fragment_id: string;
  paths: string[];
}

export interface WorkbenchAuditActorView {
  kind: "user" | "agent" | "system" | "import";
  user_id: number | null;
  ref: string | null;
}

export interface WorkbenchAuditEntryView {
  entry_id: string;
  source_table: "audit_events" | "draft_operations";
  record_id: number;
  occurred_at: string;
  actor: WorkbenchAuditActorView;
  action: string;
  target_type: string;
  target_id: number | string;
  trace_id: string | null;
  details: Record<string, unknown>;
}

export interface WorkbenchContextView {
  project_id: number;
  draft_id: number;
  draft_revision: number;
  validation: WorkbenchValidationView;
  sources: WorkbenchSourceView[];
  contract_source_refs: WorkbenchContractSourceRefView[];
  audit_entries: WorkbenchAuditEntryView[];
  verification?: VerificationReadModelView;
}

export interface BriefStrategyOption {
  strategy: Exclude<CandidateStrategy, "balanced">;
  direction: string;
  focus: string;
  strengths: string[];
  tradeoffs: string[];
  brief_fit: string;
}

export interface BriefStrategyOptionsResult {
  input_hash: string;
  strategy_version: "candidate-strategy-v1";
  options: BriefStrategyOption[];
  recommended_strategy: Exclude<CandidateStrategy, "balanced">;
  recommendation_reason: string;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  actorId: number;
  body?: unknown;
}

export async function apiRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    headers: {
      "Content-Type": "application/json",
      "X-CaseFile-User-Id": String(options.actorId),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const fallback: ApiErrorBody = {
      code: "request_failed",
      message: `请求失败（HTTP ${response.status}）`,
      details: {},
    };
    throw new ApiError(response.status, await response.json().catch(() => fallback));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function streamTaskEvents(
  path: string,
  actorId: number,
  onEvent: (event: TaskEventView) => void,
  signal: AbortSignal,
  lastEventId = 0,
) {
  const response = await fetch(`${API_ROOT}${path}`, {
    headers: {
      Accept: "text/event-stream",
      "Last-Event-ID": String(lastEventId),
      "X-CaseFile-User-Id": String(actorId),
    },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`SSE 连接失败（HTTP ${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    buffer += decoder.decode(result.value, { stream: true }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data) as TaskEventView);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export async function listProjects(actorId: number) {
  return apiRequest<ProjectView[]>("/projects", { actorId });
}

export async function listDrafts(actorId: number, projectId: number) {
  return apiRequest<DraftSummaryView[]>(`/projects/${projectId}/drafts`, {
    actorId,
  });
}

export async function activateDraft(
  actorId: number,
  projectId: number,
  draftId: number,
  expectedCurrentDraftId: number,
) {
  return apiRequest<DraftView>(
    `/projects/${projectId}/drafts/${draftId}/activate`,
    {
      actorId,
      method: "POST",
      body: { expected_current_draft_id: expectedCurrentDraftId },
    },
  );
}

export async function fetchWorkbenchContext(actorId: number, projectId: number) {
  return apiRequest<WorkbenchContextView>(
    `/projects/${projectId}/workbench-context`,
    { actorId },
  );
}

export async function listVerificationFindings(
  actorId: number,
  projectId: number,
  options: { draftId?: number; status?: VerificationFindingView["status"] } = {},
) {
  const params = new URLSearchParams();
  if (options.draftId !== undefined) params.set("draft_id", String(options.draftId));
  if (options.status !== undefined) params.set("status", options.status);
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return apiRequest<VerificationFindingView[]>(
    `/projects/${projectId}/verification-findings${query}`,
    { actorId },
  );
}

export async function rerunVerification(
  actorId: number,
  projectId: number,
  draftId: number,
  draftRevision: number,
  provider: ProviderName = "deepseek",
) {
  return apiRequest<AgentSendMessageView>(
    `/projects/${projectId}/verification-runs/rerun`,
    {
      actorId,
      method: "POST",
      body: {
        expected_draft_id: draftId,
        expected_draft_revision: draftRevision,
        provider,
      },
    },
  );
}

export async function getVerificationRun(
  actorId: number,
  projectId: number,
  verificationRunId: number,
) {
  return apiRequest<VerificationRunView>(
    `/projects/${projectId}/verification-runs/${verificationRunId}`,
    { actorId },
  );
}

export async function reviewVerificationFinding(
  actorId: number,
  projectId: number,
  findingId: number,
  decision: "confirm" | "resolve" | "reopen" | "dismiss",
  note?: string,
) {
  return apiRequest<VerificationFindingView>(
    `/projects/${projectId}/verification-findings/${findingId}/review`,
    {
      actorId,
      method: "POST",
      body: { decision, ...(note?.trim() ? { note: note.trim() } : {}) },
    },
  );
}

export async function listAgentThreads(
  actorId: number,
  projectId: number,
  options: { query?: string; includeArchived?: boolean } = {},
) {
  const params = new URLSearchParams();
  if (options.query) params.set("query", options.query);
  if (options.includeArchived) params.set("include_archived", "true");
  const query = params.size > 0 ? `?${params.toString()}` : "";
  return apiRequest<AgentThreadView[]>(
    `/projects/${projectId}/agent/threads${query}`,
    { actorId },
  );
}

export async function createAgentThread(
  actorId: number,
  projectId: number,
  draftId: number,
  draftRevision: number,
  title?: string,
) {
  return apiRequest<AgentThreadView>(`/projects/${projectId}/agent/threads`, {
    actorId,
    method: "POST",
    body: {
      expected_draft_id: draftId,
      expected_draft_revision: draftRevision,
      ...(title === undefined ? {} : { title }),
    },
  });
}

export async function updateAgentThread(
  actorId: number,
  projectId: number,
  threadId: number,
  draftId: number,
  draftRevision: number,
  changes: { title?: string; is_pinned?: boolean; archived?: boolean },
) {
  return apiRequest<AgentThreadView>(
    `/projects/${projectId}/agent/threads/${threadId}`,
    {
      actorId,
      method: "PATCH",
      body: {
        expected_draft_id: draftId,
        expected_draft_revision: draftRevision,
        ...changes,
      },
    },
  );
}

export async function listAgentMessages(
  actorId: number,
  projectId: number,
  threadId: number,
  afterSequence = 0,
) {
  return apiRequest<AgentMessageView[]>(
    `/projects/${projectId}/agent/threads/${threadId}/messages?after_sequence=${afterSequence}`,
    { actorId },
  );
}

export async function sendAgentMessage(
  actorId: number,
  projectId: number,
  threadId: number,
  draftId: number,
  draftRevision: number,
  content: string,
  provider: ProviderName = "openai",
  focus?: AgentChatFocus,
  routingHint?: AgentChatRoutingHint,
) {
  return apiRequest<AgentSendMessageView>(
    `/projects/${projectId}/agent/threads/${threadId}/messages`,
    {
      actorId,
      method: "POST",
      body: {
        expected_draft_id: draftId,
        expected_draft_revision: draftRevision,
        content,
        provider,
        ...(focus === undefined ? {} : { focus }),
        ...(routingHint === undefined ? {} : { routing_hint: routingHint }),
      },
    },
  );
}

export async function sendAgentRoutingFeedback(
  actorId: number,
  projectId: number,
  threadId: number,
  messageId: number,
  correctIntent?: AgentRoutingCorrectIntent,
  note?: string,
) {
  return apiRequest<AgentRoutingFeedbackResult>(
    `/projects/${projectId}/agent/threads/${threadId}/messages/${messageId}/routing-feedback`,
    {
      actorId,
      method: "POST",
      body: {
        ...(correctIntent === undefined ? {} : { correct_intent: correctIntent }),
        ...(note === undefined || note.trim() === "" ? {} : { note: note.trim() }),
      },
    },
  );
}

export async function applyAgentPatchSet(
  actorId: number,
  projectId: number,
  patchSetId: number,
  draftId: number,
  expectedRevision: number,
  operationIds: number[] | null,
  targetFindingIds?: number[],
  acceptedDebtFindingKeys: string[] = [],
  debtAcceptanceReason?: string,
) {
  return apiRequest<AgentPatchApplyResult>(
    `/projects/${projectId}/agent/patch-sets/${patchSetId}/apply`,
    {
      actorId,
      method: "POST",
      body: {
        expected_draft_id: draftId,
        expected_revision: expectedRevision,
        ...(operationIds === null ? {} : { operation_ids: operationIds }),
        ...(targetFindingIds === undefined ? {} : { target_finding_ids: targetFindingIds }),
        accepted_debt_finding_keys: acceptedDebtFindingKeys,
        ...(debtAcceptanceReason === undefined ? {} : { debt_acceptance_reason: debtAcceptanceReason }),
      },
    },
  );
}

export async function simulateAgentPatchSet(
  actorId: number,
  projectId: number,
  patchSetId: number,
  draftId: number,
  baseRevision: number,
  operationIds: number[] | null,
  targetFindingIds?: number[],
  acceptedDebtFindingKeys: string[] = [],
  debtAcceptanceReason?: string,
) {
  return apiRequest<AgentPatchSimulationResult>(`/projects/${projectId}/agent/patch-sets/${patchSetId}/simulate`, {
    actorId,
    method: "POST",
    body: {
      expected_draft_id: draftId,
      base_revision: baseRevision,
      ...(operationIds === null ? {} : { operation_ids: operationIds }),
      ...(targetFindingIds === undefined ? {} : { target_finding_ids: targetFindingIds }),
      accepted_debt_finding_keys: acceptedDebtFindingKeys,
      ...(debtAcceptanceReason === undefined ? {} : { debt_acceptance_reason: debtAcceptanceReason }),
    },
  });
}

export async function undoAgentPatchSet(
  actorId: number,
  projectId: number,
  patchSetId: number,
  draftId: number,
  expectedRevision: number,
) {
  return apiRequest<AgentPatchApplyResult>(
    `/projects/${projectId}/agent/patch-sets/${patchSetId}/undo`,
    {
      actorId,
      method: "POST",
      body: {
        expected_draft_id: draftId,
        expected_revision: expectedRevision,
      },
    },
  );
}

export async function redoAgentPatchSet(
  actorId: number,
  projectId: number,
  patchSetId: number,
  draftId: number,
  expectedRevision: number,
) {
  return apiRequest<AgentPatchApplyResult>(
    `/projects/${projectId}/agent/patch-sets/${patchSetId}/redo`,
    {
      actorId,
      method: "POST",
      body: {
        expected_draft_id: draftId,
        expected_revision: expectedRevision,
      },
    },
  );
}

export async function archiveProject(actorId: number, projectId: number) {
  return apiRequest<ProjectView>(`/projects/${projectId}/archive`, {
    actorId,
    method: "POST",
  });
}

export async function unarchiveProject(actorId: number, projectId: number) {
  return apiRequest<ProjectView>(`/projects/${projectId}/unarchive`, {
    actorId,
    method: "POST",
  });
}

export async function clearArchivedProjects(actorId: number) {
  return apiRequest<{ cleared: number }>("/projects/clear-archived", {
    actorId,
    method: "POST",
  });
}

export async function getCompileArtifactContent(
  actorId: number,
  projectId: number,
  compileRunId: number,
  artifactId: number,
) {
  return apiRequest<CompileArtifactContentView>(
    `/projects/${projectId}/compile-runs/${compileRunId}/artifacts/${artifactId}`,
    { actorId },
  );
}

export function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    const localizedMessages: Record<string, string> = {
      request_invalid: "提交内容不符合接口要求，请检查后重试。",
      identity_required: "当前请求缺少本地用户身份。",
      identity_invalid: "当前本地用户身份无效。",
      base_revision_required: "缺少草稿版本信息，请刷新页面后重试。",
      base_revision_invalid: "草稿版本信息无效，请刷新页面后重试。",
      draft_id_required: "缺少工作稿标识，请刷新页面后重试。",
      draft_id_invalid: "工作稿标识无效，请刷新页面后重试。",
      draft_revision_conflict: "草稿已被更新，请刷新后重新提交。",
      current_draft_changed: "当前工作稿已切换，请刷新后重试。",
      draft_locked: "这份工作稿已锁定，不能执行当前操作。",
      draft_empty: "这份工作稿尚未生成正文。",
      project_archived: "项目已归档，不能执行当前操作。",
      brief_revision_conflict: "创作简报已被更新，请刷新后重新提交。",
      resource_conflict: "当前修改与已保存的数据冲突，请刷新后重试。",
      database_unavailable: "数据库暂时不可用，请稍后重试。",
      database_error: "数据库请求失败，请稍后重试。",
      internal_error: "请求暂时无法完成，请稍后重试。",
      not_found: "没有找到请求的数据。",
      method_not_allowed: "当前操作不受支持。",
      brief_intake_already_adopted:
        "当前建案已进入正式创作简报审阅，不能再回退修改。",
      provider_setting_required: "请先配置当前模型服务。",
      provider_credential_in_use: "仍有任务正在使用这把密钥，请等待任务结束后再删除。",
      draft_not_empty: "当前草稿已有内容，不能再次执行全量生成。",
      draft_schema_upgrade_required:
        "当前工作稿仍使用历史时间契约，请先完成契约升级再重新生成。",
      brief_version_not_current: "当前创作简报版本已过期，请刷新后重试。",
      brief_extraction_input_empty: "请先填写作者答案或创作规则。",
      source_content_blank: "来源原稿不能为空。",
      brief_invalid: "创作简报内容不完整，请检查后重试。",
    };
    const localizedMessage = localizedMessages[error.body.code];
    if (localizedMessage) return localizedMessage;
    return error.message;
  }
  return error instanceof Error ? error.message : "请求未完成，请检查 API 与数据库状态。";
}
