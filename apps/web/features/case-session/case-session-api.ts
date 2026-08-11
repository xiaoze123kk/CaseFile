"use client";

import {
  ApiError,
  apiRequest,
  streamTaskEvents,
  type BriefContent,
  type BriefIntakeCandidateContent,
  type BriefIntakeView,
  type BriefVersionView,
  type BriefStrategyOptionsResult,
  type CandidateStrategy,
  type BriefView,
  type DraftCandidateView,
  type DraftCandidatePreviewView,
  type DraftCandidateAdoption,
  type DraftView,
  type ExposurePlanEntryView,
  type ExposurePlanView,
  type AnchorExtractMode,
  type PolishMode,
  type ProjectView,
  type ProviderName,
  type ProviderSettingView,
  type TaskView,
  type TaskType,
  type TimelineTemporalPosition,
  type TimelineTimePreviewView,
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
const TASK_WAIT_TIMEOUT_MS = 600 * POLL_INTERVAL_MS;

export class CaseSessionError extends Error {
  constructor(
    message: string,
    readonly failureCode?: string | null,
  ) {
    super(message);
  }
}

export class TaskCancelledError extends CaseSessionError {
  constructor(readonly task: TaskView) {
    super(
      task.failure?.message ?? "任务已取消，Current Draft 未被修改。",
      task.failure?.code ?? task.error_code ?? "task_cancelled",
    );
    this.name = "TaskCancelledError";
  }
}

export function isTaskCancelledError(error: unknown): error is TaskCancelledError {
  return error instanceof TaskCancelledError;
}

function delay(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeoutId);
      reject(new DOMException("Task wait aborted", "AbortError"));
    };
    const timeoutId = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
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
  mode: AnchorExtractMode = "extract",
  content?: BriefContent,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-anchor-extract`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_brief_revision: briefRevision,
        provider,
        mode,
        ...(content ? { content } : {}),
      },
    },
  );
}

export async function startDraftGenerationTask(
  projectId: number,
  briefVersionId: number,
  draftId: number,
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
      expected_draft_id: draftId,
      expected_draft_revision: draftRevision,
      provider,
      candidate_strategy: candidateStrategy,
      candidate_strategy_attempt: candidateStrategyAttempt,
    },
  });
}

export async function fetchTask(
  projectId: number,
  taskRunId: number,
  signal?: AbortSignal,
) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/${taskRunId}`, {
    actorId: LOCAL_ACTOR_ID,
    signal,
  });
}

export async function cancelTask(projectId: number, taskRunId: number) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/${taskRunId}/cancel`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
    },
  );
}

export async function resumeDraftGenerationTask(
  projectId: number,
  taskRunId: number,
  draftId: number,
  draftRevision: number,
  briefRevision: number,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/${taskRunId}/resume`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_draft_id: draftId,
        expected_draft_revision: draftRevision,
        expected_brief_revision: briefRevision,
      },
    },
  );
}

export async function fetchLatestTask(projectId: number, taskType: TaskType) {
  return apiRequest<TaskView | null>(
    `/projects/${projectId}/tasks/latest?task_type=${encodeURIComponent(taskType)}`,
    { actorId: LOCAL_ACTOR_ID },
  );
}

export async function waitForTask(
  projectId: number,
  taskRunId: number,
  onTick?: (task: TaskView) => void,
  signal?: AbortSignal,
): Promise<TaskView> {
  const deadline = Date.now() + TASK_WAIT_TIMEOUT_MS;
  let lastEventId = 0;

  while (Date.now() < deadline) {
    if (signal?.aborted) {
      throw new CaseSessionError("已停止等待任务结果。", "task_wait_aborted");
    }
    let task: TaskView;
    try {
      task = await fetchTask(projectId, taskRunId, signal);
    } catch (error) {
      if (signal?.aborted) {
        throw new CaseSessionError("已停止等待任务结果。", "task_wait_aborted");
      }
      throw error;
    }
    onTick?.(task);
    if (TERMINAL_TASK_STATUSES.has(task.status)) {
      if (task.status === "cancelled") {
        throw new TaskCancelledError(task);
      }
      if (task.status !== "succeeded") {
        throw new CaseSessionError(
          task.failure?.message ?? `任务未完成：${task.status}`,
          task.failure?.code ?? task.error_code,
        );
      }
      return task;
    }

    const streamController = new AbortController();
    const abortStream = () => streamController.abort();
    signal?.addEventListener("abort", abortStream, { once: true });
    const remaining = Math.max(1, deadline - Date.now());
    const timeoutId = window.setTimeout(abortStream, remaining);
    try {
      await streamTaskEvents(
        `/projects/${projectId}/tasks/${taskRunId}/stream`,
        LOCAL_ACTOR_ID,
        (event) => {
          lastEventId = Math.max(lastEventId, event.sequence_no);
          const eventStatus: Partial<Record<string, TaskView["status"]>> = {
            "task.started": "running",
            "task.cancel_requested": "cancelling",
            "task.cancelled": "cancelled",
            "task.succeeded": "succeeded",
            "task.failed": "failed",
          };
          task = {
            ...task,
            status: eventStatus[event.event_type] ?? task.status,
            stage: event.stage,
          };
          onTick?.(task);
        },
        streamController.signal,
        lastEventId,
      );
    } catch (error) {
      if (signal?.aborted) {
        throw new CaseSessionError("已停止等待任务结果。", "task_wait_aborted");
      }
      if (Date.now() >= deadline) break;
      // A proxy or browser may interrupt SSE. The next loop performs one
      // authoritative poll, then reconnects with Last-Event-ID replay.
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        await delay(POLL_INTERVAL_MS, signal);
      }
    } finally {
      window.clearTimeout(timeoutId);
      signal?.removeEventListener("abort", abortStream);
    }
  }
  throw new CaseSessionError("任务执行超时，请稍后重试。", "task_wait_timeout");
}

