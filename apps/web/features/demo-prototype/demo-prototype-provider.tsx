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
} from "react";

import type { BriefPolishResult } from "@/lib/api-client";
import {
  buildPrototypeDraftCandidates,
  type PrototypeDraftCandidate,
} from "@/features/analyst-workbench/analyst-fixture";
import {
  canFreezeBriefReview,
  createEmptyBrief,
  mergeReviewIntoBrief,
  type PrototypeAnswer,
  type PrototypeBrief,
  type PrototypeBriefReview,
  type PrototypeCandidate,
  type PrototypePolishMode,
  type PrototypeQuestion,
  type PrototypeStep,
} from "@/features/intake-prototype/intake-prototype-model";

import {
  activateDemoCandidate,
  adoptDemoCandidate,
  adoptDemoDraftCandidate,
  answerDemoQuestion,
  confirmDemoBrief,
  createDemoCandidate,
  createDemoProject,
  DemoIntakeError,
  type DemoQuestionAnswerInput,
  fetchDemoBrief,
  fetchDemoDraftCandidates,
  fetchDemoIntake,
  persistDemoSource,
  runTaskWithProviderFallback,
  saveDemoCandidate,
  startDemoAnchorExtract,
  startDemoDraftRun,
  startDemoPolish,
  startDemoQuestions,
  startDemoSynthesize,
  updateDemoBrief,
  waitForDemoTask,
} from "./demo-intake-api";
import {
  briefsMatch,
  currentIntakeCandidate,
  mapBriefContentToReview,
  mapBriefToCandidateContent,
  mapDraftCandidateView,
  mapIntakeToDemoState,
  mapReviewToBriefContent,
} from "./demo-intake-mapping";

export type PrototypeGenerationStatus = "idle" | "generating" | "ready";
export type PrototypeDraftCandidateStatus = "pending" | "current" | "stale";

export interface DemoPrototypeState {
  step: PrototypeStep;
  furthestStep: number;
  sourceText: string;
  polishMode: PrototypePolishMode;
  questions: PrototypeQuestion[];
  answers: Record<string, PrototypeAnswer>;
  brief: PrototypeBrief;
  briefCandidates: PrototypeCandidate[];
  currentBriefCandidateId: number | null;
  review: PrototypeBriefReview | null;
  workingBriefVersion: number;
  frozenBriefVersion: number | null;
  generation: {
    status: PrototypeGenerationStatus;
    stage: number;
  };
  draftCandidates: PrototypeDraftCandidate[];
  previewCandidateId: string | null;
  adoptedCandidateId: string | null;
}

type DemoPrototypeAction =
  | { type: "patch"; patch: Partial<DemoPrototypeState> }
  | { type: "set_review"; review: PrototypeBriefReview }
  | { type: "save_review" }
  | { type: "freeze_review" }
  | { type: "start_generation" }
  | { type: "advance_generation"; stage: number }
  | { type: "complete_generation"; candidates: PrototypeDraftCandidate[] }
  | { type: "preview_candidate"; candidateId: string | null }
  | { type: "adopt_candidate"; candidateId: string }
  | { type: "begin_revision" }
  | { type: "reset" };

export function createInitialDemoPrototypeState(): DemoPrototypeState {
  return {
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
    generation: { status: "idle", stage: 0 },
    draftCandidates: [],
    previewCandidateId: null,
    adoptedCandidateId: null,
  };
}

export function demoPrototypeReducer(
  state: DemoPrototypeState,
  action: DemoPrototypeAction,
): DemoPrototypeState {
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
    return { ...state, generation: { status: "generating", stage: 1 } };
  }
  if (action.type === "advance_generation") {
    if (state.generation.status !== "generating") return state;
    return {
      ...state,
      generation: { status: "generating", stage: action.stage },
    };
  }
  if (action.type === "complete_generation") {
    return {
      ...state,
      generation: { status: "ready", stage: 3 },
      draftCandidates: [...state.draftCandidates, ...action.candidates],
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
      generation: { status: "idle", stage: 0 },
      previewCandidateId: null,
    };
  }
  return createInitialDemoPrototypeState();
}

