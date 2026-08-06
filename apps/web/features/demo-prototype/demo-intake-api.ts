"use client";

import {
  apiRequest,
  type BriefContent,
  type BriefIntakeCandidateContent,
  type BriefIntakeView,
  type BriefVersionView,
  type BriefView,
  type DraftCandidateView,
  type PolishMode,
  type ProjectView,
  type ProviderName,
  type ProviderSettingView,
  type TaskView,
} from "@/lib/api-client";

/** demo 与创作模式共用的本地开发用户。 */
export const DEMO_ACTOR_ID = 1;

const PROVIDER_ORDER: ProviderName[] = ["openai", "deepseek"];
const TERMINAL_TASK_STATUSES = new Set<TaskView["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);
const POLL_INTERVAL_MS = 800;
const MAX_POLLS = 600;

export class DemoIntakeError extends Error {
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
      { actorId: DEMO_ACTOR_ID },
    );
    if (setting && setting.credential_status !== "deleted") {
      configured.push(provider);
    }
  }
  return configured;
}

/** 判断任务失败是否为模型服务认证失败（401）。 */
export function isDemoAuthFailure(error: unknown): boolean {
  return (
    error instanceof DemoIntakeError &&
    error.failureCode === "provider_authentication_failed"
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
    throw new DemoIntakeError("请先在左上角设置入口配置模型服务。");
  }
  let lastError: unknown = null;
  for (const provider of providers) {
    try {
      return { provider, result: await operation(provider) };
    } catch (error) {
      lastError = error;
      if (!isDemoAuthFailure(error)) throw error;
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

export async function createDemoProject(sourceText: string) {
  return apiRequest<ProjectView>("/projects", {
    actorId: DEMO_ACTOR_ID,
    method: "POST",
    body: {
      title: projectTitleFrom(sourceText),
      description: null,
      profile: {},
    },
  });
}

export async function fetchDemoIntake(projectId: number) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake`,
    { actorId: DEMO_ACTOR_ID },
  );
}

export async function persistDemoSource(
  projectId: number,
  intakeRevision: number,
  contentText: string,
  parentSourceRecordId: number | null = null,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/source`,
    {
      actorId: DEMO_ACTOR_ID,
      method: "PUT",
      body: {
        expected_intake_revision: intakeRevision,
        content_text: contentText,
        parent_source_record_id: parentSourceRecordId,
      },
    },
  );
}

export async function startDemoPolish(
  projectId: number,
  sourceRecordId: number,
  provider: ProviderName,
  polishMode: PolishMode,
) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/brief-polish`, {
    actorId: DEMO_ACTOR_ID,
    method: "POST",
    body: {
      source_record_id: sourceRecordId,
      provider,
      polish_mode: polishMode,
    },
  });
}

export async function startDemoQuestions(
  projectId: number,
  intakeRevision: number,
  provider: ProviderName,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-intake-questions`,
    {
      actorId: DEMO_ACTOR_ID,
      method: "POST",
      body: {
        expected_intake_revision: intakeRevision,
        provider,
      },
    },
  );
}

export async function startDemoSynthesize(
  projectId: number,
  intakeRevision: number,
  provider: ProviderName,
  baseCandidateId: number | null = null,
  instruction: string | null = null,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-intake-synthesize`,
    {
      actorId: DEMO_ACTOR_ID,
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

export async function startDemoAnchorExtract(
  projectId: number,
  briefRevision: number,
  provider: ProviderName,
) {
  return apiRequest<TaskView>(
    `/projects/${projectId}/tasks/brief-anchor-extract`,
    {
      actorId: DEMO_ACTOR_ID,
      method: "POST",
      body: {
        expected_brief_revision: briefRevision,
        provider,
      },
    },
  );
}

export async function startDemoDraftRun(
  projectId: number,
  briefVersionId: number,
  draftRevision: number,
  provider: ProviderName,
) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/generate`, {
    actorId: DEMO_ACTOR_ID,
    method: "POST",
    body: {
      brief_version_id: briefVersionId,
      expected_draft_revision: draftRevision,
      provider,
    },
  });
}

