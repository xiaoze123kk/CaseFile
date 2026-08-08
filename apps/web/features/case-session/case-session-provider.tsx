"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import type {
  BriefPolishResult,
  BriefStrategyOption,
  CandidateStrategy,
  ProviderName,
  TaskType,
  TaskView,
} from "@/lib/api-client";
import {
  buildWorkbenchCandidates,
  type WorkbenchCandidate,
} from "@/features/analyst-workbench/analyst-fixture";
import {
  canFreezeBriefReview,
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
  adoptDraftCandidate,
  answerQuestion,
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
  fetchTask,
  isBriefIntakeRevisionConflict,
  persistCaseSource,
  runTaskWithProviderFallback,
  saveBriefCandidate,
  startAnchorExtractTask,
  startDraftGenerationTask,
  startPolishTask,
  startQuestionsTask,
  startSynthesizeTask,
  startStrategyOptionsTask,
  strategyOptionsResult,
  updateBrief,
  waitForTask,
} from "./case-session-api";
import {
  briefsMatch,
  currentIntakeCandidate,
  mapBriefContentToReview,
  mapBriefToCandidateContent,
  mapWorkbenchCandidateView,
  mapIntakeToSessionState,
  mapReviewToBriefContent,
} from "./case-session-mapping";

export type GenerationStatus = "idle" | "generating" | "ready";
export type WorkbenchCandidateStatus = "pending" | "current" | "stale";
export type CandidateSlotStrategy = Exclude<CandidateStrategy, "balanced">;
export type CandidateSlotStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed";
export type CandidateTaskStage =
  | "queued"
  | "planning"
  | "processing"
  | "generating"
  | "validating"
  | "completed"
  | "failed";
export type StrategyAnalysisStatus = "idle" | "analyzing" | "ready" | "failed";
export type SessionHydrationStatus = "idle" | "loading" | "ready" | "error";

export const CANDIDATE_SLOT_STRATEGIES: readonly CandidateSlotStrategy[] = [
  "structure_first",
  "atmosphere_first",
  "reasoning_first",
];

const CANDIDATE_STRATEGY_TO_FOCUS = {
  structure_first: "structure",
  atmosphere_first: "atmosphere",
  reasoning_first: "reasoning",
} as const;

type CandidateSlot = {
  status: CandidateSlotStatus;
  stage: CandidateTaskStage;
  taskRunId: number | null;
  attempt: number;
  error: string | null;
};

export function candidateTaskStageFromTask(
  task: Pick<TaskView, "status" | "stage">,
): CandidateTaskStage {
  if (task.status === "succeeded" || task.stage === "completed") {
    return "completed";
  }
  if (task.status === "failed" || task.status === "cancelled" || task.stage === "failed") {
    return "failed";
  }
  if (task.stage === "planning") return "planning";
  if (task.stage === "generating") return "generating";
  if (task.stage === "validating") return "validating";
  if (task.stage === "queued" || task.stage === "preparing") return "queued";
  return "processing";
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
    stage: number;
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
  draftCandidates: WorkbenchCandidate[];
  previewCandidateId: string | null;
  adoptedCandidateId: string | null;
  latestTasks: Partial<Record<TaskType, TaskView>>;
}

