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
  | "brief_to_draft";
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
  input_hash: string;
  attempt_count: number;
  usage: Record<string, unknown>;
  result_snapshot_id: number | null;
  result: BriefPolishResult | BriefAnchorExtractResult | null;
  error_code: string | null;
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
  return error instanceof Error ? error.message : "请求未完成，请检查 API 与数据库状态。";
}
