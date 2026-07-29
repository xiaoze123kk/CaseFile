import {
  apiRequest,
  type ProviderName,
  type TaskEventView,
} from "@/lib/api-client";

import type { WorkbenchObjectRef } from "./workbench-model";

export type AgentThreadStatus = "active" | "archived";

export interface AgentThreadView {
  thread_id: number;
  title: string;
  status: AgentThreadStatus;
  is_pinned: boolean;
  last_message_at?: string | null;
  updated_at?: string | null;
}

export type AgentReferenceView = WorkbenchObjectRef;

export interface ValidatorIssueView {
  issue_id?: string;
  title: string;
  explanation?: string;
  fix_hint?: string;
  severity?: string;
}

export interface AgentPatchOperationView {
  operation_id: number;
  object_ref: AgentReferenceView;
  field_path: string;
  field_label?: string;
  old_value: unknown;
  new_value: unknown;
  decision: "pending" | "accepted" | "rejected" | "undone";
}

export type AgentPatchSetStatus =
  | "pending"
  | "applied"
  | "stale"
  | "rejected"
  | "undone"
  | "validation_warning";

export interface AgentPatchSetView {
  patch_set_id: number;
  reason_summary: string;
  status: AgentPatchSetStatus;
  base_draft_revision: number;
  applied_to_revision: number | null;
  is_stale: boolean;
  operations: AgentPatchOperationView[];
  validator_issues: ValidatorIssueView[];
}

export interface AgentTaskView {
  task_run_id: number;
  stage?: string | null;
  status?: string | null;
}

export interface AgentMessageView {
  message_id: number;
  role: "user" | "assistant" | "system";
  status: string;
  content: string;
  created_at?: string | null;
  task: AgentTaskView | null;
  references: AgentReferenceView[];
  patch_set?: AgentPatchSetView | null;
}

export interface AgentThreadListResponse {
  threads: AgentThreadView[];
}

export interface AgentMessageListResponse {
  messages: AgentMessageView[];
}

export interface SendAgentMessageResponse {
  thread: AgentThreadView;
  user_message: AgentMessageView;
  assistant_message: AgentMessageView;
  task: AgentTaskView;
}

export interface AgentPatchMutationResponse extends AgentPatchSetView {
  draft_revision: number;
}

export function threadIsFavorite(thread: AgentThreadView) {
  return thread.is_pinned;
}

export function threadIsArchived(thread: AgentThreadView) {
  return thread.status === "archived";
}

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" ? (value as JsonRecord) : {};
}