export function prototypeDraftCandidateStatus(
  state: DemoPrototypeState,
  candidate: PrototypeDraftCandidate,
): PrototypeDraftCandidateStatus {
  if (candidate.id === state.adoptedCandidateId) return "current";
  if (candidate.briefVersion === state.frozenBriefVersion) return "pending";
  return "stale";
}

export interface DemoPolishResult {
  text: string;
  notes: string[];
  introducedDetails: string[];
  parentSourceRecordId: number | null;
}

interface DemoPrototypeContextValue {
  state: DemoPrototypeState;
  activeCandidate: PrototypeDraftCandidate | null;
  patchState: (patch: Partial<DemoPrototypeState>) => void;
  beginBriefReview: () => Promise<void>;
  setReview: (review: PrototypeBriefReview) => void;
  saveReview: () => Promise<void>;
  freezeReview: () => Promise<boolean>;
  generateCandidates: () => Promise<boolean>;
  previewCandidate: (candidateId: string | null) => void;
  adoptCandidate: (candidateId: string) => Promise<boolean>;
  beginBriefRevision: () => void;
  candidateStatus: (
    candidate: PrototypeDraftCandidate,
  ) => PrototypeDraftCandidateStatus;
  resetPrototype: () => void;
  submitPolish: (mode: PrototypePolishMode) => Promise<DemoPolishResult>;
  adoptPolish: (
    draft: string,
    parentSourceRecordId: number | null,
  ) => Promise<void>;
  continueToQuestions: () => Promise<void>;
  generateBriefFromAnswers: () => Promise<void>;
  createManualBrief: () => Promise<void>;
  saveCandidateAsNew: () => Promise<void>;
  createDialogueRevision: (instruction: string) => Promise<void>;
  saveCandidateBookmark: (candidateId: number) => Promise<void>;
  activateCandidate: (candidateId: number) => Promise<void>;
  reextractReview: () => Promise<PrototypeBriefReview>;
}

const DemoPrototypeContext = createContext<DemoPrototypeContextValue | null>(
  null,
);

