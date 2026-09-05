"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import type {
  BriefContent,
  BriefPolishResult,
  BriefStrategyOption,
  CandidateStrategy,
  ProviderName,
  TaskType,
  LatestTaskType,
  TaskView,
} from "@/lib/api-client";
import {
  createEmptyBrief,
  mergeReviewIntoBrief,
  type IntakeAnswer,
  type IntakeBrief,
  type BriefReview,
  type BriefCandidate,
  type IntakePolishMode,
  type IntakeQuestion,
  type IntakeStep,
} from "@/features/intake/intake-model";

import {
  activateBriefCandidate,
  adoptBriefCandidate,
  adoptDraftCandidateWithReconciliation,
  answerQuestion,
  beginBriefRevision as beginBriefRevisionRequest,
  cancelTask,
  confirmBrief,
  createBriefCandidate,
  createCaseProject,
  CaseSessionError,
  type QuestionAnswerInput,
  fetchBrief,
  fetchCaseDraft,
  fetchDraftCandidates,
  fetchCaseIntake,
  fetchLatestTask,
  isBriefIntakeRevisionConflict,
  isTaskCancelledError,
  persistCaseSource,
  runTaskWithProviderFallback,
  saveBriefCandidate,
  startAnchorExtractTask,
  startDraftGenerationTask,
  startPolishTask,
  startQuestionsTask,
  resumeDraftGenerationTask,
  startSynthesizeTask,
  startStrategyOptionsTask,
  strategyOptionsResult,
  updateBrief,
  waitForTask,
  waitForRecoveredTask,
} from "./case-session-api";
import {
  briefsMatch,
  currentIntakeCandidate,
  mapAuthoritativeDraftCandidateState,
  mapBriefContentToReview,
  mapBriefToCandidateContent,
  mapCurrentBriefDraftCandidates,
  mapIntakeBriefToAnchorContent,
  mapIntakeToSessionState,
  mapReviewToBriefContent,
  rankDraftCandidateStrategy,
  type SessionWorkbenchCandidate,
} from "./case-session-mapping";

export type GenerationStatus = "idle" | "generating" | "ready";
export type WorkbenchCandidateStatus = "pending" | "current" | "stale";
export type CandidateSlotStrategy = Exclude<CandidateStrategy, "balanced">;
export type CandidateSlotStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "cancelled"
  | "failed";
export type CandidateTaskStage =
  | "queued"
  | "planning"
  | "processing"
  | "generating"
  | "validating"
  | "completed"
  | "cancelled"
  | "failed";
export type CandidateGenerationOutcome = "succeeded" | "cancelled" | "not_started";
export type StrategyAnalysisStatus = "idle" | "analyzing" | "ready" | "failed";
export type SessionHydrationStatus = "idle" | "loading" | "ready" | "error";

export const CANDIDATE_SLOT_STRATEGIES: readonly CandidateSlotStrategy[] = [
  "structure_first",
  "atmosphere_first",
  "reasoning_first",
];

type CandidateSlot = {
  status: CandidateSlotStatus;
  stage: CandidateTaskStage;
  taskRunId: number | null;
  attempt: number;
  error: string | null;
  latestTask: TaskView | null;
};

export function candidateTaskStageFromTask(
  task: Pick<TaskView, "status" | "stage">,
): CandidateTaskStage {
  if (task.status === "succeeded" || task.stage === "completed") {
    return "completed";
  }
  if (task.status === "cancelled") return "cancelled";
  if (task.status === "failed" || task.stage === "failed") {
    return "failed";
  }
  if (task.stage === "planning") return "planning";
  if (task.stage === "generating") return "generating";
  if (task.stage === "validating") return "validating";
  if (task.stage === "queued" || task.stage === "preparing") return "queued";
  return "processing";
}

export function candidateSlotStatusFromTask(
  task: Pick<TaskView, "status">,
): CandidateSlotStatus {
  if (task.status === "succeeded") return "succeeded";
  if (task.status === "cancelled") return "cancelled";
  if (task.status === "failed") return "failed";
  return "running";
}

function createCandidateSlots(): Record<CandidateSlotStrategy, CandidateSlot> {
  return Object.fromEntries(
    CANDIDATE_SLOT_STRATEGIES.map((strategy) => [
      strategy,
      {
        status: "pending",
        stage: "queued",
        taskRunId: null,
        attempt: 1,
        error: null,
        latestTask: null,
      },
    ]),
  ) as Record<CandidateSlotStrategy, CandidateSlot>;
}

export interface CaseSessionState {
  hydration: {
    status: SessionHydrationStatus;
    error: string | null;
  };
  step: IntakeStep;
  furthestStep: number;
  sourceText: string;
  polishMode: IntakePolishMode;
  questions: IntakeQuestion[];
  answers: Record<string, IntakeAnswer>;
  brief: IntakeBrief;
  briefCandidates: BriefCandidate[];
  currentBriefCandidateId: number | null;
  review: BriefReview | null;
  workingBriefVersion: number;
  frozenBriefVersion: number | null;
  generation: {
    status: GenerationStatus;
    slots: Record<CandidateSlotStrategy, CandidateSlot>;
  };
  strategyAnalysis: {
    status: StrategyAnalysisStatus;
    options: BriefStrategyOption[];
    recommendedStrategy: CandidateSlotStrategy | null;
    recommendationReason: string | null;
    error: string | null;
  };
  selectedStrategy: CandidateSlotStrategy | null;
  draftCandidates: SessionWorkbenchCandidate[];
  previewCandidateId: string | null;
  adoptedCandidateId: string | null;
  latestTasks: Partial<Record<TaskType, TaskView>>;
}

type CaseSessionAction =
  | { type: "patch"; patch: Partial<CaseSessionState> }
  | { type: "confirm_brief"; review: BriefReview; versionNo: number }
  | { type: "start_generation"; strategies: CandidateSlotStrategy[] }
  | { type: "task_updated"; task: TaskView; strategy?: CandidateSlotStrategy; error?: string | null }
  | {
      type: "update_generation_slot";
      strategy: CandidateSlotStrategy;
      status: CandidateSlotStatus;
      stage?: CandidateTaskStage;
      taskRunId?: number | null;
      attempt?: number;
      error?: string | null;
    }
  | { type: "end_generation"; status: "idle" | "ready" }
  | {
      type: "strategy_analysis_ready";
      options: BriefStrategyOption[];
      recommendedStrategy: CandidateSlotStrategy;
      recommendationReason: string;
    }
  | { type: "select_strategy"; strategy: CandidateSlotStrategy }
  | { type: "complete_generation"; candidates: SessionWorkbenchCandidate[] }
  | { type: "preview_candidate"; candidateId: string | null }
  | { type: "adopt_candidate"; candidateId: string }
  | { type: "begin_revision" }
  | { type: "reset" };

export function createInitialCaseSessionState(): CaseSessionState {
  return {
    hydration: { status: "idle", error: null },
    step: "idea",
    furthestStep: 0,
    sourceText: "",
    polishMode: "rewrite",
    questions: [],
    answers: {},
    brief: createEmptyBrief(""),
    briefCandidates: [],
    currentBriefCandidateId: null,
    review: null,
    workingBriefVersion: 1,
    frozenBriefVersion: null,
    generation: { status: "idle", slots: createCandidateSlots() },
    strategyAnalysis: {
      status: "idle",
      options: [],
      recommendedStrategy: null,
      recommendationReason: null,
      error: null,
    },
    selectedStrategy: null,
    draftCandidates: [],
    previewCandidateId: null,
    adoptedCandidateId: null,
    latestTasks: {},
  };
}

