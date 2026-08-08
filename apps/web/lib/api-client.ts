import type { CaseFile } from "@casefile/contracts";

const API_ROOT =
  process.env.NEXT_PUBLIC_CASEFILE_API_URL ?? "http://127.0.0.1:8000/api/v1";

export interface ApiErrorBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
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
  draft: { id: number; revision: number; schema_version: string; status: string };
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
  attempt_count: number;
  created_at: string | null;
  completed_at: string | null;
}

export interface DraftCandidateAdoption {
  task_run_id: number;
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

export interface DraftView {
  project_id: number;
  revision: number;
  schema_version: string;
  status: string;
  content: CaseFile | null;
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

export function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    const localizedMessages: Record<string, string> = {
      request_invalid: "提交内容不符合接口要求，请检查后重试。",
      identity_required: "当前请求缺少本地用户身份。",
      identity_invalid: "当前本地用户身份无效。",
      base_revision_required: "缺少草稿版本信息，请刷新页面后重试。",
      base_revision_invalid: "草稿版本信息无效，请刷新页面后重试。",
      draft_revision_conflict: "草稿已被更新，请刷新后重新提交。",
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
      brief_version_not_current: "当前创作简报版本已过期，请刷新后重试。",
      brief_extraction_input_empty: "请先填写作者底牌或创作边界。",
      source_content_blank: "来源原稿不能为空。",
      brief_invalid: "创作简报内容不完整，请检查后重试。",
    };
    const localizedMessage = localizedMessages[error.body.code];
    if (localizedMessage) return localizedMessage;
    return error.message;
  }
  return error instanceof Error ? error.message : "请求未完成，请检查 API 与数据库状态。";
}