export function DemoPrototypeProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    demoPrototypeReducer,
    undefined,
    createInitialDemoPrototypeState,
  );
  const stateRef = useRef(state);
  const projectIdRef = useRef<number | null>(null);
  const intakeRef = useRef<Awaited<ReturnType<typeof fetchDemoIntake>> | null>(
    null,
  );
  const briefRef = useRef<Awaited<ReturnType<typeof fetchDemoBrief>> | null>(
    null,
  );

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  async function ensureProjectAndSource(
    text: string,
  ): Promise<Awaited<ReturnType<typeof fetchDemoIntake>>> {
    const normalized = text.trim();
    if (!normalized) throw new DemoIntakeError("请先写下最初想法。");
    let intake = intakeRef.current;
    if (projectIdRef.current === null) {
      const project = await createDemoProject(text);
      projectIdRef.current = project.id;
      intake = await fetchDemoIntake(project.id);
      intakeRef.current = intake;
    } else if (!intake) {
      intake = await fetchDemoIntake(projectIdRef.current);
      intakeRef.current = intake;
    }
    if (intake.current_source?.content_text !== normalized) {
      intake = await persistDemoSource(
        projectIdRef.current,
        intake.revision,
        normalized,
      );
      intakeRef.current = intake;
    }
    return intake;
  }

  const patchState = useCallback((patch: Partial<DemoPrototypeState>) => {
    dispatch({ type: "patch", patch });
  }, []);

  const submitPolish = useCallback(
    async (mode: PrototypePolishMode): Promise<DemoPolishResult> => {
      const current = stateRef.current;
      const intake = await ensureProjectAndSource(current.sourceText);
      const sourceRecordId = intake.current_source?.source_record_id;
      if (!sourceRecordId) throw new DemoIntakeError("起案原文尚未保存。");
      const { result: done } = await runTaskWithProviderFallback(
        async (provider) => {
          const task = await startDemoPolish(
            projectIdRef.current!,
            sourceRecordId,
            provider,
            mode,
          );
          return waitForDemoTask(projectIdRef.current!, task.task_run_id);
        },
      );
      const result = done.result as BriefPolishResult | null;
      if (!result) throw new DemoIntakeError("润色任务没有返回结果。");
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
        throw new DemoIntakeError("当前会话尚未建案。");
      }
      intakeRef.current = await persistDemoSource(
        projectIdRef.current,
        intake.revision,
        draft.trim(),
        parentSourceRecordId,
      );
    },
    [],
  );

  const continueToQuestions = useCallback(async () => {
    const current = stateRef.current;
    let intake = await ensureProjectAndSource(current.sourceText);
    if (intake.questions.length === 0) {
      await runTaskWithProviderFallback(async (provider) => {
        // 任务创建会推进 intake revision，回退重试前必须重取最新版本。
        const fresh = await fetchDemoIntake(projectIdRef.current!);
        const task = await startDemoQuestions(
          projectIdRef.current!,
          fresh.revision,
          provider,
        );
        return waitForDemoTask(projectIdRef.current!, task.task_run_id);
      });
      intake = await fetchDemoIntake(projectIdRef.current!);
      intakeRef.current = intake;
    }
    const mapped = mapIntakeToDemoState(intake);
    dispatch({
      type: "patch",
      patch: {
        step: "questions",
        furthestStep: Math.max(current.furthestStep, 1),
        questions: mapped.questions,
        answers: mapped.answers,
      },
    });
  }, []);

  const generateBriefFromAnswers = useCallback(async () => {
    const current = stateRef.current;
    let intake =
      intakeRef.current ?? (await ensureProjectAndSource(current.sourceText));
    for (const question of intake.questions) {
      const answer = current.answers[question.question_key];
      if (!answer) continue;
      let input: DemoQuestionAnswerInput;
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
      intake = await answerDemoQuestion(
        projectIdRef.current!,
        intake.revision,
        question.question_key,
        input,
      );
      intakeRef.current = intake;
    }
    await runTaskWithProviderFallback(async (provider) => {
      // 任务创建会推进 intake revision，回退重试前必须重取最新版本。
      const fresh = await fetchDemoIntake(projectIdRef.current!);
      const task = await startDemoSynthesize(
        projectIdRef.current!,
        fresh.revision,
        provider,
      );
      return waitForDemoTask(projectIdRef.current!, task.task_run_id);
    });
    intakeRef.current = await fetchDemoIntake(projectIdRef.current!);
    const mapped = mapIntakeToDemoState(intakeRef.current);
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

  const createManualBrief = useCallback(async () => {
    const current = stateRef.current;
    const intake =
      intakeRef.current ?? (await ensureProjectAndSource(current.sourceText));
    const content = mapBriefToCandidateContent(
      createEmptyBrief(current.sourceText),
    );
    intakeRef.current = await createDemoCandidate(
      projectIdRef.current!,
      intake.revision,
      content,
    );
    const mapped = mapIntakeToDemoState(intakeRef.current);
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
      throw new DemoIntakeError("当前会话尚未建案。");
    }
    intakeRef.current = await createDemoCandidate(
      projectIdRef.current,
      intake.revision,
      mapBriefToCandidateContent(current.brief),
      intake.current_candidate_id,
    );
    const mapped = mapIntakeToDemoState(intakeRef.current);
    dispatch({ type: "patch", patch: { ...mapped, brief: current.brief } });
  }, []);

  const createDialogueRevision = useCallback(async (instruction: string) => {
    const current = stateRef.current;
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new DemoIntakeError("当前会话尚未建案。");
    }
    if (intake.current_candidate_id === null) {
      throw new DemoIntakeError("先保存一个候选，再发起对话修改。");
    }
    const baseCandidateId = intake.current_candidate_id;
    await runTaskWithProviderFallback(async (provider) => {
      // 任务创建会推进 intake revision，回退重试前必须重取最新版本。
      const fresh = await fetchDemoIntake(projectIdRef.current!);
      const task = await startDemoSynthesize(
        projectIdRef.current!,
        fresh.revision,
        provider,
        baseCandidateId,
        instruction,
      );
      return waitForDemoTask(projectIdRef.current!, task.task_run_id);
    });
    intakeRef.current = await fetchDemoIntake(projectIdRef.current);
    const mapped = mapIntakeToDemoState(intakeRef.current);
    dispatch({ type: "patch", patch: { ...mapped, brief: current.brief } });
  }, []);

  const saveCandidateBookmark = useCallback(async (candidateId: number) => {
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new DemoIntakeError("当前会话尚未建案。");
    }
    intakeRef.current = await saveDemoCandidate(
      projectIdRef.current,
      intake.revision,
      candidateId,
    );
    const mapped = mapIntakeToDemoState(intakeRef.current);
    dispatch({
      type: "patch",
      patch: { briefCandidates: mapped.briefCandidates },
    });
  }, []);

  const activateCandidate = useCallback(async (candidateId: number) => {
    const intake = intakeRef.current;
    if (projectIdRef.current === null || !intake) {
      throw new DemoIntakeError("当前会话尚未建案。");
    }
    intakeRef.current = await activateDemoCandidate(
      projectIdRef.current,
      intake.revision,
      candidateId,
    );
    const mapped = mapIntakeToDemoState(intakeRef.current);
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
      throw new DemoIntakeError("请先完成最初想法与追问。");
    }
    let intake = intakeRef.current ?? (await fetchDemoIntake(projectId));
    const currentCandidate = currentIntakeCandidate(intake);
    let adoptedBrief: Awaited<ReturnType<typeof fetchDemoBrief>>;
    if (
      intake.stage === "brief_review" &&
      currentCandidate &&
      briefsMatch(current.brief, currentCandidate.content)
    ) {
      // 简报已在服务端确认且内容未变，直接读取，避免重复采用。
      adoptedBrief = briefRef.current ?? (await fetchDemoBrief(projectId));
    } else {
      let candidateId = intake.current_candidate_id;
      if (
        candidateId === null ||
        !currentCandidate ||
        !briefsMatch(current.brief, currentCandidate.content)
      ) {
        intake = await createDemoCandidate(
          projectId,
          intake.revision,
          mapBriefToCandidateContent(current.brief),
          candidateId,
        );
        intakeRef.current = intake;
        candidateId = intake.current_candidate_id;
      }
      if (candidateId === null) {
        throw new DemoIntakeError("当前还没有可采用的候选简报。");
      }
      const adopted = await adoptDemoCandidate(
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

  const setReview = useCallback((review: PrototypeBriefReview) => {
    dispatch({ type: "set_review", review });
  }, []);

  const saveReview = useCallback(async () => {
    const current = stateRef.current;
    if (!current.review) return;
    const projectId = projectIdRef.current;
    if (projectId === null) throw new DemoIntakeError("当前会话尚未建案。");
    const brief = briefRef.current ?? (await fetchDemoBrief(projectId));
    const content = mapReviewToBriefContent(
      current.review,
      current.brief,
      brief.content,
    );
    briefRef.current = await updateDemoBrief(
      projectId,
      brief.draft_revision,
      content,
    );
    dispatch({ type: "save_review" });
  }, []);

  const reextractReview = useCallback(async () => {
    const current = stateRef.current;
    if (!current.review) throw new DemoIntakeError("审阅尚未建立。");
    const projectId = projectIdRef.current;
    if (projectId === null) throw new DemoIntakeError("当前会话尚未建案。");
    const brief = briefRef.current ?? (await fetchDemoBrief(projectId));
    const { result: done } = await runTaskWithProviderFallback(
      async (provider) => {
        const task = await startDemoAnchorExtract(
          projectId,
          brief.draft_revision,
          provider,
        );
        return waitForDemoTask(projectId, task.task_run_id);
      },
    );
    const result = done.result as
      | { author_anchors: Array<{ statement: string }>; creative_constraints: Array<{ statement: string; suggested_strength: "hard" | "soft" }> }
      | null;
    if (!result) throw new DemoIntakeError("拆解任务没有返回结果。");
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
    const brief = briefRef.current ?? (await fetchDemoBrief(projectId));
    const version = await confirmDemoBrief(projectId, brief.draft_revision);
    briefRef.current = await fetchDemoBrief(projectId);
    dispatch({ type: "freeze_review" });
    dispatch({
      type: "patch",
      patch: {
        workingBriefVersion: version.version_no,
        frozenBriefVersion: version.version_no,
      },
    });
    return true;
  }, []);

  const generateCandidates = useCallback(async () => {
    const current = stateRef.current;
    if (
      !current.review ||
      current.frozenBriefVersion === null ||
      current.generation.status === "generating" ||
      current.draftCandidates.some(
        (candidate) => candidate.briefVersion === current.frozenBriefVersion,
      )
    ) {
      return false;
    }
    const projectId = projectIdRef.current;
    if (projectId === null) return false;
    dispatch({ type: "start_generation" });
    try {
      const brief = briefRef.current ?? (await fetchDemoBrief(projectId));
      if (!brief.current_version_id) {
        throw new DemoIntakeError("请先冻结当前创作简报。");
      }
      // 第一份运行带 Provider 认证回退，确认可用的模型服务后复用于其余两份。
      const { provider: workingProvider } = await runTaskWithProviderFallback(
        async (provider) => {
          const task = await startDemoDraftRun(
            projectId,
            brief.current_version_id!,
            brief.draft_revision,
            provider,
          );
          await waitForDemoTask(projectId, task.task_run_id);
          dispatch({ type: "advance_generation", stage: 2 });
          return provider;
        },
      );
      const runs = await Promise.all(
        [2, 3].map(() =>
          startDemoDraftRun(
            projectId,
            brief.current_version_id!,
            brief.draft_revision,
            workingProvider,
          ),
        ),
      );
      await Promise.all(
        runs.map(async (run) => {
          await waitForDemoTask(projectId, run.task_run_id);
          dispatch({ type: "advance_generation", stage: 3 });
        }),
      );
      const candidates = await fetchDemoDraftCandidates(projectId);
      const currentOnes = candidates.filter(
        (candidate) => candidate.is_current_brief,
      );
      if (currentOnes.length < runs.length) {
        throw new DemoIntakeError("候选尚未全部归档，请稍后重试。");
      }
      const base = buildPrototypeDraftCandidates(
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
      const mapped = currentOnes.map((view, index) =>
        mapDraftCandidateView(view, base[index % base.length]),
      );
      dispatch({ type: "complete_generation", candidates: mapped });
      return true;
    } catch (error) {
      dispatch({
        type: "patch",
        patch: { generation: { status: "idle", stage: 0 } },
      });
      throw error;
    }
  }, []);

  const previewCandidate = useCallback((candidateId: string | null) => {
    dispatch({ type: "preview_candidate", candidateId });
  }, []);

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
    const brief = briefRef.current ?? (await fetchDemoBrief(projectId));
    await adoptDemoDraftCandidate(projectId, taskRunId, brief.draft_revision);
    briefRef.current = await fetchDemoBrief(projectId);
    dispatch({ type: "adopt_candidate", candidateId });
    return true;
  }, []);

  const beginBriefRevision = useCallback(() => {
    dispatch({ type: "begin_revision" });
  }, []);

  const candidateStatus = useCallback(
    (candidate: PrototypeDraftCandidate) =>
      prototypeDraftCandidateStatus(state, candidate),
    [state],
  );

  const resetPrototype = useCallback(() => {
    projectIdRef.current = null;
    intakeRef.current = null;
    briefRef.current = null;
    dispatch({ type: "reset" });
  }, []);

  const activeCandidate = useMemo(() => {
    const activeId = state.previewCandidateId ?? state.adoptedCandidateId;
    return (
      state.draftCandidates.find((candidate) => candidate.id === activeId) ??
      null
    );
  }, [state.adoptedCandidateId, state.draftCandidates, state.previewCandidateId]);

  const value = useMemo<DemoPrototypeContextValue>(
    () => ({
      state,
      activeCandidate,
      patchState,
      beginBriefReview,
      setReview,
      saveReview,
      freezeReview,
      generateCandidates,
      previewCandidate,
      adoptCandidate,
      beginBriefRevision,
      candidateStatus,
      resetPrototype,
      submitPolish,
      adoptPolish,
      continueToQuestions,
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
      activateCandidate,
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
      patchState,
      previewCandidate,
      resetPrototype,
      reextractReview,
      saveCandidateAsNew,
      saveCandidateBookmark,
      saveReview,
      setReview,
      state,
      submitPolish,
    ],
  );

  return (
    <DemoPrototypeContext.Provider value={value}>
      {children}
    </DemoPrototypeContext.Provider>
  );
}

export function useDemoPrototype() {
  const context = useContext(DemoPrototypeContext);
  if (!context) {
    throw new Error("演示原型必须位于 DemoPrototypeProvider 内。");
  }
  return context;
}
