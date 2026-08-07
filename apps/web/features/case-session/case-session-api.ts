"use client";

import {
  ApiError,
  apiRequest,
  type BriefContent,
  type BriefIntakeCandidateContent,
  type BriefIntakeView,
  type BriefVersionView,
  type CandidateStrategy,
  type BriefView,
  type DraftCandidateView,
  type DraftView,
  type PolishMode,
  type ProjectView,
  type ProviderName,
  type ProviderSettingView,
  type TaskView,
} from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";

/** 正式双页开发环境共用的本地单用户身份。 */

const PROVIDER_ORDER: ProviderName[] = ["openai", "deepseek"];
const TERMINAL_TASK_STATUSES = new Set<TaskView["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);
const POLL_INTERVAL_MS = 800;
const MAX_POLLS = 600;

export class CaseSessionError extends Error {
  constructor(
    message: string,
    readonly failureCode?: string | null,
  ) {
    super(message);
  }
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

/** 已配置（非删除墓碑）的模型服务列表，按产品默认顺序排列。 */
export async function listConfiguredProviders(): Promise<ProviderName[]> {
  const configured: ProviderName[] = [];
  for (const provider of PROVIDER_ORDER) {
    const setting = await apiRequest<ProviderSettingView | null>(
      `/settings/provider?provider=${provider}`,
      { actorId: LOCAL_ACTOR_ID },
    );
    if (setting && setting.credential_status !== "deleted") {
      configured.push(provider);
    }
  }
  return configured;
}

/** 判断任务失败是否为模型服务认证失败（401）。 */
export function isProviderAuthFailure(error: unknown): boolean {
  return (
    error instanceof CaseSessionError &&
    error.failureCode === "provider_authentication_failed"
  );
}

/** 判断写入是否因 Brief Intake 乐观并发版本过期而被拒绝。 */
export function isBriefIntakeRevisionConflict(error: unknown): boolean {
  return (
    (error instanceof ApiError &&
      error.body.code === "brief_intake_revision_conflict") ||
    (error instanceof CaseSessionError &&
      error.failureCode === "brief_intake_revision_conflict")
  );
}

/**
 * 依次用已配置的 Provider 执行同一操作；仅在认证失败时回退到下一个
 * Provider，其他错误直接抛出。返回实际生效的 Provider 与结果。
 */
export async function runTaskWithProviderFallback<T>(
  operation: (provider: ProviderName) => Promise<T>,
): Promise<{ provider: ProviderName; result: T }> {
  const providers = await listConfiguredProviders();
  if (providers.length === 0) {
    throw new CaseSessionError("请先在左上角设置入口配置模型服务。");
  }
  let lastError: unknown = null;
  for (const provider of providers) {
    try {
      return { provider, result: await operation(provider) };
    } catch (error) {
      lastError = error;
      if (!isProviderAuthFailure(error)) throw error;
    }
  }
  throw lastError;
}

function projectTitleFrom(sourceText: string) {
  return (
    sourceText
      .split(/\r?\n/u)
      .map((line) => line.trim())
      .find(Boolean)
      ?.slice(0, 120) ?? "未命名推理卷宗"
  );
}

export async function createCaseProject(sourceText: string) {
  return apiRequest<ProjectView>("/projects", {
    actorId: LOCAL_ACTOR_ID,
    method: "POST",
    body: {
      title: projectTitleFrom(sourceText),
      description: null,
      profile: {},
    },
  });
}

export async function fetchCaseIntake(projectId: number) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake`,
    { actorId: LOCAL_ACTOR_ID },
  );
}

export async function persistCaseSource(
  projectId: number,
  intakeRevision: number,
  contentText: string,
  parentSourceRecordId: number | null = null,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/source`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "PUT",
      body: {
        expected_intake_revision: intakeRevision,
        content_text: contentText,
        parent_source_record_id: parentSourceRecordId,
      },
    },
  );
}

export async function startPolishTask(
  projectId: number,
  sourceRecordId: number,
  provider: ProviderName,
  polishMode: PolishMode,
) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/brief-polish`, {
    actorId: LOCAL_ACTOR_ID,
    method: "POST",
    body: {
      source_record_id: sourceRecordId,
      provider,
      polish_mode: polishMode,
    },
  });
}

export async function startQuestionsTask(
  projectId: number,
  intakeRevision: number,
  provider: ProviderName,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-intake-questions`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_intake_revision: intakeRevision,
        provider,
      },
    },
  );
}

export async function startSynthesizeTask(
  projectId: number,
  intakeRevision: number,
  provider: ProviderName,
  baseCandidateId: number | null = null,
  instruction: string | null = null,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-intake-synthesize`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_intake_revision: intakeRevision,
        provider,
        base_candidate_id: baseCandidateId,
        instruction,
      },
    },
  );
}

export async function startAnchorExtractTask(
  projectId: number,
  briefRevision: number,
  provider: ProviderName,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-anchor-extract`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_brief_revision: briefRevision,
        provider,
      },
    },
  );
}

