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
    super(body.message);
  }
}

export type ProviderName = "openai" | "deepseek";
export type TaskType =
  | "brief_polish"
  | "brief_anchor_extract"
  | "brief_to_draft"
  | "casefile_chat";
export type ResolutionMode =
  | "author_anchored"
  | "agent_proposed"
  | "open";
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
}

export interface BriefContent {
  source_record_ids: number[];
  creative_intent: string;
  reasoning_proposition: string;
  resolution_mode: ResolutionMode;
  author_answer: string | null;
  author_anchors: BriefAnchor[];
  boundary_text: string | null;
  creative_constraints: CreativeConstraint[];
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

export interface BriefPolishResult {
  input_hash: string;
  polished_text: string;
  preserved_intent_summary: string;
  ambiguities: string[];
  proposal_source_record: SourceRecordView;
}

export interface BriefAnchorExtractResult {
  input_hash: string;
  author_anchors: Array<{ statement: string }>;
  creative_constraints: Array<{
    statement: string;
    suggested_strength: ConstraintStrength;
  }>;
  warnings: string[];
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
  agent_thread_id: number | null;
  input_message_id: number | null;
  output_message_id: number | null;
  input_hash: string;
  attempt_count: number;
  usage: Record<string, unknown>;
  result_snapshot_id: number | null;
  result: BriefPolishResult | BriefAnchorExtractResult | null;
  error_code: string | null;
  failure: TaskFailure | null;
  created_at: string | null;
  updated_at: string | null;
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
  content: CaseFileDocument | null;
}

export interface CaseFileObject {
  id: string;
  title?: string;
  name?: string;
  description?: string;
  entity_type?: string;
  truth_status?: string;
  [key: string]: unknown;
}

export interface CaseFileDocument {
  casefile_id: string;
  title: string;
  schema_version: string;
  [collection: string]: unknown;
  entities: CaseFileObject[];
  locations: CaseFileObject[];
  events: CaseFileObject[];
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
      provider_setting_required: "请先配置当前模型服务。",
      draft_not_empty: "当前草稿已有内容，不能再次执行全量生成。",
      brief_version_not_current: "当前创作简报版本已过期，请刷新后重试。",
      brief_extraction_input_empty: "请先填写作者底牌或创作边界。",
      source_content_blank: "来源原稿不能为空。",
      brief_invalid: "创作简报内容不完整，请检查后重试。",
    };
    const localizedMessage = localizedMessages[error.body.code];
    if (localizedMessage) return localizedMessage;
    if (/[\u3400-\u9fff]/u.test(error.body.message)) return error.body.message;
    return `请求未能完成（错误代码：${error.body.code}）。`;
  }
  return error instanceof Error ? error.message : "请求未完成，请检查 API 与数据库状态。";
}