export async function waitForRecoveredTask(
  projectId: number,
  taskRunId: number,
  onTick: (task: TaskView) => void,
) {
  try {
    return await waitForTask(projectId, taskRunId, onTick);
  } catch (error) {
    if (isTaskCancelledError(error)) return error.task;
    return fetchTask(projectId, taskRunId).catch(() => null);
  }
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

/** 只读加载一份已校验候选正文；不会采用候选或写入 Current Draft。 */
export async function fetchDraftCandidatePreview(
  projectId: number,
  taskRunId: number,
) {
  return apiRequest<DraftCandidatePreviewView>(
    `/projects/${projectId}/draft-candidates/${taskRunId}`,
    { actorId: LOCAL_ACTOR_ID },
  );
}

/** 读取 CaseFile 当前工作稿，供需要 Draft revision 的写入门禁使用。 */
export async function fetchCaseDraft(projectId: number) {
  return apiRequest<DraftView>(`/projects/${projectId}/draft`, {
    actorId: LOCAL_ACTOR_ID,
  });
}

export async function reconcileDraftCandidateAdoption(
  projectId: number,
  taskRunId: number,
) {
  const [candidates, draft] = await Promise.all([
    fetchDraftCandidates(projectId),
    fetchCaseDraft(projectId),
  ]);
  return {
    candidates,
    draft,
    targetIsCurrent: candidates.some(
      (candidate) => candidate.task_run_id === taskRunId && candidate.is_current,
    ),
  };
}

export async function startStrategyOptionsTask(
  projectId: number,
  briefVersionId: number,
  provider: ProviderName,
  refresh = false,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-strategy-options`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        brief_version_id: briefVersionId,
        provider,
        refresh,
      },
    },
  );
}

export function strategyOptionsResult(task: TaskView) {
  return task.result as BriefStrategyOptionsResult | null;
}

/** 以 Draft revision 为门禁更新一个 CaseFile 对象；调用方成功后应重取完整 Draft。 */
export async function patchCaseDraftObject(
  projectId: number,
  objectId: string,
  expectedDraftId: number,
  expectedRevision: number,
  changes: Record<string, unknown>,
) {
  return apiRequest<Record<string, unknown>>(
    `/projects/${projectId}/draft/objects/${encodeURIComponent(objectId)}`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "PATCH",
      body: {
        expected_draft_id: expectedDraftId,
        expected_revision: expectedRevision,
        changes,
      },
    },
  );
}

/** 只读预演事件时间变化；不会推进 Draft revision。 */
export async function previewCaseDraftEventTime(
  projectId: number,
  eventId: string,
  expectedDraftId: number,
  expectedRevision: number,
  proposedTime: TimelineTemporalPosition,
) {
  return apiRequest<TimelineTimePreviewView>(
    `/projects/${projectId}/draft/events/${encodeURIComponent(eventId)}/time-preview`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: {
        expected_draft_id: expectedDraftId,
        expected_revision: expectedRevision,
        proposed_time: proposedTime,
      },
    },
  );
}

/** 读取 Current Draft 独立版本化的单一线性披露计划。 */
export async function fetchExposurePlan(projectId: number) {
  return apiRequest<ExposurePlanView>(
    `/projects/${projectId}/draft/exposure-plan`,
    { actorId: LOCAL_ACTOR_ID },
  );
}

/** 以计划自身 revision 保存完整线性顺序；不推进 Draft revision。 */
export async function putExposurePlan(
  projectId: number,
  expectedDraftId: number,
  expectedRevision: number,
  entries: Omit<ExposurePlanEntryView, "sequence_no">[],
) {
  return apiRequest<ExposurePlanView>(
    `/projects/${projectId}/draft/exposure-plan`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "PUT",
      body: {
        expected_draft_id: expectedDraftId,
        expected_revision: expectedRevision,
        entries,
      },
    },
  );
}

export async function adoptDraftCandidate(
  projectId: number,
  taskRunId: number,
  expectedCurrentDraftId: number,
) {
  return apiRequest<DraftCandidateAdoption>(
    `/projects/${projectId}/draft-candidates/${taskRunId}/adopt`,
    {
      actorId: LOCAL_ACTOR_ID,
      method: "POST",
      body: { expected_current_draft_id: expectedCurrentDraftId },
    },
  );
}

export async function adoptDraftCandidateWithReconciliation(
  projectId: number,
  taskRunId: number,
  expectedCurrentDraftId: number,
) {
  try {
    const adoption = await adoptDraftCandidate(
      projectId,
      taskRunId,
      expectedCurrentDraftId,
    );
    return { adoption, facts: null, error: null };
  } catch (error) {
    let facts: Awaited<ReturnType<typeof reconcileDraftCandidateAdoption>>;
    try {
      facts = await reconcileDraftCandidateAdoption(projectId, taskRunId);
    } catch {
      throw new CaseSessionError(
        "采用结果暂时无法确认，请刷新候选列表后核对 Current Draft。",
        "draft_candidate_adoption_unconfirmed",
      );
    }
    return {
      adoption: null,
      facts,
      error: facts.targetIsCurrent ? null : error,
    };
  }
}