type CaseSessionAction =
  | { type: "patch"; patch: Partial<CaseSessionState> }
  | { type: "set_review"; review: BriefReview }
  | { type: "save_review" }
  | { type: "freeze_review" }
  | { type: "start_generation"; strategies: CandidateSlotStrategy[] }
  | {
      type: "update_generation_slot";
      strategy: CandidateSlotStrategy;
      status: CandidateSlotStatus;
      stage?: CandidateTaskStage;
      taskRunId?: number | null;
      attempt?: number;
      error?: string | null;
    }
  | { type: "advance_generation"; stage: number }
  | {
      type: "strategy_analysis_ready";
      options: BriefStrategyOption[];
      recommendedStrategy: CandidateSlotStrategy;
      recommendationReason: string;
    }
  | { type: "select_strategy"; strategy: CandidateSlotStrategy }
  | { type: "complete_generation"; candidates: WorkbenchCandidate[] }
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
    generation: { status: "idle", stage: 0, slots: createCandidateSlots() },
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
  if (action.type === "set_review") {
    return { ...state, review: action.review };
  }
  if (action.type === "save_review") {
    if (!state.review) return state;
    return {
      ...state,
      review: { ...state.review, dirty: false, saved: true },
    };
  }
  if (action.type === "freeze_review") {
    if (!state.review || !canFreezeBriefReview(state.review)) return state;
    return {
      ...state,
      step: "candidates",
      furthestStep: Math.max(state.furthestStep, 4),
      frozenBriefVersion: state.workingBriefVersion,
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
      };
    }
    return { ...state, generation: { status: "generating", stage: 1, slots } };
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
          },
        },
      },
    };
  }
  if (action.type === "advance_generation") {
    if (state.generation.status !== "generating") return state;
    return {
      ...state,
      generation: {
        ...state.generation,
        status: "generating",
        stage: action.stage,
      },
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
      generation: { ...state.generation, status: "ready", stage: 3 },
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
    if (!candidate || candidate.briefVersion !== state.frozenBriefVersion) {
      return state;
    }
    return {
      ...state,
      adoptedCandidateId: candidate.id,
      previewCandidateId: candidate.id,
    };
  }
  if (action.type === "begin_revision") {
    return {
      ...state,
      step: "confirmation",
      brief: state.review
        ? mergeReviewIntoBrief(state.brief, state.review)
        : state.brief,
      workingBriefVersion: state.workingBriefVersion + 1,
      frozenBriefVersion: null,
      review: null,
      generation: { status: "idle", stage: 0, slots: createCandidateSlots() },
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
  candidate: WorkbenchCandidate,
): WorkbenchCandidateStatus {
  if (candidate.id === state.adoptedCandidateId) return "current";
  if (candidate.briefVersion === state.frozenBriefVersion) return "pending";
  return "stale";
}

export interface PolishResult {
  text: string;
  notes: string[];
  introducedDetails: string[];
  parentSourceRecordId: number | null;
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
  activeCandidate: WorkbenchCandidate | null;
  patchState: (patch: Partial<CaseSessionState>) => void;
  beginBriefReview: () => Promise<void>;
  setReview: (review: BriefReview) => void;
  saveReview: () => Promise<void>;
  freezeReview: () => Promise<boolean>;
  generateCandidates: (
    strategy?: CandidateSlotStrategy,
    attempt?: number,
  ) => Promise<boolean>;
  retryTask: (taskType: TaskType) => Promise<PolishResult | null>;
  analyzeStrategies: (refresh?: boolean) => Promise<boolean>;
  selectStrategy: (strategy: CandidateSlotStrategy) => void;
  previewCandidate: (candidateId: string | null) => void;
  adoptCandidate: (candidateId: string) => Promise<boolean>;
  beginBriefRevision: () => void;
  candidateStatus: (
    candidate: WorkbenchCandidate,
  ) => WorkbenchCandidateStatus;
  resetSession: () => void;
  loadProject: (projectId: number) => Promise<void>;
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
  createManualBrief: () => Promise<void>;
  saveCandidateAsNew: () => Promise<void>;
  createDialogueRevision: (instruction: string) => Promise<void>;
  saveCandidateBookmark: (candidateId: number) => Promise<void>;
  activateCandidate: (candidateId: number) => Promise<void>;
  reextractReview: () => Promise<BriefReview>;
}

const CaseSessionContext = createContext<CaseSessionContextValue | null>(
  null,
);

const RECOVERABLE_TASK_TYPES: readonly TaskType[] = [
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

export function CaseSessionProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    caseSessionReducer,
    undefined,
    createInitialCaseSessionState,
  );
  const [activeProjectId, setActiveProjectId] = useState<number | null>(null);
  const stateRef = useRef(state);
  const projectIdRef = useRef<number | null>(null);
  const intakeRef = useRef<Awaited<ReturnType<typeof fetchCaseIntake>> | null>(
    null,
  );
  const briefRef = useRef<Awaited<ReturnType<typeof fetchBrief>> | null>(
    null,
  );
  const recoveringTaskIdsRef = useRef(new Set<number>());
  const initialProjectLoadRef = useRef(false);

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

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  async function ensureProjectAndSource(
    text: string,
  ): Promise<Awaited<ReturnType<typeof fetchCaseIntake>>> {
    const normalized = text.trim();
    if (!normalized) throw new CaseSessionError("请先写下最初想法。");
    let intake = intakeRef.current;
    if (projectIdRef.current === null) {
      const project = await createCaseProject(text);
      projectIdRef.current = project.id;
      setActiveProjectId(project.id);
      syncProjectPointer(project.id);
      intake = await fetchCaseIntake(project.id);
      intakeRef.current = intake;
    } else if (!intake) {
      intake = await fetchCaseIntake(projectIdRef.current);
      intakeRef.current = intake;
    }
    if (intake.current_source?.content_text !== normalized) {
      intake = await persistCaseSource(
        projectIdRef.current,
        intake.revision,
        normalized,
      );
      intakeRef.current = intake;
    }
    return intake;
  }

  const patchState = useCallback((patch: Partial<CaseSessionState>) => {
    dispatch({ type: "patch", patch });
  }, []);

  const submitPolish = useCallback(
    async (mode: IntakePolishMode): Promise<PolishResult> => {
      const current = stateRef.current;
      const intake = await ensureProjectAndSource(current.sourceText);
      const sourceRecordId = intake.current_source?.source_record_id;
      if (!sourceRecordId) throw new CaseSessionError("起案原文尚未保存。");
      const { result: done } = await runTaskWithProviderFallback(
        async (provider) => {
          const task = await startPolishTask(
            projectIdRef.current!,
            sourceRecordId,
            provider,
            mode,
          );
          return waitForTask(projectIdRef.current!, task.task_run_id);
        },
      );
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
    [],
  );

  const adoptPolish = useCallback(
    async (draft: string, parentSourceRecordId: number | null) => {
      const intake = intakeRef.current;
      if (projectIdRef.current === null || !intake) {
        throw new CaseSessionError("当前会话尚未建案。");
      }
      intakeRef.current = await persistCaseSource(
        projectIdRef.current,
        intake.revision,
        draft.trim(),
        parentSourceRecordId,
      );
    },
    [],
  );

  const startWithFreshIntakeRevision = useCallback(
    async <T,>(operation: (revision: number) => Promise<T>): Promise<T> => {
      const projectId = projectIdRef.current;
      if (projectId === null) {
        throw new CaseSessionError("当前会话尚未建案。");
      }
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const fresh = await fetchCaseIntake(projectId);
        intakeRef.current = fresh;
        try {
          return await operation(fresh.revision);
        } catch (error) {
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

  const requestQuestions = useCallback(async (forceGeneration: boolean) => {
    const current = stateRef.current;
    let intake = await ensureProjectAndSource(current.sourceText);
    if (forceGeneration || intake.questions.length === 0) {
      await runTaskWithProviderFallback(async (provider) => {
        const task = await startWithFreshIntakeRevision((revision) =>
          startQuestionsTask(
            projectIdRef.current!,
            revision,
            provider,
          ),
        );
        return waitForTask(projectIdRef.current!, task.task_run_id);
      });
      intake = await fetchCaseIntake(projectIdRef.current!);
      intakeRef.current = intake;
    }
    const mapped = mapIntakeToSessionState(intake);
    dispatch({
      type: "patch",
      patch: {
        step: "questions",
        furthestStep: Math.max(current.furthestStep, 1),
        questions: mapped.questions,
        answers: mapped.answers,
      },
    });
  }, [startWithFreshIntakeRevision]);

  const continueToQuestions = useCallback(
    () => requestQuestions(false),
    [requestQuestions],
  );

  const generateMoreQuestions = useCallback(
    () => requestQuestions(true),
    [requestQuestions],
  );

  const generateBriefFromAnswers = useCallback(async () => {
    const current = stateRef.current;
    let intake =
      intakeRef.current ?? (await ensureProjectAndSource(current.sourceText));
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
        projectIdRef.current!,
        intake.revision,
        question.question_key,
        input,
      );
      intakeRef.current = intake;
    }
    await runTaskWithProviderFallback(async (provider) => {
      const task = await startWithFreshIntakeRevision((revision) =>
        startSynthesizeTask(
          projectIdRef.current!,
          revision,
          provider,
        ),
      );
      return waitForTask(projectIdRef.current!, task.task_run_id);
    });
    intakeRef.current = await fetchCaseIntake(projectIdRef.current!);
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
  }, [startWithFreshIntakeRevision]);

  const createManualBrief = useCallback(async () => {
    const current = stateRef.current;
    const intake =
      intakeRef.current ?? (await ensureProjectAndSource(current.sourceText));
    const content = mapBriefToCandidateContent(
      createEmptyBrief(current.sourceText),
    );
    intakeRef.current = await createBriefCandidate(
      projectIdRef.current!,
      intake.revision,
      content,
    );
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
  }, []);

  const saveCandidateAsNew = useCallback(async () => {
    const current = stateRef.current;
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    intakeRef.current = await createBriefCandidate(
      projectIdRef.current,
      intake.revision,
      mapBriefToCandidateContent(current.brief),
      intake.current_candidate_id,
    );
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({ type: "patch", patch: { ...mapped, brief: current.brief } });
  }, []);

  const createDialogueRevision = useCallback(async (instruction: string) => {
    const current = stateRef.current;
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    if (intake.current_candidate_id === null) {
      throw new CaseSessionError("先保存一个候选，再发起对话修改。");
    }
    const baseCandidateId = intake.current_candidate_id;
    await runTaskWithProviderFallback(async (provider) => {
      // 任务创建会推进 intake revision，回退重试前必须重取最新版本。
      const fresh = await fetchCaseIntake(projectIdRef.current!);
      const task = await startSynthesizeTask(
        projectIdRef.current!,
        fresh.revision,
        provider,
        baseCandidateId,
        instruction,
      );
      return waitForTask(projectIdRef.current!, task.task_run_id);
    });
    intakeRef.current = await fetchCaseIntake(projectIdRef.current);
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({ type: "patch", patch: { ...mapped, brief: current.brief } });
  }, []);

  const saveCandidateBookmark = useCallback(async (candidateId: number) => {
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    intakeRef.current = await saveBriefCandidate(
      projectIdRef.current,
      intake.revision,
      candidateId,
    );
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({
      type: "patch",
      patch: { briefCandidates: mapped.briefCandidates },
    });
  }, []);

  const activateCandidate = useCallback(async (candidateId: number) => {
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new CaseSessionError("当前会话尚未建案。");
    }
    intakeRef.current = await activateBriefCandidate(
      projectIdRef.current,
      intake.revision,
      candidateId,
    );
    const mapped = mapIntakeToSessionState(intakeRef.current);
    dispatch({
      type: "patch",
      patch: {
        ...mapped,
        brief: mapped.brief ?? stateRef.current.brief,
      },
    });
  }, []);

  const beginBriefReview = useCallback(async () => {
    const current = stateRef.current;
    const projectId = projectIdRef.current;
    if (projectId === null) {
      throw new CaseSessionError("请先完成最初想法与追问。");
    }
    let intake = intakeRef.current ?? (await fetchCaseIntake(projectId));
    const currentCandidate = currentIntakeCandidate(intake);
    let adoptedBrief: Awaited<ReturnType<typeof fetchBrief>>;
    if (
      intake.stage === "brief_review" &&
      currentCandidate &&
      briefsMatch(current.brief, currentCandidate.content)
    ) {
      // 简报已在服务端确认且内容未变，直接读取，避免重复采用。
      adoptedBrief = briefRef.current ?? (await fetchBrief(projectId));
    } else {
      let candidateId = intake.current_candidate_id;
      if (
        candidateId === null ||
        !currentCandidate ||
        !briefsMatch(current.brief, currentCandidate.content)
      ) {
        intake = await createBriefCandidate(
          projectId,
          intake.revision,
          mapBriefToCandidateContent(current.brief),
          candidateId,
        );
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
      intakeRef.current = adopted.intake;
      adoptedBrief = adopted.brief;
    }
    briefRef.current = adoptedBrief;
    const pendingDecisions = current.questions
      .filter((question) => current.answers[question.key]?.pending)
      .map((question) => question.prompt);
    const review = mapBriefContentToReview(
      adoptedBrief.content,
      pendingDecisions,
    );
    dispatch({ type: "set_review", review });
    dispatch({
      type: "patch",
      patch: {
        step: "review",
        furthestStep: Math.max(current.furthestStep, 3),
        brief: current.brief,
      },
    });
  }, []);

  const setReview = useCallback((review: BriefReview) => {
    dispatch({ type: "set_review", review });
  }, []);

  const saveReview = useCallback(async () => {
    const current = stateRef.current;
    if (!current.review) return;
    const projectId = projectIdRef.current;
    if (projectId === null) throw new CaseSessionError("当前会话尚未建案。");
    const brief = briefRef.current ?? (await fetchBrief(projectId));
    const content = mapReviewToBriefContent(
      current.review,
      current.brief,
      brief.content,
    );
    briefRef.current = await updateBrief(
      projectId,
      brief.draft_revision,
      content,
    );
    dispatch({ type: "save_review" });
  }, []);

  const reextractReview = useCallback(async () => {
    const current = stateRef.current;
    if (!current.review) throw new CaseSessionError("审阅尚未建立。");
    const projectId = projectIdRef.current;
    if (projectId === null) throw new CaseSessionError("当前会话尚未建案。");
    const brief = briefRef.current ?? (await fetchBrief(projectId));
    const { result: done } = await runTaskWithProviderFallback(
      async (provider) => {
        const task = await startAnchorExtractTask(
          projectId,
          brief.draft_revision,
          provider,
        );
        return waitForTask(projectId, task.task_run_id);
      },
    );
    const result = done.result as
      | { author_anchors: Array<{ statement: string }>; creative_constraints: Array<{ statement: string; suggested_strength: "hard" | "soft" }> }
      | null;
    if (!result) throw new CaseSessionError("拆解任务没有返回结果。");
    return {
      ...current.review,
      authorAnchors: result.author_anchors.map((anchor, index) => ({
        id: `anchor-agent-${index + 1}`,
        statement: anchor.statement,
        origin: "agent" as const,
      })),
      creativeConstraints: result.creative_constraints.map(
        (constraint, index) => ({
          id: `constraint-agent-${index + 1}`,
          statement: constraint.statement,
          strength: constraint.suggested_strength,
          origin: "agent" as const,
        }),
      ),
      dirty: true,
      saved: false,
    };
  }, []);

  const freezeReview = useCallback(async () => {
    const current = stateRef.current;
    if (!current.review || !canFreezeBriefReview(current.review)) return false;
    const projectId = projectIdRef.current;
    if (projectId === null) return false;
    // adopt 投影只产出边界原文、不产出原子约束；冻结前先持久化审阅，
    // 让服务端拿到原子项并通过 brief_creative_constraints_required 门禁。
    await saveReview();
    const brief = briefRef.current ?? (await fetchBrief(projectId));
    const version = await confirmBrief(projectId, brief.draft_revision);
    briefRef.current = await fetchBrief(projectId);
    dispatch({ type: "freeze_review" });
    dispatch({
      type: "patch",
      patch: {
        workingBriefVersion: version.version_no,
        frozenBriefVersion: version.version_no,
      },
    });
    return true;
  }, [saveReview]);

  const analyzeStrategies = useCallback(async (refresh = false) => {
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
      if (!brief.current_version_id) {
        throw new CaseSessionError("请先冻结当前创作简报。");
      }
      const { result: done } = await runTaskWithProviderFallback(
        async (provider) => {
          const task = await startStrategyOptionsTask(
            projectId,
            brief.current_version_id!,
            provider,
            refresh,
          );
          return waitForTask(projectId, task.task_run_id);
        },
      );
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
    const current = stateRef.current;
    const selectedStrategy = requestedStrategy ?? current.selectedStrategy;
    if (
      !current.review ||
      current.frozenBriefVersion === null ||
      !selectedStrategy ||
      current.generation.status === "generating"
    ) {
      return false;
    }
    const projectId = projectIdRef.current;
    if (projectId === null) return false;
    try {
      const brief = briefRef.current ?? (await fetchBrief(projectId));
      if (!brief.current_version_id) {
        throw new CaseSessionError("请先冻结当前创作简报。");
      }
      const existingCandidates = await fetchDraftCandidates(projectId);
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
      const missingStrategies = requestedStrategies.filter(
        (strategy) => !existingStrategies.has(strategy),
      );
      for (const strategy of existingStrategies) {
        dispatch({
          type: "update_generation_slot",
          strategy,
          status: "succeeded",
          stage: "completed",
        });
      }
      const draft = await fetchCaseDraft(projectId);
      dispatch({ type: "start_generation", strategies: missingStrategies });
      let workingProvider: ProviderName | null = null;

      if (missingStrategies.length > 0) {
        // 首个缺失槽位负责 Provider 认证回退；成功后复用于其余槽位。
        const firstStrategy = missingStrategies.includes("structure_first")
          ? "structure_first"
          : missingStrategies[0];
        const fallbackResult = await runTaskWithProviderFallback(
          async (provider) => {
            let taskRunId: number | null = null;
            try {
              const task = await startDraftGenerationTask(
                projectId,
                brief.current_version_id!,
                draft.revision,
                provider,
                firstStrategy,
                selectedStrategy === firstStrategy ? requestedAttempt : 1,
              );
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
                dispatch({
                  type: "update_generation_slot",
                  strategy: firstStrategy,
                  status:
                    latestTask.status === "succeeded"
                      ? "succeeded"
                      : latestTask.status === "failed" || latestTask.status === "cancelled"
                        ? "failed"
                        : "running",
                  stage: candidateTaskStageFromTask(latestTask),
                });
              });
              dispatch({
                type: "update_generation_slot",
                strategy: firstStrategy,
                status: "succeeded",
                stage: "completed",
              });
              dispatch({ type: "advance_generation", stage: 2 });
              return provider;
            } catch (error) {
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
        workingProvider = fallbackResult.provider;

        const parallelStrategies = missingStrategies.filter(
          (strategy) => strategy !== firstStrategy,
        );
        await Promise.allSettled(
          parallelStrategies.map(async (strategy) => {
            let taskRunId: number | null = null;
            try {
              const task = await startDraftGenerationTask(
                projectId,
                brief.current_version_id!,
                draft.revision,
                workingProvider!,
                strategy,
                1,
              );
              taskRunId = task.task_run_id;
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "running",
                stage: "queued",
                taskRunId,
              });
              await waitForTask(projectId, task.task_run_id, (latestTask) => {
                dispatch({
                  type: "update_generation_slot",
                  strategy,
                  status:
                    latestTask.status === "succeeded"
                      ? "succeeded"
                      : latestTask.status === "failed" || latestTask.status === "cancelled"
                        ? "failed"
                        : "running",
                  stage: candidateTaskStageFromTask(latestTask),
                });
              });
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "succeeded",
                stage: "completed",
              });
              dispatch({ type: "advance_generation", stage: 3 });
            } catch (error) {
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "failed",
                stage: "failed",
                taskRunId,
                error: error instanceof Error ? error.message : "生成失败",
              });
            }
          }),
        );
      }

      const candidates = await fetchDraftCandidates(projectId);
      let refreshedCurrentOnes = candidates.filter(
        (candidate) => candidate.is_current_brief,
      );
      const rankStrategy = (strategy: CandidateStrategy) =>
        strategy === "structure_first"
          ? 0
          : strategy === "atmosphere_first"
            ? 1
            : strategy === "reasoning_first"
              ? 2
              : 3;
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
                  rankStrategy(left.candidate_strategy) -
                  rankStrategy(right.candidate_strategy),
              )
              .slice(1)
              .map((candidate) => candidate.candidate_strategy as CandidateSlotStrategy);
          }),
        ),
      ];
      if (workingProvider && duplicateRetryStrategies.length > 0) {
        await Promise.allSettled(
          duplicateRetryStrategies.map(async (strategy) => {
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
                draft.revision,
                workingProvider!,
                strategy,
                2,
              );
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
                dispatch({
                  type: "update_generation_slot",
                  strategy,
                  status:
                    latestTask.status === "succeeded"
                      ? "succeeded"
                      : latestTask.status === "failed" || latestTask.status === "cancelled"
                        ? "failed"
                        : "running",
                  stage: candidateTaskStageFromTask(latestTask),
                });
              });
              dispatch({
                type: "update_generation_slot",
                strategy,
                status: "succeeded",
                stage: "completed",
                attempt: 2,
              });
            } catch (error) {
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
        refreshedCurrentOnes = (await fetchDraftCandidates(projectId)).filter(
          (candidate) => candidate.is_current_brief,
        );
      }
      const duplicateHashCounts = new Map<string, number>();
      for (const candidate of refreshedCurrentOnes) {
        duplicateHashCounts.set(
          candidate.content_hash,
          (duplicateHashCounts.get(candidate.content_hash) ?? 0) + 1,
        );
      }
      const base = buildWorkbenchCandidates(
        {
          creativeIntent: current.review.creativeIntent,
          reasoningProposition: current.review.reasoningProposition,
          authorAnswer: current.review.authorAnswer,
          constraints: current.review.creativeConstraints
            .map((constraint) => constraint.statement.trim())
            .filter(Boolean),
        },
        current.frozenBriefVersion,
      );
      const mapped = [...refreshedCurrentOnes]
        .sort(
          (left, right) =>
            rankStrategy(left.candidate_strategy) -
            rankStrategy(right.candidate_strategy),
        )
        .map((view) => {
          const focus =
            view.candidate_strategy === "balanced"
              ? "structure"
              : CANDIDATE_STRATEGY_TO_FOCUS[view.candidate_strategy];
          return mapWorkbenchCandidateView(
            view,
            base.find((candidate) => candidate.focus === focus) ?? base[0],
            duplicateHashCounts.get(view.content_hash)! > 1
              ? "与同批候选内容相同，差异不足"
              : undefined,
          );
        });
      dispatch({ type: "complete_generation", candidates: mapped });
      return refreshedCurrentOnes.some(
        (candidate) => candidate.candidate_strategy === selectedStrategy,
      );
    } catch (error) {
      const generation = stateRef.current.generation;
      dispatch({
        type: "patch",
        patch: { generation: { ...generation, status: "idle", stage: 0 } },
      });
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
      const current = stateRef.current;
      const refreshLatest = async () => {
        const projectId = projectIdRef.current;
        if (projectId === null) return;
        let latest: TaskView | null = null;
        try {
          latest = await fetchLatestTask(projectId, taskType);
        } catch {
          // The retry request has already been submitted; a refresh failure should not
          // turn a successful retry into a false error state in the UI.
          return;
        }
        if (!latest) return;
        dispatch({
          type: "patch",
          patch: {
            latestTasks: {
              ...stateRef.current.latestTasks,
              [taskType]: latest,
            },
          },
        });
      };
      switch (taskType) {
        case "brief_polish": {
          const result = await submitPolish(current.polishMode);
          await refreshLatest();
          return result;
        }
        case "brief_intake_questions":
          await requestQuestions(true);
          await refreshLatest();
          return null;
        case "brief_intake_synthesize":
          await generateBriefFromAnswers();
          await refreshLatest();
          return null;
        case "brief_anchor_extract": {
          const review = await reextractReview();
          dispatch({ type: "set_review", review });
          await refreshLatest();
          return null;
        }
        case "brief_strategy_options":
          await analyzeStrategies(true);
          await refreshLatest();
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
          await refreshLatest();
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
      reextractReview,
      requestQuestions,
      submitPolish,
    ],
  );

  const adoptCandidate = useCallback(async (candidateId: string) => {
    const current = stateRef.current;
    const candidate = current.draftCandidates.find(
      (item) => item.id === candidateId,
    );
    if (!candidate || candidate.briefVersion !== current.frozenBriefVersion) {
      return false;
    }
    const projectId = projectIdRef.current;
    if (projectId === null) {
      // 无后端会话（纯 fixture 接力场景）时只在会话内采用。
      dispatch({ type: "adopt_candidate", candidateId });
      return true;
    }
    const taskRunId = Number(candidateId.replace(/^draft-/, ""));
    if (!Number.isInteger(taskRunId)) return false;
    const draft = await fetchCaseDraft(projectId);
    await adoptDraftCandidate(projectId, taskRunId, draft.revision);
    briefRef.current = await fetchBrief(projectId);
    dispatch({ type: "adopt_candidate", candidateId });
    return true;
  }, []);

  const beginBriefRevision = useCallback(() => {
    dispatch({ type: "begin_revision" });
  }, []);

  const candidateStatus = useCallback(
    (candidate: WorkbenchCandidate) =>
      workbenchCandidateStatus(state, candidate),
    [state],
  );

  const resetSession = useCallback(() => {
    projectIdRef.current = null;
    intakeRef.current = null;
    briefRef.current = null;
    setActiveProjectId(null);
    syncProjectPointer(null);
    dispatch({ type: "reset" });
  }, [syncProjectPointer]);

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
    projectIdRef.current = stashed.projectId;
    intakeRef.current = stashed.intake;
    briefRef.current = stashed.brief;
    setActiveProjectId(stashed.projectId);
    stashRef.current = null;
    setStashAvailable(false);
    syncProjectPointer(stashed.projectId);
    dispatch({ type: "patch", patch: stashed.state });
  }, [syncProjectPointer]);

  const resumeTask = useCallback(async (projectId: number, task: TaskView) => {
    if (!ACTIVE_TASK_STATUSES.has(task.status)) return;
    if (recoveringTaskIdsRef.current.has(task.task_run_id)) return;
    recoveringTaskIdsRef.current.add(task.task_run_id);
    const update = (latest: TaskView) => {
      if (projectIdRef.current !== projectId) return;
      dispatch({
        type: "patch",
        patch: {
          latestTasks: {
            ...stateRef.current.latestTasks,
            [latest.task_type]: latest,
          },
        },
      });
    };
    try {
      await waitForTask(projectId, task.task_run_id, update);
    } catch {
      try {
        update(await fetchTask(projectId, task.task_run_id));
      } catch {
        // The next explicit action will surface the authoritative API error.
      }
    } finally {
      recoveringTaskIdsRef.current.delete(task.task_run_id);
    }
  }, []);

  const loadProject = useCallback(async (projectId: number) => {
    dispatch({
      type: "patch",
      patch: { hydration: { status: "loading", error: null } },
    });
    try {
    const intake = await fetchCaseIntake(projectId);
    intakeRef.current = intake;
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
    let draftCandidates: WorkbenchCandidate[] = [];
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
    if (intake.stage === "questions") {
      step = "questions";
      furthestStep = 1;
    } else if (intake.stage === "confirmation") {
      step = "confirmation";
      furthestStep = 2;
    } else if (intake.stage === "brief_review") {
      brief = await fetchBrief(projectId);
      briefRef.current = brief;
      review = mapBriefContentToReview(brief.content, pendingDecisions);
      const frozenVersionNo = brief.current_version_no ?? null;
      if (brief.current_version_id !== null && frozenVersionNo !== null) {
        // 简报已冻结：回到候选稿步骤，恢复工作稿列表与采用状态。
        frozenBriefVersion = frozenVersionNo;
        step = "candidates";
        furthestStep = 4;
        const currentOnes = (await fetchDraftCandidates(projectId)).filter(
          (candidate) => candidate.is_current_brief,
        );
        const rankStrategy = (strategy: CandidateStrategy) =>
          strategy === "structure_first"
            ? 0
            : strategy === "atmosphere_first"
              ? 1
              : strategy === "reasoning_first"
                ? 2
                : 3;
        const base = buildWorkbenchCandidates(
          {
            creativeIntent: review.creativeIntent,
            reasoningProposition: review.reasoningProposition,
            authorAnswer: review.authorAnswer,
            constraints: review.creativeConstraints
              .map((constraint) => constraint.statement.trim())
              .filter(Boolean),
          },
          frozenBriefVersion,
        );
        draftCandidates = [...currentOnes]
          .sort(
            (left, right) =>
              rankStrategy(left.candidate_strategy) -
              rankStrategy(right.candidate_strategy),
          )
          .map((view) => {
            const focus =
              view.candidate_strategy === "balanced"
                ? "structure"
                : CANDIDATE_STRATEGY_TO_FOCUS[view.candidate_strategy];
            return mapWorkbenchCandidateView(
              view,
              base.find((candidate) => candidate.focus === focus) ?? base[0],
            );
          });
        const adopted = currentOnes.find((candidate) => candidate.is_adopted);
        if (adopted) {
          adoptedCandidateId = `draft-${adopted.task_run_id}`;
          previewCandidateId = adoptedCandidateId;
        }
      } else {
        step = "review";
        furthestStep = 3;
      }
    }
    projectIdRef.current = projectId;
    setActiveProjectId(projectId);
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
        generation: { status: "idle", stage: 0, slots: createCandidateSlots() },
        strategyAnalysis: {
          status: "idle",
          options: [],
          recommendedStrategy: null,
          recommendationReason: null,
          error: null,
        },
        selectedStrategy: null,
        latestTasks,
        hydration: { status: "ready", error: null },
        ...mapped,
        brief: mapped.brief ?? createEmptyBrief(sourceText),
      },
    });
    projectIdRef.current = projectId;
    syncProjectPointer(projectId);
    for (const task of Object.values(latestTasks)) {
      if (task) void resumeTask(projectId, task);
    }
    } catch (error) {
      const message = error instanceof Error ? error.message : "项目恢复失败，请重试。";
      projectIdRef.current = null;
      setActiveProjectId(null);
      syncProjectPointer(null);
      dispatch({
        type: "patch",
        patch: { hydration: { status: "error", error: message } },
      });
      throw error;
    }
  }, [resumeTask, syncProjectPointer]);

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
    const recoveryTimer = window.setTimeout(() => {
      void loadProject(projectId).catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(recoveryTimer);
  }, [loadProject, syncProjectPointer]);

  const activeCandidate = useMemo(() => {
    const activeId = state.previewCandidateId ?? state.adoptedCandidateId;
    return (
      state.draftCandidates.find((candidate) => candidate.id === activeId) ??
      null
    );
  }, [state.adoptedCandidateId, state.draftCandidates, state.previewCandidateId]);

  const value = useMemo<CaseSessionContextValue>(
    () => ({
      state,
      activeProjectId,
      activeCandidate,
      patchState,
      beginBriefReview,
      setReview,
      saveReview,
      freezeReview,
      analyzeStrategies,
      selectStrategy,
      generateCandidates,
      retryTask,
      previewCandidate,
      adoptCandidate,
      beginBriefRevision,
      candidateStatus,
      resetSession,
      loadProject,
      stashCurrentSession,
      restoreStashedSession,
      hasStashedSession: stashAvailable,
      submitPolish,
      adoptPolish,
      continueToQuestions,
      generateMoreQuestions,
      generateBriefFromAnswers,
      createManualBrief,
      saveCandidateAsNew,
      createDialogueRevision,
      saveCandidateBookmark,
      activateCandidate,
      reextractReview,
    }),
    [
      activeCandidate,
      activeProjectId,
      activateCandidate,
      analyzeStrategies,
      adoptCandidate,
      adoptPolish,
      beginBriefReview,
      beginBriefRevision,
      candidateStatus,
      continueToQuestions,
      createDialogueRevision,
      createManualBrief,
      freezeReview,
      generateBriefFromAnswers,
      generateCandidates,
      generateMoreQuestions,
      retryTask,
      loadProject,
      patchState,
      previewCandidate,
      resetSession,
      reextractReview,
      restoreStashedSession,
      stashAvailable,
      stashCurrentSession,
      saveCandidateAsNew,
      saveCandidateBookmark,
      saveReview,
      selectStrategy,
      setReview,
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
