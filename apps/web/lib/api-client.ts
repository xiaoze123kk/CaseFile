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

export interface ProjectView {
  id: number;
  title: string;
  profile: Record<string, unknown>;
  draft: { id: number; revision: number; schema_version: string; status: string };
}

export interface ProviderSettingView {
  provider: string;
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
  source_text: string;
  one_line_concept: string;
  core_mystery: string;
  player_goal: string;
  gameplay_loop: string;
  constraints: string[];
  open_questions: string[];
  project_profile: Record<string, unknown>;
}

export interface BriefVersionView {
  brief_version_id: number;
  version_no: number;
  content: BriefContent;
}

export interface TaskView {
  task_run_id: number;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  model_id: string;
  input_draft_revision: number;
  attempt_count: number;
  usage: Record<string, unknown>;
  result_snapshot_id: number | null;
  error_code: string | null;
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
