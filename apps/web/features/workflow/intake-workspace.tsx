"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { DocumentHeader } from "@/components/archive-ui";
import {
  apiRequest,
  errorMessage,
  type BriefIntakeAdoptionView,
  type BriefIntakeCandidateContent,
  type BriefIntakeQuestionView,
  type BriefIntakeStage,
  type BriefIntakeView,
  type BriefPolishResult,
  type BriefView,
  type ProjectView,
  type ProviderSettingView,
  type TaskView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import { IntakeConfirmationStep } from "./intake-confirmation-step";
import { IntakeIdeaStep } from "./intake-idea-step";
import { seedManualCandidate } from "./intake-model";
import { IntakeQuestionsStep } from "./intake-questions-step";
import styles from "./brief-intake-workspace.module.css";
import { useRecoverableTask, useTaskEventStream } from "./task-recovery";

type IntakeStep = "idea" | "questions" | "confirmation";

const terminalTaskStatuses = new Set<TaskView["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);

const stageToStep: Record<BriefIntakeStage, IntakeStep> = {
  idea: "idea",
  questions: "questions",
  confirmation: "confirmation",
  brief_review: "confirmation",
};

const stepMeta: Record<IntakeStep, { index: string; label: string }> = {
  idea: { index: "1 / 3", label: "最初想法" },
  questions: { index: "2 / 3", label: "关键追问" },
  confirmation: { index: "3 / 3", label: "创作简报确认" },
};

const intakePaths = [
  {
    code: "A",
    label: "我有一个想法",
    summary: "已有灵感，整理成简报",
    inputHint: "输入一句描述",
    active: true,
  },
  {
    code: "B",
    label: "帮我想一个",
    summary: "没有方向，生成多个创意",
    inputHint: "输入偏好限制",
    active: false,
  },
  {
    code: "C",
    label: "我有已有内容",
    summary: "有现成素材，解析成简报",
    inputHint: "上传或粘贴",
    active: false,
  },
  {
    code: "D",
    label: "我已经准备好",
    summary: "方向明确，进入工作台",
    inputHint: "选模板/空白",
    active: false,
  },
] as const;

function projectTitleFrom(sourceText: string) {
  return (
    sourceText
      .split(/\r?\n/u)
      .map((line) => line.trim())
      .find(Boolean)
      ?.slice(0, 120) ?? "未命名推理卷宗"
  );
}

function isPolishResult(
  task: TaskView | null,
): task is TaskView & { result: BriefPolishResult } {
  return Boolean(
    task?.task_type === "brief_polish" &&
      task.status === "succeeded" &&
      task.result &&
      "polished_text" in task.result,
  );
}

function taskIsActive(task: TaskView | null) {
  return Boolean(task && !terminalTaskStatuses.has(task.status));
}

function openSettings() {
  window.dispatchEvent(new Event("casefile:open-settings"));
}

export function IntakeWorkspace() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [activeStep, setActiveStep] = useState<IntakeStep>("idea");
  const [sourceText, setSourceText] = useState("");
  const [polishDraft, setPolishDraft] = useState("");
  const [polishReviewOpen, setPolishReviewOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const hydratedSourceRef = useRef<string | null>(null);
  const hydratedStageRevisionRef = useRef<string | null>(null);
  const hydratedPolishReviewTaskRef = useRef<number | null>(null);
  const observedTerminalTasksRef = useRef(new Set<number>());

  const intakeQueryKey = useMemo(
    () => ["brief-intake", workflow.actorId, workflow.projectId] as const,
    [workflow.actorId, workflow.projectId],
  );
  const intakeQuery = useQuery({
    queryKey: intakeQueryKey,
    queryFn: () =>
      apiRequest<BriefIntakeView>(
        `/projects/${workflow.projectId}/brief-intake`,
        { actorId: workflow.actorId },
      ),
    enabled: workflow.ready && workflow.projectId !== null,
  });
  const providerQuery = useQuery({
    queryKey: ["provider-setting", workflow.actorId, workflow.provider],
    queryFn: () =>
      apiRequest<ProviderSettingView | null>(
        `/settings/provider?provider=${workflow.provider}`,
        { actorId: workflow.actorId },
      ),
    enabled: workflow.ready,
  });

  const polishRecovery = useRecoverableTask(
    workflow.projectId,
    workflow.actorId,
    "brief_polish",
    workflow.taskRunIds.brief_polish,
    workflow.ready,
  );
  const questionsRecovery = useRecoverableTask(
    workflow.projectId,
    workflow.actorId,
    "brief_intake_questions",
    workflow.taskRunIds.brief_intake_questions,
    workflow.ready,
  );
  const synthesizeRecovery = useRecoverableTask(
    workflow.projectId,
    workflow.actorId,
    "brief_intake_synthesize",
    workflow.taskRunIds.brief_intake_synthesize,
    workflow.ready,
  );
  const polishTask = polishRecovery.task;
  const questionsTask = questionsRecovery.task;
  const synthesizeTask = synthesizeRecovery.task;
  const polishStream = useTaskEventStream(
    workflow.projectId,
    workflow.actorId,
    taskIsActive(polishTask) ? polishTask?.task_run_id ?? null : null,
  );
  const questionsStream = useTaskEventStream(
    workflow.projectId,
    workflow.actorId,
    taskIsActive(questionsTask) ? questionsTask?.task_run_id ?? null : null,
  );
  const synthesizeStream = useTaskEventStream(
    workflow.projectId,
    workflow.actorId,
    taskIsActive(synthesizeTask) ? synthesizeTask?.task_run_id ?? null : null,
  );

  useEffect(() => {
    const source = intakeQuery.data?.current_source;
    if (!source) return;
    const hydrationKey = `${source.source_record_id}:${source.content_hash}`;
    if (hydratedSourceRef.current === hydrationKey) return;
    hydratedSourceRef.current = hydrationKey;
    const timeoutId = window.setTimeout(() => setSourceText(source.content_text));
    return () => window.clearTimeout(timeoutId);
  }, [intakeQuery.data?.current_source]);

  useEffect(() => {
    const intake = intakeQuery.data;
    if (!intake) return;
    const stage = intake.stage;
    const hydrationKey = `${intake.project_id}:${intake.revision}`;
    if (hydratedStageRevisionRef.current === hydrationKey) return;
    hydratedStageRevisionRef.current = hydrationKey;
    const timeoutId = window.setTimeout(() => setActiveStep(stageToStep[stage]));
    return () => window.clearTimeout(timeoutId);
  }, [intakeQuery.data]);

  useEffect(() => {
    const tasks = [polishTask, questionsTask, synthesizeTask].filter(
      (task): task is TaskView => Boolean(task),
    );
    for (const task of tasks) {
      if (
        !terminalTaskStatuses.has(task.status) ||
        observedTerminalTasksRef.current.has(task.task_run_id)
      ) {
        continue;
      }
      observedTerminalTasksRef.current.add(task.task_run_id);
      if (task.task_type === "brief_intake_questions") {
        void queryClient.invalidateQueries({ queryKey: intakeQueryKey });
      }
      if (task.task_type === "brief_intake_synthesize") {
        void queryClient.invalidateQueries({ queryKey: intakeQueryKey });
      }
    }
  }, [intakeQueryKey, polishTask, queryClient, questionsTask, synthesizeTask]);

  const polishResult = isPolishResult(polishTask) ? polishTask.result : null;
  const polishCandidateStale = Boolean(
    polishResult &&
      polishTask &&
      (intakeQuery.data?.current_source?.source_record_id !==
        polishTask.input_source_record_id ||
        intakeQuery.data?.current_source?.content_text !== sourceText.trim()),
  );

  useEffect(() => {
    if (!polishReviewOpen || !polishResult || !polishTask) return;
    if (hydratedPolishReviewTaskRef.current === polishTask.task_run_id) return;
    hydratedPolishReviewTaskRef.current = polishTask.task_run_id;
    const timeoutId = window.setTimeout(() =>
      setPolishDraft(polishResult.polished_text),
    );
    return () => window.clearTimeout(timeoutId);
  }, [polishResult, polishReviewOpen, polishTask]);

  async function persistIntake(view: BriefIntakeView) {
    queryClient.setQueryData(
      ["brief-intake", workflow.actorId, view.project_id],
      view,
    );
    return view;
  }

  async function ensureCurrentSource() {
    const normalized = sourceText.trim();
    if (!normalized) throw new Error("请先写下最初想法。");
    let projectId = workflow.projectId;
    let intake = intakeQuery.data ?? null;
    if (projectId === null) {
      const project = await apiRequest<ProjectView>("/projects", {
        actorId: workflow.actorId,
        method: "POST",
        body: {
          title: projectTitleFrom(normalized),
          description: null,
          profile: {},
        },
      });
      projectId = project.id;
      workflow.setProject(project.id);
      intake = await apiRequest<BriefIntakeView>(
        `/projects/${project.id}/brief-intake`,
        { actorId: workflow.actorId },
      );
      queryClient.setQueryData(
        ["brief-intake", workflow.actorId, project.id],
        intake,
      );
      await queryClient.invalidateQueries({ queryKey: ["projects", workflow.actorId] });
    }
    if (!intake || intake.project_id !== projectId) {
      intake = await apiRequest<BriefIntakeView>(
        `/projects/${projectId}/brief-intake`,
        { actorId: workflow.actorId },
      );
    }
    if (intake.current_source?.content_text === normalized) {
      return { projectId, intake };
    }
    const updated = await apiRequest<BriefIntakeView>(
      `/projects/${projectId}/brief-intake/source`,
      {
        actorId: workflow.actorId,
        method: "PUT",
        body: {
          expected_intake_revision: intake.revision,
          content_text: normalized,
        },
      },
    );
    await persistIntake(updated);
    return { projectId, intake: updated };
  }

  const continueMutation = useMutation({
    mutationFn: async () => {
      setActionError(null);
      const current = await ensureCurrentSource();
      if (!providerQuery.data) return { task: null, ...current };
      const task = await apiRequest<TaskView>(
        `/projects/${current.projectId}/tasks/brief-intake-questions`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            expected_intake_revision: current.intake.revision,
            provider: workflow.provider,
          },
        },
      );
      return { task, ...current };
    },
    onSuccess: async ({ projectId, task }) => {
      if (task) workflow.setTask("brief_intake_questions", task.task_run_id);
      setActiveStep("questions");
      await queryClient.invalidateQueries({
        queryKey: ["brief-intake", workflow.actorId, projectId],
      });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const polishMutation = useMutation({
    mutationFn: async () => {
      setActionError(null);
      const current = await ensureCurrentSource();
      if (!providerQuery.data) throw new Error("请先配置模型服务。");
      return apiRequest<TaskView>(
        `/projects/${current.projectId}/tasks/brief-polish`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            source_record_id: current.intake.current_source?.source_record_id,
            provider: workflow.provider,
          },
        },
      );
    },
    onSuccess: (task) => workflow.setTask("brief_polish", task.task_run_id),
    onError: (error) => setActionError(errorMessage(error)),
  });

  const adoptPolishMutation = useMutation({
    mutationFn: async () => {
      if (!workflow.projectId || !intakeQuery.data || !polishResult) {
        throw new Error("润色候选已经失去当前来源上下文。");
      }
      return apiRequest<BriefIntakeView>(
        `/projects/${workflow.projectId}/brief-intake/source`,
        {
          actorId: workflow.actorId,
          method: "PUT",
          body: {
            expected_intake_revision: intakeQuery.data.revision,
            content_text: polishDraft.trim(),
            parent_source_record_id:
              polishResult.proposal_source_record.source_record_id,
          },
        },
      );
    },
    onSuccess: async (view) => {
      await persistIntake(view);
      setSourceText(view.current_source?.content_text ?? polishDraft.trim());
      setPolishReviewOpen(false);
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const answerMutation = useMutation({
    mutationFn: async ({
      question,
      answer,
    }: {
      question: BriefIntakeQuestionView;
      answer:
        | { mode: "answer"; text: string }
        | { mode: "suggestion"; suggestionIndex: number }
        | { mode: "pending" };
    }) => {
      if (!workflow.projectId || !intakeQuery.data) {
        throw new Error("追问状态尚未载入。");
      }
      const body =
        answer.mode === "answer"
          ? {
              expected_intake_revision: intakeQuery.data.revision,
              answer_mode: "answer",
              answer_text: answer.text,
            }
          : answer.mode === "suggestion"
            ? {
                expected_intake_revision: intakeQuery.data.revision,
                answer_mode: "suggestion",
                suggestion_index: answer.suggestionIndex,
              }
            : {
                expected_intake_revision: intakeQuery.data.revision,
                answer_mode: "pending",
              };
      return apiRequest<BriefIntakeView>(
        `/projects/${workflow.projectId}/brief-intake/questions/${question.question_key}`,
        { actorId: workflow.actorId, method: "PATCH", body },
      );
    },
    onSuccess: persistIntake,
    onError: (error) => setActionError(errorMessage(error)),
  });

  const synthesisMutation = useMutation({
    mutationFn: async ({
      baseCandidateId = null,
      instruction = null,
    }: {
      baseCandidateId?: number | null;
      instruction?: string | null;
    }) => {
      if (!workflow.projectId || !intakeQuery.data) {
        throw new Error("Intake 状态尚未载入。");
      }
      if (!providerQuery.data) throw new Error("请先配置模型服务。");
      return apiRequest<TaskView>(
        `/projects/${workflow.projectId}/tasks/brief-intake-synthesize`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            expected_intake_revision: intakeQuery.data.revision,
            provider: workflow.provider,
            base_candidate_id: baseCandidateId,
            instruction,
          },
        },
      );
    },
    onSuccess: async (task) => {
      workflow.setTask("brief_intake_synthesize", task.task_run_id);
      setActiveStep("confirmation");
      await queryClient.invalidateQueries({ queryKey: intakeQueryKey });
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const candidateMutation = useMutation({
    mutationFn: async ({
      content,
      parentCandidateId,
    }: {
      content: BriefIntakeCandidateContent;
      parentCandidateId: number | null;
    }) => {
      if (!workflow.projectId || !intakeQuery.data) {
        throw new Error("Intake 状态尚未载入。");
      }
      return apiRequest<BriefIntakeView>(
        `/projects/${workflow.projectId}/brief-intake/candidates`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            expected_intake_revision: intakeQuery.data.revision,
            parent_candidate_id: parentCandidateId,
            content,
            activate: true,
          },
        },
      );
    },
    onSuccess: persistIntake,
    onError: (error) => setActionError(errorMessage(error)),
  });

  const candidateActionMutation = useMutation({
    mutationFn: async ({
      candidateId,
      action,
    }: {
      candidateId: number;
      action: "save" | "activate";
    }) => {
      if (!workflow.projectId || !intakeQuery.data) {
        throw new Error("候选状态尚未载入。");
      }
      return apiRequest<BriefIntakeView>(
        `/projects/${workflow.projectId}/brief-intake/candidates/${candidateId}/${action}`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: { expected_intake_revision: intakeQuery.data.revision },
        },
      );
    },
    onSuccess: persistIntake,
    onError: (error) => setActionError(errorMessage(error)),
  });

  const adoptCandidateMutation = useMutation({
    mutationFn: async (candidateId: number) => {
      if (!workflow.projectId || !intakeQuery.data) {
        throw new Error("候选状态尚未载入。");
      }
      return apiRequest<BriefIntakeAdoptionView>(
        `/projects/${workflow.projectId}/brief-intake/candidates/${candidateId}/adopt`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            expected_intake_revision: intakeQuery.data.revision,
            expected_brief_revision: intakeQuery.data.brief.draft_revision,
          },
        },
      );
    },
    onSuccess: (result) => {
      queryClient.setQueryData(intakeQueryKey, result.intake);
      queryClient.setQueryData<BriefView>(
        ["brief", workflow.actorId, workflow.projectId],
        result.brief,
      );
      router.push("/brief");
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const intake = intakeQuery.data ?? null;
  const intakeClosed = intake?.stage === "brief_review";
  const currentCandidate =
    intake?.candidates.find(
      (candidate) => candidate.candidate_id === intake.current_candidate_id,
    ) ?? null;
  const manualSeed = useMemo(
    () => seedManualCandidate(sourceText, intake?.questions ?? []),
    [intake?.questions, sourceText],
  );
  const ideaSourceText =
    intakeClosed && intake?.current_source
      ? intake.current_source.content_text
      : sourceText;
  const mutationBusy = [
    continueMutation,
    polishMutation,
    adoptPolishMutation,
    answerMutation,
    synthesisMutation,
    candidateMutation,
    candidateActionMutation,
    adoptCandidateMutation,
  ].some((mutation) => mutation.isPending);
  const streamError =
    polishStream.streamError ??
    questionsStream.streamError ??
    synthesizeStream.streamError;
  const displayedError =
    actionError ??
    streamError ??
    (intakeQuery.error ? errorMessage(intakeQuery.error) : null) ??
    (providerQuery.error ? errorMessage(providerQuery.error) : null) ??
    (polishRecovery.error ? errorMessage(polishRecovery.error) : null) ??
    (questionsRecovery.error ? errorMessage(questionsRecovery.error) : null) ??
    (synthesizeRecovery.error ? errorMessage(synthesizeRecovery.error) : null);
  const meta = stepMeta[activeStep];
  const persistedStep =
    intake?.stage === "brief_review"
      ? "confirmation"
      : stageToStep[intake?.stage ?? "idea"];
  const highestReachableIndex = ["idea", "questions", "confirmation"].indexOf(
    persistedStep,
  );

  function resetIntake() {
    workflow.clear();
    setSourceText("");
    setActiveStep("idea");
    setPolishDraft("");
    setPolishReviewOpen(false);
    setActionError(null);
    hydratedSourceRef.current = null;
    hydratedStageRevisionRef.current = null;
    hydratedPolishReviewTaskRef.current = null;
    observedTerminalTasksRef.current.clear();
  }

  return (
    <main className={`document ${styles.homeDocument}`}>
      <DocumentHeader
        action={
          workflow.projectId ? (
            <button
              className={styles.newCaseAction}
              onClick={resetIntake}
              type="button"
            >
              新建案件
            </button>
          ) : undefined
        }
        eyebrow="建案中心 · A 路径"
        meta={[
          { label: "当前步骤", value: `${meta.index} · ${meta.label}` },
          {
            label: "保存状态",
            value: intake?.current_source ? "服务端可恢复" : "尚未起案",
            tone: displayedError ? "critical" : "default",
          },
        ]}
        title="把一句念头，立成一份卷宗。"
      />

      <div className={styles.intakeLayout}>
        <aside className={styles.pathRail} aria-label="建案入口">
          <header>
            <span>建案入口</span>
            <b>选择起点</b>
          </header>
          {intakePaths.map((path) => (
            <div data-active={path.active} key={path.code}>
              <b>{path.code}</b>
              <div className={styles.pathCopy}>
                <strong>{path.label}</strong>
                <span className={styles.pathSummary}>{path.summary}</span>
                <span className={styles.pathFooter}>
                  <span>{path.inputHint}</span>
                  <em>{path.active ? "进行中" : "规划中"}</em>
                </span>
              </div>
            </div>
          ))}
        </aside>

        <section className={styles.intakeWorkArea} aria-label="A 路径建案工作区">
          <nav className={styles.stepTrack} aria-label="建案步骤">
            {(["idea", "questions", "confirmation"] as IntakeStep[]).map(
              (step, index) => {
                const labels = ["最初想法", "关键追问", "创作简报确认"];
                const currentIndex = ["idea", "questions", "confirmation"].indexOf(
                  activeStep,
                );
                const reachable =
                  step === "idea" ||
                  Boolean(intake?.current_source) &&
                    index <= highestReachableIndex;
                return (
                  <button
                    aria-current={step === activeStep ? "step" : undefined}
                    data-active={step === activeStep}
                    data-complete={index < currentIndex}
                    disabled={!reachable}
                    key={step}
                    onClick={() => setActiveStep(step)}
                    type="button"
                  >
                    <b>{String(index + 1).padStart(2, "0")}</b>
                    <span>{labels[index]}</span>
                  </button>
                );
              },
            )}
          </nav>

          <div className={styles.threadStage} data-step={activeStep}>
            {activeStep === "idea" ? (
              <IntakeIdeaStep
                busy={mutationBusy}
                closed={intakeClosed}
                error={intakeClosed ? null : displayedError}
                onAdoptPolish={() => adoptPolishMutation.mutate()}
                onClosePolish={() => setPolishReviewOpen(false)}
                onContinue={() => continueMutation.mutate()}
                onOpenPolish={() => {
                  if (polishResult) setPolishDraft(polishResult.polished_text);
                  setPolishReviewOpen(true);
                }}
                onOpenSettings={openSettings}
                onOpenBrief={() => router.push("/brief")}
                onPolish={() => {
                  setPolishDraft("");
                  setPolishReviewOpen(true);
                  polishMutation.mutate();
                }}
                onPolishDraftChange={setPolishDraft}
                onSourceChange={(value) => {
                  setActionError(null);
                  setSourceText(value);
                }}
                onStartNewCase={resetIntake}
                polishCandidateStale={polishCandidateStale}
                polishDraft={polishDraft}
                polishResult={polishResult}
                polishReviewOpen={polishReviewOpen}
                polishTask={polishTask}
                providerReady={Boolean(providerQuery.data)}
                savedSource={intake?.current_source ?? null}
                sourceText={ideaSourceText}
              />
            ) : activeStep === "questions" ? (
              <IntakeQuestionsStep
                busy={mutationBusy}
                error={displayedError}
                hardQuestionsResolved={intake?.hard_questions_resolved ?? true}
                onAnswer={(question, answer) =>
                  answerMutation.mutate({ question, answer })
                }
                onBack={() => setActiveStep("idea")}
                onGenerate={() => synthesisMutation.mutate({})}
                onManualContinue={() => setActiveStep("confirmation")}
                onOpenSettings={openSettings}
                onRetryQuestions={() => continueMutation.mutate()}
                providerReady={Boolean(providerQuery.data)}
                questions={intake?.questions ?? []}
                questionsTask={questionsTask}
                sourceText={sourceText}
                synthesizeTask={synthesizeTask}
              />
            ) : intake ? (
              <IntakeConfirmationStep
                busy={mutationBusy}
                currentCandidate={currentCandidate}
                error={displayedError}
                intake={intake}
                manualSeed={manualSeed}
                onActivateCandidate={(candidateId) =>
                  candidateActionMutation.mutate({
                    candidateId,
                    action: "activate",
                  })
                }
                onAdoptCandidate={(candidateId) =>
                  adoptCandidateMutation.mutate(candidateId)
                }
                onBack={() => setActiveStep("questions")}
                onCreateManualCandidate={(content, parentCandidateId) =>
                  candidateMutation.mutate({ content, parentCandidateId })
                }
                onDialogueRevision={(candidateId, instruction) =>
                  synthesisMutation.mutate({
                    baseCandidateId: candidateId,
                    instruction,
                  })
                }
                onOpenSettings={openSettings}
                onSaveCandidate={(candidateId) =>
                  candidateActionMutation.mutate({
                    candidateId,
                    action: "save",
                  })
                }
                providerReady={Boolean(providerQuery.data)}
                synthesizeTask={synthesizeTask}
                key={currentCandidate?.candidate_id ?? "manual-candidate"}
              />
            ) : (
              <div className={styles.taskWaiting}>
                <span className={styles.taskPulse} aria-hidden="true" />
                <div>
                  <b>正在恢复建案状态</b>
                  <p>原文、回答与候选将从服务端载入。</p>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