export function caseSessionReducer(
  state: CaseSessionState,
  action: CaseSessionAction,
): CaseSessionState {
  if (action.type === "patch") return { ...state, ...action.patch };
  if (action.type === "task_updated") {
    const task = action.task;
    const strategy = task.task_type === "brief_to_draft"
      ? action.strategy ?? CANDIDATE_SLOT_STRATEGIES.find((value) => value === task.candidate_strategy)
      : undefined;
    return {
      ...state,
      latestTasks: { ...state.latestTasks, [task.task_type]: task },
      generation: strategy ? {
        ...state.generation,
        slots: {
          ...state.generation.slots,
          [strategy]: {
            ...state.generation.slots[strategy],
            status: candidateSlotStatusFromTask(task),
            stage: candidateTaskStageFromTask(task),
            taskRunId: task.task_run_id,
            error: action.error ?? null,
            latestTask: task,
          },
        },
      } : state.generation,
    };
  }
  if (action.type === "confirm_brief") {
    return {
      ...state,
      review: action.review,
      furthestStep: Math.max(state.furthestStep, 3),
      workingBriefVersion: action.versionNo,
      frozenBriefVersion: action.versionNo,
    };
  }
  if (action.type === "start_generation") {
    const slots = { ...state.generation.slots };
    for (const strategy of action.strategies) {
      slots[strategy] = {
        status: "running",
        stage: "queued",
        taskRunId: null,
        attempt: 1,
        error: null,
        latestTask: null,
      };
    }
    return { ...state, generation: { status: "generating", slots } };
  }
  if (action.type === "update_generation_slot") {
    const previous = state.generation.slots[action.strategy];
    return {
      ...state,
      generation: {
        ...state.generation,
        slots: {
          ...state.generation.slots,
          [action.strategy]: {
            status: action.status,
            stage: action.stage ?? previous.stage,
            taskRunId: action.taskRunId ?? previous.taskRunId,
            attempt: action.attempt ?? previous.attempt,
            error: action.error ?? null,
            latestTask: previous.latestTask,
          },
        },
      },
    };
  }
  if (action.type === "end_generation") {
    return {
      ...state,
      generation: { ...state.generation, status: action.status },
    };
  }
  if (action.type === "strategy_analysis_ready") {
    return {
      ...state,
      strategyAnalysis: {
        status: "ready",
        options: action.options,
        recommendedStrategy: action.recommendedStrategy,
        recommendationReason: action.recommendationReason,
        error: null,
      },
    };
  }
  if (action.type === "select_strategy") {
    return { ...state, selectedStrategy: action.strategy };
  }
  if (action.type === "complete_generation") {
    return {
      ...state,
      generation: { ...state.generation, status: "ready" },
      draftCandidates: [
        ...state.draftCandidates.filter(
          (existing) => !action.candidates.some((candidate) => candidate.id === existing.id),
        ),
        ...action.candidates,
      ],
    };
  }
  if (action.type === "preview_candidate") {
    return { ...state, previewCandidateId: action.candidateId };
  }
  if (action.type === "adopt_candidate") {
    const candidate = state.draftCandidates.find(
      (item) => item.id === action.candidateId,
    );
    if (!candidate || workbenchCandidateStatus(state, candidate) !== "pending") {
      return state;
    }
    return {
      ...state,
      adoptedCandidateId: candidate.id,
      previewCandidateId: candidate.id,
      draftCandidates: state.draftCandidates.map((item) =>
        item.candidateState
          ? {
              ...item,
              candidateState: {
                ...item.candidateState,
                isCurrent: item.id === candidate.id,
                isAdopted:
                  item.candidateState.isAdopted || item.id === candidate.id,
                canAdopt:
                  item.id === candidate.id ? false : item.candidateState.canAdopt,
              },
            }
          : item,
      ),
    };
  }
  if (action.type === "begin_revision") {
    return {
      ...state,
      step: "confirmation",
      furthestStep: Math.min(state.furthestStep, 2),
      brief: state.review
        ? mergeReviewIntoBrief(state.brief, state.review)
        : state.brief,
      workingBriefVersion: state.workingBriefVersion + 1,
      frozenBriefVersion: null,
      review: null,
      generation: { status: "idle", slots: createCandidateSlots() },
      strategyAnalysis: {
        status: "idle",
        options: [],
        recommendedStrategy: null,
        recommendationReason: null,
        error: null,
      },
      selectedStrategy: null,
      previewCandidateId: null,
    };
  }
  return createInitialCaseSessionState();
}

export function workbenchCandidateStatus(
  state: CaseSessionState,
  candidate: SessionWorkbenchCandidate,
): WorkbenchCandidateStatus {
  if (candidate.id === state.adoptedCandidateId) return "current";
  if (candidate.candidateState) {
    if (candidate.candidateState.isCurrent) return "current";
    return candidate.candidateState.canAdopt ? "pending" : "stale";
  }
  if (candidate.briefVersion === state.frozenBriefVersion) return "pending";
  return "stale";
}

export interface PolishResult {
  text: string;
  notes: string[];
  introducedDetails: string[];
  parentSourceRecordId: number | null;
}

export interface DialogueRevisionResult {
  baseBrief: IntakeBrief;
  candidate: BriefCandidate;
}

/** 被历史恢复顶替的当前会话，保存在单槽暂存中。 */
interface StashedSession {
  projectId: number;
  intake: Awaited<ReturnType<typeof fetchCaseIntake>>;
  brief: Awaited<ReturnType<typeof fetchBrief>> | null;
  state: CaseSessionState;
}

interface CaseSessionContextValue {
  state: CaseSessionState;
  activeProjectId: number | null;
  activeCandidate: SessionWorkbenchCandidate | null;
  patchState: (patch: Partial<CaseSessionState>) => void;
  confirmBriefAndContinue: (draftOverride?: IntakeBrief) => Promise<number>;
  generateCandidates: (
    strategy?: CandidateSlotStrategy,
    attempt?: number,
  ) => Promise<CandidateGenerationOutcome>;
  resumeGeneration: (strategy: CandidateSlotStrategy, onReload?: () => void) => Promise<boolean>;
  cancelGeneration: (strategy: CandidateSlotStrategy, onReload?: () => void) => Promise<TaskView | null>;
  retryTask: (taskType: TaskType) => Promise<PolishResult | null>;
  analyzeStrategies: (refresh?: boolean) => Promise<boolean>;
  selectStrategy: (strategy: CandidateSlotStrategy) => void;
  previewCandidate: (candidateId: string | null) => void;
  adoptCandidate: (candidateId: string) => Promise<number | false>;
  beginBriefRevision: () => Promise<void>;
  candidateStatus: (
    candidate: SessionWorkbenchCandidate,
  ) => WorkbenchCandidateStatus;
  resetSession: () => void;
  loadProject: (projectId: number) => Promise<void>;
  getSessionEpoch: () => number;
  stashCurrentSession: () => void;
  restoreStashedSession: () => void;
  hasStashedSession: boolean;
  submitPolish: (mode: IntakePolishMode) => Promise<PolishResult>;
  adoptPolish: (
    draft: string,
    parentSourceRecordId: number | null,
  ) => Promise<void>;
  continueToQuestions: () => Promise<void>;
  generateMoreQuestions: () => Promise<void>;
  generateBriefFromAnswers: () => Promise<void>;
  saveCandidateAsNew: () => Promise<void>;
  createDialogueRevision: (
    instruction: string,
  ) => Promise<DialogueRevisionResult>;
  saveCandidateBookmark: (candidateId: number) => Promise<void>;
  activateCandidate: (candidateId: number) => Promise<void>;
  generateAuthorAnswer: (draft?: IntakeBrief) => Promise<string>;
}

const CaseSessionContext = createContext<CaseSessionContextValue | null>(
  null,
);

const RECOVERABLE_TASK_TYPES: readonly LatestTaskType[] = [
  "brief_polish",
  "brief_anchor_extract",
  "brief_intake_questions",
  "brief_intake_synthesize",
  "brief_strategy_options",
  "brief_to_draft",
];

const ACTIVE_TASK_STATUSES = new Set<TaskView["status"]>([
  "queued",
  "running",
  "cancelling",
]);

function assertSessionActive(signal: AbortSignal): void {
  if (signal.aborted) {
    throw new CaseSessionError("会话已切换，已停止旧操作。", "session_changed");
  }
}