export async function fetchDemoTask(projectId: number, taskRunId: number) {
  return apiRequest<TaskView>(`/projects/${projectId}/tasks/${taskRunId}`, {
    actorId: DEMO_ACTOR_ID,
  });
}

export async function waitForDemoTask(
  projectId: number,
  taskRunId: number,
  onTick?: (task: TaskView) => void,
): Promise<TaskView> {
  for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
    const task = await fetchDemoTask(projectId, taskRunId);
    onTick?.(task);
    if (TERMINAL_TASK_STATUSES.has(task.status)) {
      if (task.status !== "succeeded") {
        throw new DemoIntakeError(
          task.failure?.message ?? `任务未完成：${task.status}`,
          task.failure?.code ?? task.error_code,
        );
      }
      return task;
    }
    await delay(POLL_INTERVAL_MS);
  }
  throw new DemoIntakeError("任务执行超时，请稍后重试。");
}

export interface DemoQuestionAnswerInput {
  mode: "answer" | "suggestion" | "pending";
  text?: string;
  suggestionIndex?: number;
}

export async function answerDemoQuestion(
  projectId: number,
  intakeRevision: number,
  questionKey: string,
  answer: DemoQuestionAnswerInput,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/questions/${questionKey}`,
    {
      actorId: DEMO_ACTOR_ID,
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

export async function createDemoCandidate(
  projectId: number,
  intakeRevision: number,
  content: BriefIntakeCandidateContent,
  parentCandidateId: number | null = null,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/candidates`,
    {
      actorId: DEMO_ACTOR_ID,
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

export async function saveDemoCandidate(
  projectId: number,
  intakeRevision: number,
  candidateId: number,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/candidates/${candidateId}/save`,
    {
      actorId: DEMO_ACTOR_ID,
      method: "POST",
      body: { expected_intake_revision: intakeRevision },
    },
  );
}

export async function activateDemoCandidate(
  projectId: number,
  intakeRevision: number,
  candidateId: number,
) {
  return apiRequest<BriefIntakeView>(
    `/projects/${projectId}/brief-intake/candidates/${candidateId}/activate`,
    {
      actorId: DEMO_ACTOR_ID,
      method: "POST",
      body: { expected_intake_revision: intakeRevision },
    },
  );
}

export async function adoptDemoCandidate(
  projectId: number,
  intakeRevision: number,
  candidateId: number,
  briefRevision: number,
) {
  return apiRequest<{
    intake: BriefIntakeView;
    brief: BriefView;
  }>(`/projects/${projectId}/brief-intake/candidates/${candidateId}/adopt`, {
    actorId: DEMO_ACTOR_ID,
    method: "POST",
    body: {
      expected_intake_revision: intakeRevision,
      expected_brief_revision: briefRevision,
    },
  });
}

export async function fetchDemoBrief(projectId: number) {
  return apiRequest<BriefView>(`/projects/${projectId}/brief`, {
    actorId: DEMO_ACTOR_ID,
  });
}

export async function updateDemoBrief(
  projectId: number,
  expectedRevision: number,
  content: BriefContent,
) {
  return apiRequest<BriefView>(`/projects/${projectId}/brief`, {
    actorId: DEMO_ACTOR_ID,
    method: "PUT",
    body: { expected_revision: expectedRevision, content },
  });
}

export async function confirmDemoBrief(
  projectId: number,
  expectedRevision: number,
) {
  return apiRequest<BriefVersionView>(`/projects/${projectId}/brief/confirm`, {
    actorId: DEMO_ACTOR_ID,
    method: "POST",
    body: { expected_revision: expectedRevision },
  });
}

export async function fetchDemoDraftCandidates(projectId: number) {
  return apiRequest<DraftCandidateView[]>(
    `/projects/${projectId}/draft-candidates`,
    { actorId: DEMO_ACTOR_ID },
  );
}

export async function adoptDemoDraftCandidate(
  projectId: number,
  taskRunId: number,
  draftRevision: number,
) {
  return apiRequest<{ task_run_id: number; adopted: true }>(
    `/projects/${projectId}/draft-candidates/${taskRunId}/adopt`,
    {
      actorId: DEMO_ACTOR_ID,
      method: "POST",
      body: { expected_draft_revision: draftRevision },
    },
  );
}