function optionalString(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

function nullableString(value: unknown) {
  return typeof value === "string" ? value : null;
}

function normalizeThread(value: unknown): AgentThreadView {
  const source = record(value);
  return {
    thread_id: Number(source.thread_id),
    title: optionalString(source.title) ?? "新对话",
    is_pinned: source.is_pinned === true,
    status: source.status === "archived" ? "archived" : "active",
    last_message_at: nullableString(source.last_message_at),
    updated_at: nullableString(source.updated_at),
  };
}

function normalizeReference(
  value: unknown,
): AgentReferenceView | null {
  if (typeof value === "string") {
    return { object_id: value };
  }
  const source = record(value);
  const objectId = optionalString(source.object_id);
  if (!objectId) return null;
  return {
    object_id: objectId,
    object_type: optionalString(source.object_type),
  };
}

function normalizeValidatorIssue(
  value: unknown,
  index: number,
): ValidatorIssueView {
  const source = record(value);
  return {
    issue_id:
      optionalString(source.issue_id) ??
      optionalString(source.rule_id) ??
      `validator-${index}`,
    title:
      optionalString(source.title) ??
      optionalString(source.message) ??
      "卷宗仍有一处需要确认",
    explanation:
      optionalString(source.explanation) ??
      optionalString(source.message),
    fix_hint: optionalString(source.fix_hint),
    severity: optionalString(source.severity),
  };
}

function normalizePatchOperation(
  value: unknown,
  index: number,
): AgentPatchOperationView {
  const source = record(value);
  const objectRef =
    normalizeReference(source.object_ref) ??
    normalizeReference({
      object_id: source.object_id,
      object_type: source.object_type,
    }) ??
    {};
  const decision = optionalString(source.decision);
  return {
    operation_id: Number(source.operation_id ?? index),
    object_ref: objectRef,
    field_path: optionalString(source.field_path) ?? "",
    field_label: optionalString(source.field_label),
    old_value: source.old_value,
    new_value: source.new_value,
    decision:
      decision === "accepted" ||
      decision === "rejected" ||
      decision === "undone"
        ? decision
        : "pending",
  };
}

function normalizePatchSet(value: unknown): AgentPatchSetView {
  const source = record(value);
  const status = optionalString(source.status);
  const operations = Array.isArray(source.operations)
    ? source.operations.map(normalizePatchOperation)
    : [];
  const validatorIssues = Array.isArray(source.validator_issues)
    ? source.validator_issues.map(normalizeValidatorIssue)
    : [];
  return {
    patch_set_id: Number(source.patch_set_id),
    reason_summary:
      optionalString(source.reason_summary) ??
      optionalString(source.summary) ??
      "Agent 建议更新卷宗",
    status:
      status === "applied" ||
      status === "stale" ||
      status === "rejected" ||
      status === "undone" ||
      status === "validation_warning"
        ? status
        : "pending",
    base_draft_revision: Number(
      source.base_draft_revision ?? source.base_revision ?? 0,
    ),
    applied_to_revision:
      source.applied_to_revision !== null &&
      source.applied_to_revision !== undefined &&
      Number.isInteger(Number(source.applied_to_revision))
        ? Number(source.applied_to_revision)
        : null,
    is_stale: source.is_stale === true || status === "stale",
    operations,
    validator_issues: validatorIssues,
  };
}

function normalizeTask(value: unknown): AgentTaskView | null {
  if (!value) return null;
  const source = record(value);
  const taskRunId = Number(source.task_run_id);
  if (!Number.isInteger(taskRunId)) return null;
  return {
    task_run_id: taskRunId,
    stage: nullableString(source.stage),
    status: nullableString(source.status),
  };
}

function normalizeMessage(value: unknown): AgentMessageView {
  const source = record(value);
  const role = optionalString(source.role);
  const referenceValues = Array.isArray(source.referenced_object_ids)
    ? source.referenced_object_ids
    : Array.isArray(source.references)
      ? source.references
      : [];
  return {
    message_id: Number(source.message_id),
    role:
      role === "user" || role === "system" ? role : "assistant",
    status: optionalString(source.status) ?? "completed",
    content: optionalString(source.content) ?? "",
    created_at: nullableString(source.created_at),
    task: normalizeTask(source.task),
    references: referenceValues
      .map(normalizeReference)
      .filter((item): item is AgentReferenceView => item !== null),
    patch_set: source.patch_set
      ? normalizePatchSet(source.patch_set)
      : null,
  };
}

export async function listAgentThreads(projectId: number, actorId: number) {
  const result = await apiRequest<AgentThreadListResponse | unknown[]>(
    `/projects/${projectId}/agent/threads`,
    { actorId },
  );
  const threads = Array.isArray(result) ? result : result.threads;
  return threads.map(normalizeThread);
}

export async function createAgentThread(projectId: number, actorId: number) {
  const result = await apiRequest<unknown>(
    `/projects/${projectId}/agent/threads`,
    {
      actorId,
      body: {},
      method: "POST",
    },
  );
  return normalizeThread(result);
}

export function patchAgentThread(
  projectId: number,
  actorId: number,
  threadId: number,
  changes: {
    title?: string;
    is_pinned?: boolean;
    archived?: boolean;
  },
) {
  return apiRequest<unknown>(
    `/projects/${projectId}/agent/threads/${threadId}`,
    {
      actorId,
      body: changes,
      method: "PATCH",
    },
  ).then(normalizeThread);
}

export async function listAgentMessages(
  projectId: number,
  actorId: number,
  threadId: number,
) {
  const result = await apiRequest<AgentMessageListResponse | unknown[]>(
    `/projects/${projectId}/agent/threads/${threadId}/messages`,
    {
      actorId,
    },
  );
  const messages = Array.isArray(result) ? result : result.messages;
  return messages.map(normalizeMessage);
}

export async function sendAgentMessage(
  projectId: number,
  actorId: number,
  threadId: number,
  content: string,
  provider: ProviderName,
) {
  const result = await apiRequest<JsonRecord>(
    `/projects/${projectId}/agent/threads/${threadId}/messages`,
    {
      actorId,
      body: { content, provider },
      method: "POST",
    },
  );
  const task = normalizeTask(result.task);
  if (!task) throw new Error("Agent 任务响应缺少任务编号。");
  return {
    thread: normalizeThread(result.thread),
    user_message: normalizeMessage(result.user_message),
    assistant_message: normalizeMessage(result.assistant_message),
    task,
  } satisfies SendAgentMessageResponse;
}

export async function applyAgentPatchSet(
  projectId: number,
  actorId: number,
  patchSetId: number,
  operationIds: number[] | null,
  expectedRevision: number,
) {
  const result = await apiRequest<JsonRecord>(
    `/projects/${projectId}/agent/patch-sets/${patchSetId}/apply`,
    {
      actorId,
      body: {
        operation_ids: operationIds,
        expected_revision: expectedRevision,
      },
      method: "POST",
    },
  );
  return {
    ...normalizePatchSet(result),
    draft_revision: Number(result.draft_revision),
  } satisfies AgentPatchMutationResponse;
}

export async function undoAgentPatchSet(
  projectId: number,
  actorId: number,
  patchSetId: number,
  expectedRevision: number,
) {
  const result = await apiRequest<JsonRecord>(
    `/projects/${projectId}/agent/patch-sets/${patchSetId}/undo`,
    {
      actorId,
      body: { expected_revision: expectedRevision },
      method: "POST",
    },
  );
  return {
    ...normalizePatchSet(result),
    draft_revision: Number(result.draft_revision),
  } satisfies AgentPatchMutationResponse;
}

export const terminalAgentEventTypes = new Set([
  "task.succeeded",
  "task.failed",
  "task.cancelled",
]);

export function executionStageLabel(event: TaskEventView) {
  const stageLabels: Record<string, string> = {
    queued: "任务已排队",
    loading_casefile: "正在读取完整卷宗",
    analyzing: "正在梳理事实与关系",
    generating: "正在组织答复",
    proposing: "正在生成修改建议",
    validating: "正在等待校验结果",
    completed: "处理完成",
  };
  return stageLabels[event.stage] ?? "正在处理卷宗";
}