export function CaseSessionProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    caseSessionReducer,
    undefined,
    createInitialCaseSessionState,
  );
  const [activeProjectId, setActiveProjectId] = useState<number | null>(null);
  const stateRef = useRef(state);
  const projectIdRef = useRef<number | null>(null);
  const sessionEpochRef = useRef(0);
  const sessionReadControllerRef = useRef(new AbortController());
  const intakeRef = useRef<Awaited<ReturnType<typeof fetchCaseIntake>> | null>(
    null,
  );
  const briefRef = useRef<Awaited<ReturnType<typeof fetchBrief>> | null>(
    null,
  );
  const recoveringTaskIdsRef = useRef(new Set<string>());
  const initialProjectLoadRef = useRef(false);
  const projectCreationRef = useRef<{
    signal: AbortSignal;
    promise: ReturnType<typeof createCaseProject>;
  } | null>(null);

  const syncProjectPointer = useCallback((projectId: number | null) => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (projectId === null) {
      url.searchParams.delete("project");
    } else {
      url.searchParams.set("project", String(projectId));
    }
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
  }, []);

  useLayoutEffect(() => {
    stateRef.current = state;
  }, [state]);

  const invalidateSession = useCallback(() => {
    const epoch = ++sessionEpochRef.current;
    sessionReadControllerRef.current.abort();
    projectCreationRef.current = null;
    sessionReadControllerRef.current = new AbortController();
    return epoch;
  }, []);

  useEffect(() => () => { invalidateSession(); }, [invalidateSession]);

  const ensureProjectAndSource = useCallback(async (
    text: string,
  ): Promise<Awaited<ReturnType<typeof fetchCaseIntake>>> => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const normalized = text.trim();
    if (!normalized) throw new CaseSessionError("请先写下最初想法。");
    let projectId = projectIdRef.current;
    let intake = intakeRef.current;
    if (projectId === null) {
      const creation = projectCreationRef.current?.signal === signal
        ? projectCreationRef.current
        : { signal, promise: createCaseProject(text) };
      projectCreationRef.current = creation;
      let project: Awaited<ReturnType<typeof createCaseProject>>;
      try {
        project = await creation.promise;
      } finally {
        if (projectCreationRef.current === creation) projectCreationRef.current = null;
      }
      assertSessionActive(signal);
      projectId = project.id;
      projectIdRef.current = projectId;
      setActiveProjectId(project.id);
      syncProjectPointer(project.id);
      intake = await fetchCaseIntake(project.id);
      assertSessionActive(signal);
      intakeRef.current = intake;
    } else if (!intake) {
      const existingProjectId = projectId;
      intake = await fetchCaseIntake(existingProjectId);
      assertSessionActive(signal);
      intakeRef.current = intake;
    }
    if (intake.current_source?.content_text !== normalized) {
      const revision = intake.revision;
      intake = await persistCaseSource(
        projectId,
        revision,
        normalized,
      );
      assertSessionActive(signal);
      intakeRef.current = intake;
    }
    return intake;
  }, [syncProjectPointer]);

  const patchState = useCallback((patch: Partial<CaseSessionState>) => {
    dispatch({ type: "patch", patch });
  }, []);

  const submitPolish = useCallback(
    async (mode: IntakePolishMode): Promise<PolishResult> => {
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const current = stateRef.current;
      const intake = await ensureProjectAndSource(current.sourceText);
      assertSessionActive(signal);
      const projectId = intake.project_id;
      const sourceRecordId = intake.current_source?.source_record_id;
      if (!sourceRecordId) throw new CaseSessionError("起案原文尚未保存。");
      const { result: done } = await runTaskWithProviderFallback(
        async (provider) => {
          assertSessionActive(signal);
          const task = await startPolishTask(
            projectId,
            sourceRecordId,
            provider,
            mode,
          );
          assertSessionActive(signal);
          return waitForTask(projectId, task.task_run_id, undefined, signal);
        },
      );
      assertSessionActive(signal);
      const result = done.result as BriefPolishResult | null;
      if (!result) throw new CaseSessionError("润色任务没有返回结果。");
      return {
        text: result.polished_text,
        notes: [result.preserved_intent_summary, ...result.ambiguities].filter(
          Boolean,
        ),
        introducedDetails: result.introduced_details ?? [],
        parentSourceRecordId:
          result.proposal_source_record?.source_record_id ?? null,
      };
    },
    [ensureProjectAndSource],
  );

  const adoptPolish = useCallback(
    async (draft: string, parentSourceRecordId: number | null) => {
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const projectId = projectIdRef.current;
      const intake = intakeRef.current;
      if (projectId === null || !intake) {
        throw new CaseSessionError("当前会话尚未建案。");
      }
      const updatedIntake = await persistCaseSource(
        projectId,
        intake.revision,
        draft.trim(),
        parentSourceRecordId,
      );
      assertSessionActive(signal);
      intakeRef.current = updatedIntake;
    },
    [],
  );

  const startWithFreshIntakeRevision = useCallback(
    async <T,>(operation: (revision: number) => Promise<T>): Promise<T> => {
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const projectId = projectIdRef.current;
      if (projectId === null) {
        throw new CaseSessionError("当前会话尚未建案。");
      }
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const fresh = await fetchCaseIntake(projectId);
        assertSessionActive(signal);
        intakeRef.current = fresh;
        try {
          const result = await operation(fresh.revision);
          assertSessionActive(signal);
          return result;
        } catch (error) {
          assertSessionActive(signal);
          if (!isBriefIntakeRevisionConflict(error)) throw error;
          if (attempt === 1) {
            throw new CaseSessionError(
              "建案内容刚刚发生更新，已同步最新版本，请再试一次。",
              "brief_intake_revision_conflict",
            );
          }
        }
      }
      throw new CaseSessionError("建案版本同步失败，请再试一次。");
    },
    [],
  );

  const persistCurrentAnswers = useCallback(async () => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    return startWithFreshIntakeRevision(async () => {
      assertSessionActive(signal);
      const projectId = projectIdRef.current;
      if (projectId === null || !intakeRef.current) {
        throw new CaseSessionError("当前会话尚未建案。");
      }
      let intake = intakeRef.current;
      for (const question of intake.questions) {
        const answer = current.answers[question.question_key];
        if (!answer) continue;
        let input: QuestionAnswerInput;
        if (answer.pending) {
          input = { mode: "pending" };
        } else if (answer.source === "agent_suggestion") {
          const suggestionIndex = question.suggestions.indexOf(answer.text);
          input =
            suggestionIndex >= 0
              ? { mode: "suggestion", suggestionIndex }
              : { mode: "answer", text: answer.text };
        } else {
          input = { mode: "answer", text: answer.text };
        }
        intake = await answerQuestion(
          projectId,
          intake.revision,
          question.question_key,
          input,
        );
        assertSessionActive(signal);
      }
      intakeRef.current = intake;
      return intake;
    });
  }, [startWithFreshIntakeRevision]);

  const requestQuestions = useCallback(async (forceGeneration: boolean) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    let intake = await ensureProjectAndSource(current.sourceText);
    assertSessionActive(signal);
    const projectId = intake.project_id;
    const hadExistingQuestions = intake.questions.length > 0;
    if (forceGeneration) {
      // “再生成一些问题”必须先把页面上尚未落库的回答写入服务端：
      // 追加任务要带着已有问答去避开重复，完成后的服务端映射也不会清空本地回答。
      intake = await persistCurrentAnswers();
      assertSessionActive(signal);
    }
    if (forceGeneration || intake.questions.length === 0) {
      await runTaskWithProviderFallback(async (provider) => {
        assertSessionActive(signal);
        const task = await startWithFreshIntakeRevision((revision) =>
          startQuestionsTask(
            projectId,
            revision,
            provider,
          ),
        );
        assertSessionActive(signal);
        return waitForTask(projectId, task.task_run_id, undefined, signal);
      });
      assertSessionActive(signal);
      intake = await fetchCaseIntake(projectId);
      assertSessionActive(signal);
      intakeRef.current = intake;
    }
    const mapped = mapIntakeToSessionState(intake);
    const preservedAnswers = { ...mapped.answers };
    for (const question of mapped.questions) {
      const localAnswer = current.answers[question.key];
      if (localAnswer) preservedAnswers[question.key] = localAnswer;
    }
    dispatch({
      type: "patch",
      patch: {
        step: "questions",
        furthestStep: Math.max(current.furthestStep, 1),
        questions: mapped.questions,
        answers:
          forceGeneration || !hadExistingQuestions
            ? mapped.answers
            : preservedAnswers,
      },
    });
  }, [ensureProjectAndSource, persistCurrentAnswers, startWithFreshIntakeRevision]);

  const continueToQuestions = useCallback(
    () => requestQuestions(false),
    [requestQuestions],
  );

  const generateMoreQuestions = useCallback(
    () => requestQuestions(true),
    [requestQuestions],
  );

  const generateBriefFromAnswers = useCallback(async () => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    let intake =
      intakeRef.current ?? (await ensureProjectAndSource(current.sourceText));
    assertSessionActive(signal);
    const projectId = intake.project_id;
    const baseCandidateId = intake.current_candidate_id;
    for (const question of intake.questions) {
      const answer = current.answers[question.question_key];
      if (!answer) continue;
      let input: QuestionAnswerInput;
      if (answer.pending) {
        input = { mode: "pending" };
      } else if (answer.source === "agent_suggestion") {
        const suggestionIndex = question.suggestions.indexOf(answer.text);
        input =
          suggestionIndex >= 0
            ? { mode: "suggestion", suggestionIndex }
            : { mode: "answer", text: answer.text };
      } else {
        input = { mode: "answer", text: answer.text };
      }
      intake = await answerQuestion(
        projectId,
        intake.revision,
        question.question_key,
        input,
      );
      assertSessionActive(signal);
      intakeRef.current = intake;
    }
    await runTaskWithProviderFallback(async (provider) => {
      assertSessionActive(signal);
      const task = await startWithFreshIntakeRevision((revision) =>
        startSynthesizeTask(
          projectId,
          revision,
          provider,
          baseCandidateId,
          baseCandidateId === null
            ? null
            : "根据最新起案与关键问答重新整理简报；保留当前候选中未受影响的作者修改。",
        ),
      );
      assertSessionActive(signal);
      return waitForTask(projectId, task.task_run_id, undefined, signal);
    });
    assertSessionActive(signal);
    const updatedIntake = await fetchCaseIntake(projectId);
    assertSessionActive(signal);
    intakeRef.current = updatedIntake;
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({
      type: "patch",
      patch: {
        step: "confirmation",
        furthestStep: Math.max(current.furthestStep, 2),
        ...mapped,
        brief: mapped.brief ?? current.brief,
      },
    });
  }, [ensureProjectAndSource, startWithFreshIntakeRevision]);

  const saveCandidateAsNew = useCallback(async () => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const projectId = projectIdRef.current;
    const current = stateRef.current;
    const intake = intakeRef.current;
    if (projectId === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    const updatedIntake = await createBriefCandidate(
      projectId,
      intake.revision,
      mapBriefToCandidateContent(current.brief),
      intake.current_candidate_id,
    );
    assertSessionActive(signal);
    intakeRef.current = updatedIntake;
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({ type: "patch", patch: { ...mapped, brief: current.brief } });
  }, []);

  const createDialogueRevision = useCallback(async (instruction: string) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const projectId = projectIdRef.current;
    const current = stateRef.current;
    const intake = intakeRef.current;
    if (projectId === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    if (intake.current_candidate_id === null) {
      throw new CaseSessionError("先保存一个候选，再发起对话修改。");
    }
    const baseCandidateId = intake.current_candidate_id;
    const baseBrief =
      current.briefCandidates.find(
        (candidate) => candidate.id === baseCandidateId,
      )?.brief ?? current.brief;
    await runTaskWithProviderFallback(async (provider) => {
      // 任务创建会推进 intake revision，回退重试前必须重取最新版本。
      assertSessionActive(signal);
      const fresh = await fetchCaseIntake(projectId);
      assertSessionActive(signal);
      const task = await startSynthesizeTask(
        projectId,
        fresh.revision,
        provider,
        baseCandidateId,
        instruction,
      );
      assertSessionActive(signal);
      return waitForTask(projectId, task.task_run_id, undefined, signal);
    });
    assertSessionActive(signal);
    const updatedIntake = await fetchCaseIntake(projectId);
    assertSessionActive(signal);
    intakeRef.current = updatedIntake;
    const mapped = mapIntakeToSessionState(intakeRef.current);
    const candidate = mapped.briefCandidates.find(
      (item) => item.id === mapped.currentBriefCandidateId,
    );
    if (!candidate) {
      throw new CaseSessionError("Agent 已完成修改，但新候选暂时无法读取。");
    }
    dispatch({
      type: "patch",
      patch: { ...mapped, brief: mapped.brief ?? current.brief },
    });
    return { baseBrief, candidate };
  }, []);

  const saveCandidateBookmark = useCallback(async (candidateId: number) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const projectId = projectIdRef.current;
    const intake = intakeRef.current;
    if (projectId === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    const updatedIntake = await saveBriefCandidate(
      projectId,
      intake.revision,
      candidateId,
    );
    assertSessionActive(signal);
    intakeRef.current = updatedIntake;
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({
      type: "patch",
      patch: { briefCandidates: mapped.briefCandidates },
    });
  }, []);

  const activateCandidate = useCallback(async (candidateId: number) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const projectId = projectIdRef.current;
    const intake = intakeRef.current;
    if (projectId === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    const updatedIntake = await activateBriefCandidate(
      projectId,
      intake.revision,
      candidateId,
    );
    assertSessionActive(signal);
    intakeRef.current = updatedIntake;
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({
      type: "patch",
      patch: {
        ...mapped,
        brief: mapped.brief ?? stateRef.current.brief,
      },
    });
  }, []);

  const confirmBriefAndContinue = useCallback(
    async (draftOverride?: IntakeBrief) => {
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const current = stateRef.current;
      const draft = draftOverride ?? current.brief;
      const projectId = projectIdRef.current;
      if (projectId === null) {
        throw new CaseSessionError("请先完成最初想法与追问。");
      }
      if (current.frozenBriefVersion !== null) {
        throw new CaseSessionError("创作简报已经冻结；修改内容请先建立简报修订。");
      }

      // 每次从权威 Intake 开始。上次确认即使只完成了采用或写回，
      // 也能从 brief_review 状态继续，而不会重复创建不可变版本。
      let intake = await fetchCaseIntake(projectId);
      assertSessionActive(signal);
      intakeRef.current = intake;
      let candidate = currentIntakeCandidate(intake);

      if (
        intake.stage === "brief_review" &&
        (!candidate || !briefsMatch(draft, candidate.content))
      ) {
        intake = await beginBriefRevisionRequest(projectId);
        assertSessionActive(signal);
        intakeRef.current = intake;
        candidate = currentIntakeCandidate(intake);
      }

      let adoptedBrief: Awaited<ReturnType<typeof fetchBrief>>;
      if (
        intake.stage === "brief_review" &&
        candidate &&
        briefsMatch(draft, candidate.content)
      ) {
        adoptedBrief = await fetchBrief(projectId);
        assertSessionActive(signal);
      } else {
        let candidateId = intake.current_candidate_id;
        if (
          candidateId === null ||
          !candidate ||
          !briefsMatch(draft, candidate.content)
        ) {
          intake = await createBriefCandidate(
            projectId,
            intake.revision,
            mapBriefToCandidateContent(draft),
            candidateId,
          );
          assertSessionActive(signal);
          intakeRef.current = intake;
          candidateId = intake.current_candidate_id;
        }
        if (candidateId === null) {
          throw new CaseSessionError("当前还没有可采用的候选简报。");
        }
        const adopted = await adoptBriefCandidate(
          projectId,
          intake.revision,
          candidateId,
          intake.brief.draft_revision,
        );
        assertSessionActive(signal);
        intakeRef.current = adopted.intake;
        adoptedBrief = adopted.brief;
      }

      const pendingDecisions = current.questions
        .filter((question) => current.answers[question.key]?.pending)
        .map((question) => question.prompt);
      const review = {
        ...mapBriefContentToReview(adoptedBrief.content, pendingDecisions),
        dirty: false,
        saved: true,
      };
      const content = mapReviewToBriefContent(review, draft, adoptedBrief.content);
      const savedBrief = await updateBrief(
        projectId,
        adoptedBrief.draft_revision,
        content,
      );
      assertSessionActive(signal);
      briefRef.current = savedBrief;
      const version = await confirmBrief(projectId, savedBrief.draft_revision);
      assertSessionActive(signal);
      try {
        const refreshedBrief = await fetchBrief(projectId);
        assertSessionActive(signal);
        briefRef.current = refreshedBrief;
      } catch {
        assertSessionActive(signal);
        // 冻结已经持久化。后续策略分析会按需重新读取 Brief。
        briefRef.current = null;
      }
      dispatch({ type: "confirm_brief", review, versionNo: version.version_no });
      dispatch({ type: "patch", patch: { brief: draft } });
      return version.version_no;
    },
    [],
  );

  const runAnchorExtract = useCallback(
    async (
      mode: "extract" | "suggest_author_answer",
      content?: BriefContent,
    ) => {
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const projectId = projectIdRef.current;
      if (projectId === null) throw new CaseSessionError("当前会话尚未建案。");
      const brief = briefRef.current ?? (await fetchBrief(projectId));
      assertSessionActive(signal);
      const { result: done } = await runTaskWithProviderFallback(
        async (provider) => {
          assertSessionActive(signal);
          const task = await startAnchorExtractTask(
            projectId,
            brief.draft_revision,
            provider,
            mode,
            content,
          );
          assertSessionActive(signal);
          return waitForTask(projectId, task.task_run_id, undefined, signal);
        },
      );
      assertSessionActive(signal);
      const result = done.result as
        | {
            suggested_author_answer?: string;
            author_anchors: Array<{ statement: string }>;
            creative_constraints: Array<{
              statement: string;
              suggested_strength: "hard" | "soft";
            }>;
          }
        | null;
      if (!result) throw new CaseSessionError("拆解任务没有返回结果。");
      return result;
    },
    [],
  );

  const generateAuthorAnswer = useCallback(async (draftOverride?: IntakeBrief) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    const draft = draftOverride ?? current.brief;
    const projectId = projectIdRef.current;
    if (projectId === null) throw new CaseSessionError("当前会话尚未建案。");
    const brief = briefRef.current ?? (await fetchBrief(projectId));
    assertSessionActive(signal);
    // 答案候选必须反映作者此刻看到的表单，即使会话中仍保留旧的
    // review 读模型；后端会把它作为临时上下文，而不是可冻结 Brief 校验。
    const content = mapIntakeBriefToAnchorContent(
      draft,
      brief.content,
      intakeRef.current?.current_source?.source_record_id ?? null,
    );
    const result = await runAnchorExtract("suggest_author_answer", content);
    assertSessionActive(signal);
    const suggestion = result.suggested_author_answer?.trim();
    if (!suggestion) {
      throw new CaseSessionError("Agent 没有形成可审阅的作者答案候选，请直接填写你的结论。");
    }
    return suggestion;
  }, [runAnchorExtract]);

  const analyzeStrategies = useCallback(async (refresh = false) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    if (current.strategyAnalysis.status === "analyzing") return false;
    const projectId = projectIdRef.current;
    if (projectId === null) return false;
    dispatch({
      type: "patch",
      patch: {
        strategyAnalysis: {
          ...current.strategyAnalysis,
          status: "analyzing",
          error: null,
        },
      },
    });
    try {
      const brief = briefRef.current ?? (await fetchBrief(projectId));
      assertSessionActive(signal);
      if (!brief.current_version_id) {
        throw new CaseSessionError("请先冻结当前创作简报。");
      }
      const { result: done } = await runTaskWithProviderFallback(
        async (provider) => {
          assertSessionActive(signal);
          const task = await startStrategyOptionsTask(
            projectId,
            brief.current_version_id!,
            provider,
            refresh,
          );
          assertSessionActive(signal);
          return waitForTask(projectId, task.task_run_id, undefined, signal);
        },
      );
      assertSessionActive(signal);
      const result = strategyOptionsResult(done);
      if (!result || result.options.length !== 3) {
        throw new CaseSessionError("策略分析任务没有返回完整的三个方向。");
      }
      dispatch({
        type: "strategy_analysis_ready",
        options: result.options,
        recommendedStrategy: result.recommended_strategy,
        recommendationReason: result.recommendation_reason,
      });
      return true;
    } catch (error) {
      assertSessionActive(signal);
      const latest = stateRef.current.strategyAnalysis;
      dispatch({
        type: "patch",
        patch: {
          strategyAnalysis: {
            ...latest,
            status: "failed",
            error: error instanceof Error ? error.message : "策略分析失败",
          },
        },
      });
      throw error;
    }
  }, []);

  const selectStrategy = useCallback((strategy: CandidateSlotStrategy) => {
    dispatch({ type: "select_strategy", strategy });
  }, []);

  const generateCandidates = useCallback(
    async (
      requestedStrategy?: CandidateSlotStrategy,
      requestedAttempt = 1,
    ) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    const selectedStrategy = requestedStrategy ?? current.selectedStrategy;
    if (
      !current.review ||
      current.frozenBriefVersion === null ||
      !selectedStrategy ||
      current.generation.status === "generating"
    ) {
      return "not_started";
    }
    const projectId = projectIdRef.current;
    if (projectId === null) return "not_started";
    try {
      const brief = briefRef.current ?? (await fetchBrief(projectId));
      assertSessionActive(signal);
      if (!brief.current_version_id) {
        throw new CaseSessionError("请先冻结当前创作简报。");
      }
      const existingCandidates = await fetchDraftCandidates(projectId);
      assertSessionActive(signal);
      const currentOnes = existingCandidates.filter(
        (candidate) => candidate.is_current_brief,
      );
      const existingStrategies = new Set(
        currentOnes
          .map((candidate) => candidate.candidate_strategy)
          .filter(
            (strategy): strategy is CandidateSlotStrategy =>
              strategy !== "balanced",
          ),
      );
      const requestedStrategies = [selectedStrategy];
      const regenerateStrategies = requestedStrategies.filter(
        (strategy) =>
          existingStrategies.has(strategy) && requestedAttempt > 1,
      );
      const missingStrategies = requestedStrategies.filter(
        (strategy) => !existingStrategies.has(strategy),
      );
      const strategiesToRun = [...missingStrategies, ...regenerateStrategies];
      for (const strategy of existingStrategies) {
        dispatch({
          type: "update_generation_slot",
          strategy,
          status: "succeeded",
          stage: "completed",
        });
      }
      const draft = await fetchCaseDraft(projectId);
      assertSessionActive(signal);
      dispatch({ type: "start_generation", strategies: strategiesToRun });
      let workingProvider: ProviderName | null = null;

      if (strategiesToRun.length > 0) {
        // 当前选中策略负责 Provider 认证回退；成功后供候选去重重试复用。
        const firstStrategy = strategiesToRun.includes("structure_first")
          ? "structure_first"
          : strategiesToRun[0];
        const fallbackResult = await runTaskWithProviderFallback(
          async (provider) => {
            assertSessionActive(signal);
            let taskRunId: number | null = null;
            try {
              const task = await startDraftGenerationTask(
                projectId,
                brief.current_version_id!,
                draft.draft_id,
                draft.revision,
                provider,
                firstStrategy,
                selectedStrategy === firstStrategy ? requestedAttempt : 1,
              );
              assertSessionActive(signal);
              taskRunId = task.task_run_id;
              dispatch({
                type: "update_generation_slot",
                strategy: firstStrategy,
                status: "running",
                taskRunId,
                attempt:
                  selectedStrategy === firstStrategy ? requestedAttempt : 1,
              });
              await waitForTask(projectId, task.task_run_id, (latestTask) => {
                if (signal.aborted) return;
                dispatch({
                  type: "task_updated",
                  strategy: firstStrategy,
                  task: latestTask,
                });
              }, signal);
              assertSessionActive(signal);
              dispatch({
                type: "update_generation_slot",
                strategy: firstStrategy,
                status: "succeeded",
                stage: "completed",
              });
              return provider;
            } catch (error) {
              assertSessionActive(signal);
              dispatch({
                type: "update_generation_slot",
                strategy: firstStrategy,
                status: "failed",
                stage: "failed",
                taskRunId,
                error: error instanceof Error ? error.message : "生成失败",
              });
              throw error;
            }
          },
        );
        assertSessionActive(signal);
        workingProvider = fallbackResult.provider;
      }

      const candidates = await fetchDraftCandidates(projectId);
      assertSessionActive(signal);
      let refreshedCurrentOnes = candidates.filter(
        (candidate) => candidate.is_current_brief,
      );
      const duplicateRetryStrategies = [
        ...new Set(
          [...
            refreshedCurrentOnes.reduce((groups, candidate) => {
              if (
                !missingStrategies.includes(
                  candidate.candidate_strategy as CandidateSlotStrategy,
                ) ||
                candidate.candidate_strategy === "balanced"
              ) {
                return groups;
              }
              const group = groups.get(candidate.content_hash) ?? [];
              group.push(candidate);
              groups.set(candidate.content_hash, group);
              return groups;
            }, new Map<string, typeof refreshedCurrentOnes>()).values(),
          ].flatMap((group) => {
            if (group.length < 2) return [];
            return group
              .sort(
                (left, right) =>
                  rankDraftCandidateStrategy(left.candidate_strategy) -
                  rankDraftCandidateStrategy(right.candidate_strategy),
              )
              .slice(1)
              .map((candidate) => candidate.candidate_strategy as CandidateSlotStrategy);
          }),
        ),
      ];
      if (workingProvider && duplicateRetryStrategies.length > 0) {
        await Promise.allSettled(
          duplicateRetryStrategies.map(async (strategy) => {
            assertSessionActive(signal);
            let taskRunId: number | null = null;
            dispatch({
              type: "update_generation_slot",
              strategy,
              status: "running",
              stage: "queued",
              attempt: 2,
            });
            try {
              const task = await startDraftGenerationTask(
                projectId,
                brief.current_version_id!,
                draft.draft_id,
                draft.revision,
                workingProvider!,
                strategy,
                2,
              );
              assertSessionActive(signal);
              taskRunId = task.task_run_id;
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "running",
                stage: "queued",
                taskRunId,
                attempt: 2,
              });
              await waitForTask(projectId, task.task_run_id, (latestTask) => {
                if (signal.aborted) return;
                dispatch({
                  type: "task_updated",
                  strategy,
                  task: latestTask,
                });
              }, signal);
              assertSessionActive(signal);
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "succeeded",
                stage: "completed",
                attempt: 2,
              });
            } catch (error) {
              assertSessionActive(signal);
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "failed",
                stage: "failed",
                taskRunId,
                attempt: 2,
                error: error instanceof Error ? error.message : "生成失败",
              });
            }
          }),
        );
        assertSessionActive(signal);
        refreshedCurrentOnes = (await fetchDraftCandidates(projectId)).filter(
          (candidate) => candidate.is_current_brief,
        );
        assertSessionActive(signal);
      }
      const mapped = mapCurrentBriefDraftCandidates(
        refreshedCurrentOnes,
        current.review,
        current.frozenBriefVersion,
      );
      dispatch({ type: "complete_generation", candidates: mapped });
      return refreshedCurrentOnes.some(
        (candidate) => candidate.candidate_strategy === selectedStrategy,
      )
        ? "succeeded"
        : "not_started";
    } catch (error) {
      assertSessionActive(signal);
      if (isTaskCancelledError(error)) {
        dispatch({
          type: "task_updated",
          strategy: selectedStrategy,
          task: error.task,
          error: null,
        });
        dispatch({ type: "end_generation", status: "idle" });
        return "cancelled";
      }
      dispatch({ type: "end_generation", status: "idle" });
      throw error;
    }
    },
    [],
  );

  const previewCandidate = useCallback((candidateId: string | null) => {
    dispatch({ type: "preview_candidate", candidateId });
  }, []);

  const retryTask = useCallback(
    async (taskType: TaskType): Promise<PolishResult | null> => {
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const projectId = projectIdRef.current;
      const current = stateRef.current;
      const refreshLatest = async (taskType: LatestTaskType) => {
        assertSessionActive(signal);
        if (projectId === null) return;
        let latest: TaskView | null = null;
        try {
          latest = await fetchLatestTask(projectId, taskType);
          assertSessionActive(signal);
        } catch {
          // The retry request has already been submitted; a refresh failure should not
          // turn a successful retry into a false error state in the UI.
          assertSessionActive(signal);
          return;
        }
        if (!latest) return;
        dispatch({ type: "task_updated", task: latest });
      };
      switch (taskType) {
        case "brief_polish": {
          const result = await submitPolish(current.polishMode);
          assertSessionActive(signal);
          await refreshLatest(taskType);
          assertSessionActive(signal);
          return result;
        }
        case "brief_intake_questions":
          await requestQuestions(true);
          assertSessionActive(signal);
          await refreshLatest(taskType);
          assertSessionActive(signal);
          return null;
        case "brief_intake_synthesize":
          await generateBriefFromAnswers();
          assertSessionActive(signal);
          await refreshLatest(taskType);
          assertSessionActive(signal);
          return null;
        case "brief_strategy_options":
          await analyzeStrategies(true);
          assertSessionActive(signal);
          await refreshLatest(taskType);
          assertSessionActive(signal);
          return null;
        case "brief_to_draft": {
          if (!current.selectedStrategy) {
            throw new CaseSessionError("请先选择一个创作策略，再重试深稿生成。");
          }
          const previousTask = current.latestTasks[taskType];
          await generateCandidates(
            current.selectedStrategy,
            (previousTask?.attempt_count ?? 1) + 1,
          );
          assertSessionActive(signal);
          await refreshLatest(taskType);
          assertSessionActive(signal);
          return null;
        }
        default:
          throw new CaseSessionError("当前任务类型暂不支持自动重试，请回到对应步骤操作。");
      }
    },
    [
      analyzeStrategies,
      generateBriefFromAnswers,
      generateCandidates,
      requestQuestions,
      submitPolish,
    ],
  );

  const adoptCandidate = useCallback(async (candidateId: string) => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const current = stateRef.current;
    const candidate = current.draftCandidates.find(
      (item) => item.id === candidateId,
    );
    if (!candidate || workbenchCandidateStatus(current, candidate) !== "pending") return false;
    const projectId = projectIdRef.current;
    if (projectId === null) {
      // 无后端会话（纯 fixture 接力场景）时只在会话内采用。
      dispatch({ type: "adopt_candidate", candidateId });
      return 1;
    }
    const taskRunId = Number(candidateId.replace(/^draft-/, ""));
    if (!Number.isInteger(taskRunId)) return false;
    const draft = await fetchCaseDraft(projectId);
    assertSessionActive(signal);
    const outcome = await adoptDraftCandidateWithReconciliation(
      projectId,
      taskRunId,
      draft.draft_id,
    );
    assertSessionActive(signal);
    let reconciled = false;
    if (outcome.facts) {
      const latest = stateRef.current;
      if (latest.review) {
        dispatch({
          type: "patch",
          patch: mapAuthoritativeDraftCandidateState(
            outcome.facts.candidates,
            latest.review,
            latest.previewCandidateId,
          ),
        });
        reconciled = outcome.facts.targetIsCurrent;
      }
    }
    if (outcome.error) throw outcome.error;
    if (!reconciled) dispatch({ type: "adopt_candidate", candidateId });
    try {
      const refreshedBrief = await fetchBrief(projectId);
      assertSessionActive(signal);
      briefRef.current = refreshedBrief;
    } catch {
      assertSessionActive(signal);
      // Candidate adoption is already durable. A Brief refresh failure must not
      // turn the successful write into an author-facing adoption failure.
    }
    return outcome.adoption?.draft_id ?? outcome.facts?.draft.draft_id ?? false;
  }, []);

  const beginBriefRevision = useCallback(async () => {
    const signal = sessionReadControllerRef.current.signal;
    assertSessionActive(signal);
    const projectId = projectIdRef.current;
    if (projectId === null) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    // 先持久化“建立简报修订”：服务端把 Intake 从 brief_review 重开为
    // confirmation，后续第 3 步的保存/采用请求才会被状态机接受。
    const intake = await beginBriefRevisionRequest(projectId);
    assertSessionActive(signal);
    intakeRef.current = intake;
    dispatch({ type: "begin_revision" });
  }, []);

  const candidateStatus = useCallback(
    (candidate: SessionWorkbenchCandidate) =>
      workbenchCandidateStatus(state, candidate),
    [state],
  );

  const resetSession = useCallback(() => {
    invalidateSession();
    projectIdRef.current = null;
    intakeRef.current = null;
    briefRef.current = null;
    setActiveProjectId(null);
    syncProjectPointer(null);
    dispatch({ type: "reset" });
  }, [invalidateSession, syncProjectPointer]);

  const stashRef = useRef<StashedSession | null>(null);
  const [stashAvailable, setStashAvailable] = useState(false);

  const stashCurrentSession = useCallback(() => {
    const current = stateRef.current;
    const projectId = projectIdRef.current;
    const intake = intakeRef.current;
    if (projectId === null || intake === null) return;
    const empty =
      !current.sourceText.trim() &&
      current.questions.length === 0 &&
      current.briefCandidates.length === 0;
    if (empty) return;
    stashRef.current = {
      projectId,
      intake,
      brief: briefRef.current,
      state: current,
    };
    setStashAvailable(true);
  }, []);

  const restoreStashedSession = useCallback(() => {
    const stashed = stashRef.current;
    if (!stashed) return;
    invalidateSession();
    projectIdRef.current = stashed.projectId;
    intakeRef.current = stashed.intake;
    briefRef.current = stashed.brief;
    setActiveProjectId(stashed.projectId);
    stashRef.current = null;
    setStashAvailable(false);
    syncProjectPointer(stashed.projectId);
    dispatch({ type: "patch", patch: stashed.state });
  }, [invalidateSession, syncProjectPointer]);

  const resumeTask = useCallback(async (
    projectId: number,
    task: TaskView,
    recoveryReview: BriefReview | null,
  ) => {
    const epoch = sessionEpochRef.current;
    const recoveryKey = `${epoch}:${task.task_run_id}`;
    if (!ACTIVE_TASK_STATUSES.has(task.status)) return;
    if (recoveringTaskIdsRef.current.has(recoveryKey)) return;
    recoveringTaskIdsRef.current.add(recoveryKey);
    const update = (latest: TaskView) => {
      if (sessionEpochRef.current !== epoch || projectIdRef.current !== projectId) return;
      dispatch({
        type: "task_updated",
        task: latest,
        error:
          latest.status === "failed"
            ? latest.failure?.message ?? "生成任务失败，Current Draft 未被修改。"
            : null,
      });
      if (latest.task_type !== "brief_to_draft") return;
      const strategy = CANDIDATE_SLOT_STRATEGIES.find(
        (candidateStrategy) => candidateStrategy === latest.candidate_strategy,
      );
      if (!strategy) return;
      if (!ACTIVE_TASK_STATUSES.has(latest.status)) {
        dispatch({
          type: "end_generation",
          status: latest.status === "cancelled" ? "idle" : "ready",
        });
      }
    };
    let terminalTask: TaskView | null = null;
    try {
      terminalTask = await waitForRecoveredTask(
        projectId, task.task_run_id, update, sessionReadControllerRef.current.signal,
      );
      if (terminalTask) update(terminalTask);
    } finally {
      recoveringTaskIdsRef.current.delete(recoveryKey);
    }
    if (
      sessionEpochRef.current === epoch &&
      task.task_type === "brief_to_draft" &&
      terminalTask &&
      !ACTIVE_TASK_STATUSES.has(terminalTask.status)
    ) {
      try {
        const candidates = await fetchDraftCandidates(projectId);
        if (sessionEpochRef.current === epoch && projectIdRef.current === projectId && recoveryReview) {
          dispatch({
            type: "patch",
            patch: mapAuthoritativeDraftCandidateState(
              candidates,
              recoveryReview,
              stateRef.current.previewCandidateId,
            ),
          });
        }
      } catch {
        // The terminal slot remains usable; an explicit reload retries project facts.
      }
    }
  }, []);

  const loadProject = useCallback(async (projectId: number) => {
    const epoch = invalidateSession();
    // Keep the previous form readable, but do not accept its operations while loading.
    sessionReadControllerRef.current.abort();
    dispatch({
      type: "patch",
      patch: { hydration: { status: "loading", error: null } },
    });
    try {
    const intake = await fetchCaseIntake(projectId);
    if (sessionEpochRef.current !== epoch) return;
    const mapped = mapIntakeToSessionState(intake);
    const pendingDecisions = intake.pending_decisions.map(
      (decision) => decision.prompt,
    );
    const sourceText = intake.current_source?.content_text ?? "";
    let brief: Awaited<ReturnType<typeof fetchBrief>> | null = null;
    let review: BriefReview | null = null;
    let step: IntakeStep = "idea";
    let furthestStep = 0;
    let frozenBriefVersion: number | null = null;
    let draftCandidates: SessionWorkbenchCandidate[] = [];
    let previewCandidateId: string | null = null;
    let adoptedCandidateId: string | null = null;
    const latestTasks: Partial<Record<TaskType, TaskView>> = {};
    await Promise.all(
      RECOVERABLE_TASK_TYPES.map(async (taskType) => {
        try {
          const task = await fetchLatestTask(projectId, taskType);
          if (task) latestTasks[taskType] = task;
        } catch {
          // A missing task record must not prevent the project itself from loading.
        }
      }),
    );
    if (sessionEpochRef.current !== epoch) return;
    if (intake.stage === "questions") {
      step = "questions";
      furthestStep = 1;
    } else if (intake.stage === "confirmation") {
      step = "confirmation";
      furthestStep = 2;
    } else if (intake.stage === "brief_review") {
      brief = await fetchBrief(projectId);
      if (sessionEpochRef.current !== epoch) return;
      review = mapBriefContentToReview(brief.content, pendingDecisions);
      const frozenVersionNo = brief.current_version_no ?? null;
      if (brief.current_version_id !== null && frozenVersionNo !== null) {
        // 简报已冻结：保留生成所需读模型，默认进入深稿候选。
        review = { ...review, dirty: false, saved: true };
        frozenBriefVersion = frozenVersionNo;
        step = "candidates";
        furthestStep = 3;
        const candidateViews = await fetchDraftCandidates(projectId);
        const candidateState = mapAuthoritativeDraftCandidateState(candidateViews, review, null);
        draftCandidates = candidateState.draftCandidates;
        adoptedCandidateId = candidateState.adoptedCandidateId;
        previewCandidateId = candidateState.previewCandidateId;
      } else {
        // brief_review 仅是后台生命周期；未冻结时仍恢复到可见的第 3 步。
        step = "confirmation";
        furthestStep = 2;
      }
    }
    if (sessionEpochRef.current !== epoch) return;
    sessionReadControllerRef.current = new AbortController();
    intakeRef.current = intake;
    briefRef.current = brief;
    projectIdRef.current = projectId;
    setActiveProjectId(projectId);
    const generationSlots = createCandidateSlots();
    const latestGenerationTask = latestTasks.brief_to_draft;
    const resumedStrategy = latestGenerationTask?.candidate_strategy;
    const selectedStrategy = CANDIDATE_SLOT_STRATEGIES.find(
      (strategy) => strategy === resumedStrategy,
    ) ?? null;
    if (latestGenerationTask && selectedStrategy) {
      generationSlots[selectedStrategy] = {
        status: candidateSlotStatusFromTask(latestGenerationTask),
        stage: candidateTaskStageFromTask(latestGenerationTask),
        taskRunId: latestGenerationTask.task_run_id,
        attempt: Math.max(1, latestGenerationTask.attempt_count),
        error:
          latestGenerationTask.status === "failed"
            ? latestGenerationTask.failure?.message ?? "生成任务失败"
            : null,
        latestTask: latestGenerationTask,
      };
    }
    dispatch({
      type: "patch",
      patch: {
        step,
        furthestStep,
        sourceText,
        review,
        workingBriefVersion: frozenBriefVersion ?? 1,
        frozenBriefVersion,
        draftCandidates,
        previewCandidateId,
        adoptedCandidateId,
        generation: {
          status:
            latestGenerationTask && ACTIVE_TASK_STATUSES.has(latestGenerationTask.status)
              ? "generating"
              : latestGenerationTask?.status === "cancelled"
                ? "idle"
              : latestGenerationTask
                ? "ready"
                : "idle",
          slots: generationSlots,
        },
        strategyAnalysis: {
          status: "idle",
          options: [],
          recommendedStrategy: null,
          recommendationReason: null,
          error: null,
        },
        selectedStrategy,
        latestTasks,
        hydration: { status: "ready", error: null },
        ...mapped,
        brief: mapped.brief ?? createEmptyBrief(sourceText),
      },
    });
    syncProjectPointer(projectId);
    for (const task of Object.values(latestTasks)) {
      if (task) void resumeTask(projectId, task, review);
    }
    } catch (error) {
      if (sessionEpochRef.current !== epoch) return;
      sessionReadControllerRef.current = new AbortController();
      const message = error instanceof Error ? error.message : "项目恢复失败，请重试。";
      projectIdRef.current = null;
      intakeRef.current = null;
      briefRef.current = null;
      setActiveProjectId(null);
      syncProjectPointer(null);
      dispatch({
        type: "patch",
        patch: { hydration: { status: "error", error: message } },
      });
      throw error;
    }
  }, [invalidateSession, resumeTask, syncProjectPointer]);

  const resumeGeneration = useCallback(
    async (strategy: CandidateSlotStrategy, onReload?: () => void) => {
      const epoch = sessionEpochRef.current;
      const signal = sessionReadControllerRef.current.signal;
      assertSessionActive(signal);
      const projectId = projectIdRef.current;
      const slot = stateRef.current.generation.slots[strategy];
      if (projectId === null || slot.taskRunId === null || !slot.latestTask) {
        return false;
      }
      const brief = briefRef.current ?? (await fetchBrief(projectId));
      if (sessionEpochRef.current !== epoch) return false;
      const draft = await fetchCaseDraft(projectId);
      if (sessionEpochRef.current !== epoch) return false;
      const resumed = await resumeDraftGenerationTask(
        projectId,
        slot.taskRunId,
        draft.draft_id,
        draft.revision,
        brief.draft_revision,
      );
      if (sessionEpochRef.current !== epoch) return false;
      dispatch({
        type: "task_updated",
        strategy,
        task: resumed,
        error: null,
      });
      let reloadEpoch: number | null = null;
      try {
        await waitForTask(projectId, resumed.task_run_id, (task) => {
          if (sessionEpochRef.current !== epoch) return;
          dispatch({
            type: "task_updated",
            strategy,
            task,
          });
        }, signal);
        if (sessionEpochRef.current !== epoch) return false;
        const loading = loadProject(projectId);
        reloadEpoch = sessionEpochRef.current;
        onReload?.();
        await loading;
        return sessionEpochRef.current === reloadEpoch;
      } catch (error) {
        if (sessionEpochRef.current !== (reloadEpoch ?? epoch)) return false;
        dispatch({
          type: "update_generation_slot",
          strategy,
          status: "failed",
          stage: "failed",
          error: error instanceof Error ? error.message : "恢复失败",
        });
        throw error;
      }
    },
    [loadProject],
  );

  const cancelGeneration = useCallback(
    async (strategy: CandidateSlotStrategy, onReload?: () => void) => {
      assertSessionActive(sessionReadControllerRef.current.signal);
      const epoch = sessionEpochRef.current;
      const projectId = projectIdRef.current;
      const slot = stateRef.current.generation.slots[strategy];
      if (
        projectId === null ||
        slot.taskRunId === null ||
        !slot.latestTask ||
        !ACTIVE_TASK_STATUSES.has(slot.latestTask.status)
      ) {
        return null;
      }
      const task = await cancelTask(projectId, slot.taskRunId);
      if (sessionEpochRef.current !== epoch) return null;
      dispatch({
        type: "task_updated",
        strategy,
        task,
        error:
          task.status === "failed"
            ? task.failure?.message ?? "生成任务已失败，Current Draft 未被修改。"
            : null,
      });
      if (task.status === "succeeded") {
        const loading = loadProject(projectId);
        onReload?.();
        await loading;
      } else if (task.status === "cancelled" || task.status === "failed") {
        dispatch({ type: "end_generation", status: "idle" });
      }
      return task;
    },
    [loadProject],
  );

  useEffect(() => {
    if (initialProjectLoadRef.current || typeof window === "undefined") return;
    initialProjectLoadRef.current = true;
    const rawProjectId = new URLSearchParams(window.location.search).get("project");
    if (!rawProjectId) {
      dispatch({
        type: "patch",
        patch: { hydration: { status: "ready", error: null } },
      });
      return;
    }
    const projectId = /^\d+$/.test(rawProjectId) ? Number(rawProjectId) : NaN;
    if (!Number.isSafeInteger(projectId) || projectId < 1) {
      dispatch({
        type: "patch",
        patch: {
          hydration: {
            status: "error",
            error: "项目地址无效，请从建案历史重新调出。",
          },
        },
      });
      syncProjectPointer(null);
      return;
    }
    const epoch = sessionEpochRef.current;
    const recoveryTimer = window.setTimeout(() => {
      if (sessionEpochRef.current !== epoch) return;
      void loadProject(projectId).catch(() => undefined);
    }, 0);
    return () => {
      window.clearTimeout(recoveryTimer);
      initialProjectLoadRef.current = false;
    };
  }, [loadProject, syncProjectPointer]);

  const activeCandidate = useMemo(() => {
    const activeId = state.previewCandidateId ?? state.adoptedCandidateId;
    return (
      state.draftCandidates.find((candidate) => candidate.id === activeId) ??
      null
    );
  }, [state.adoptedCandidateId, state.draftCandidates, state.previewCandidateId]);

  const getSessionEpoch = useCallback(() => sessionEpochRef.current, []);

  const value = useMemo<CaseSessionContextValue>(
    () => ({
      state,
      activeProjectId,
      activeCandidate,
      patchState,
      confirmBriefAndContinue,
      analyzeStrategies,
      selectStrategy,
      generateCandidates,
      resumeGeneration,
      cancelGeneration,
      retryTask,
      previewCandidate,
      adoptCandidate,
      beginBriefRevision,
      candidateStatus,
      resetSession,
      loadProject,
      getSessionEpoch,
      stashCurrentSession,
      restoreStashedSession,
      hasStashedSession: stashAvailable,
      submitPolish,
      adoptPolish,
      continueToQuestions,
      generateMoreQuestions,
      generateBriefFromAnswers,
      saveCandidateAsNew,
      createDialogueRevision,
      saveCandidateBookmark,
      activateCandidate,
      generateAuthorAnswer,
    }),
    [
      activeCandidate,
      activeProjectId,
      activateCandidate,
      analyzeStrategies,
      adoptCandidate,
      adoptPolish,
      beginBriefRevision,
      candidateStatus,
      continueToQuestions,
      confirmBriefAndContinue,
      createDialogueRevision,
      generateBriefFromAnswers,
      generateCandidates,
      resumeGeneration,
      cancelGeneration,
      generateMoreQuestions,
      generateAuthorAnswer,
      retryTask,
      loadProject,
      getSessionEpoch,
      patchState,
      previewCandidate,
      resetSession,
      restoreStashedSession,
      stashAvailable,
      stashCurrentSession,
      saveCandidateAsNew,
      saveCandidateBookmark,
      selectStrategy,
      state,
      submitPolish,
    ],
  );

  return (
    <CaseSessionContext.Provider value={value}>
      {children}
    </CaseSessionContext.Provider>
  );
}

export function useCaseSession() {
  const context = useContext(CaseSessionContext);
  if (!context) {
    throw new Error("CaseFile 页面必须位于 CaseSessionProvider 内。");
  }
  return context;
}