export async function startDraftGenerationTask(
  projectId: number,
  briefVersionId: number,
  draftRevision: number,
  provider: ProviderName,
  candidateStrategy: CandidateStrategy = "balanced",
  candidateStrategyAttempt = 1,
) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/generate`, {
    actorId: LOCAL_ACTOR_ID,
    method: "POST",
    body: {
      brief_version_id: briefVersionId,
      expected_draft_revision: draftRevision,
      provider,
      candidate_strategy: candidateStrategy,
      candidate_strategy_attempt: candidateStrategyAttempt,
    },
  });
}

export async function fetchTask(projectId: number, taskRunId: number) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/${taskRunId}`, {
    actorId: LOCAL_ACTOR_ID,
  });
}

export async function waitForTask(
  projectId: number,
  taskRunId: number,
  onTick?: (task: TaskView) => void,
): Promise<TaskView> {
  for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
    const task = await fetchTask(projectId, taskRunId);
    onTick?.(task);
    if (TERMINAL_TASK_STATUSES.has(task.status)) {
      if (task.status !== "succeeded") {
        throw new CaseSessionError(
          task.failure?.message ?? `任务未完成：${task.status}`,
          task.failure?.code ?? task.error_code,
        );
      }
      return task;
    }
    await delay(POLL_INTERVAL_MS);
  }
  throw new CaseSessionError("任务执行超时，请稍后重试。");
}

export interface QuestionAnswerInput {
  mode: "answer" | "suggestion" | "pending";
  text?: string;
  suggestionIndex?: number;
}

export async function answerQuestion(
  projectId: number,
  intakeRevision: number,
  questionKey: string,
  answer: QuestionAnswerInput,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/questions/${questionKey}`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "PATCH",
      body: {
        expected_intake_revision: intakeRevision,
        answer_mode: answer.mode,
        answer_text: answer.mode === "answer" ? answer.text : null,
        suggestion_index:
          answer.mode === "suggestion" ? answer.suggestionIndex : null,
      },
    },
  );
}

export async function createBriefCandidate(
  projectId: number,
  intakeRevision: number,
  content: BriefIntakeCandidateContent,
  parentCandidateId: number | null = null,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/candidates`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_intake_revision: intakeRevision,
        parent_candidate_id: parentCandidateId,
        content,
        activate: true,
      },
    },
  );
}

export async function saveBriefCandidate(
  projectId: number,
  intakeRevision: number,
  candidateId: number,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/candidates/${candidateId}/save`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: { expected_intake_revision: intakeRevision },
    },
  );
}

export async function activateBriefCandidate(
  projectId: number,
  intakeRevision: number,
  candidateId: number,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/candidates/${candidateId}/activate`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: { expected_intake_revision: intakeRevision },
    },
  );
}

export async function adoptBriefCandidate(
  projectId: number,
  intakeRevision: number,
  candidateId: number,
  briefRevision: number,
) {
  return apiRequest<{
    intake: BriefIntakeView;
    brief: BriefView;
  }>(`/projects/${projectId}/brief-intake/candidates/${candidateId}/adopt`, {
    actorId: LOCAL_ACTOR_ID,
    method: "POST",
    body: {
      expected_intake_revision: intakeRevision,
      expected_brief_revision: briefRevision,
    },
  });
}

export async function fetchBrief(projectId: number) {
  return apiRequest<BriefView>(`/projects/${projectId}/brief`, {
    actorId: LOCAL_ACTOR_ID,
  });
}

export async function updateBrief(
  projectId: number,
  expectedRevision: number,
  content: BriefContent,
) {
  return apiRequest<BriefView>(`/projects/${projectId}/brief`, {
    actorId: LOCAL_ACTOR_ID,
    method: "PUT",
    body: { expected_revision: expectedRevision, content },
  });
}

export async function confirmBrief(
  projectId: number,
  expectedRevision: number,
) {
  return apiRequest<BriefVersionView>(`/projects/${projectId}/brief/confirm`, {
    actorId: LOCAL_ACTOR_ID,
    method: "POST",
    body: { expected_revision: expectedRevision },
  });
}

export async function fetchDraftCandidates(projectId: number) {
  return apiRequest<DraftCandidateView[]>(
    `/projects/${projectId}/draft-candidates`,
    { actorId: LOCAL_ACTOR_ID },
  );
}

/** 读取 CaseFile 当前工作稿，供需要 Draft revision 的写入门禁使用。 */
export async function fetchCaseDraft(projectId: number) {
  return apiRequest<DraftView>(`/projects/${projectId}/draft`, {
    actorId: LOCAL_ACTOR_ID,
  });
}

/** 以 Draft revision 为门禁更新一个 CaseFile 对象；调用方成功后应重取完整 Draft。 */
export async function patchCaseDraftObject(
  projectId: number,
  objectId: string,
  expectedRevision: number,
  changes: Record<string, unknown>,
) {
  return apiRequest<Record<string, unknown>>(
    `/projects/${projectId}/draft/objects/${encodeURIComponent(objectId)}`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "PATCH",
      body: { expected_revision: expectedRevision, changes },
    },
  );
}

export async function adoptDraftCandidate(
  projectId: number,
  taskRunId: number,
  draftRevision: number,
) {
  return apiRequest<{ task_run_id: number; adopted: true }>(
    `/projects/${projectId}/draft-candidates/${taskRunId}/adopt`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: { expected_draft_revision: draftRevision },
    },
  );
}
