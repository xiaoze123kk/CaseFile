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

import {
  buildPrototypeDraftCandidates,
  type PrototypeDraftCandidate,
} from "@/features/analyst-workbench/analyst-fixture";
import {
  canFreezeBriefReview,
  createBriefReview,
  createEmptyBrief,
  mergeReviewIntoBrief,
  type PrototypeAnswer,
  type PrototypeBrief,
  type PrototypeBriefReview,
  type PrototypeCandidate,
  type PrototypePolishMode,
  type PrototypeStep,
} from "@/features/intake-prototype/intake-prototype-model";

export type PrototypeGenerationStatus = "idle" | "generating" | "ready";
export type PrototypeDraftCandidateStatus = "pending" | "current" | "stale";

export interface DemoPrototypeState {
  step: PrototypeStep;
  furthestStep: number;
  sourceText: string;
  polishMode: PrototypePolishMode;
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

interface DemoPrototypeContextValue {
  state: DemoPrototypeState;
  activeCandidate: PrototypeDraftCandidate | null;
  patchState: (patch: Partial<DemoPrototypeState>) => void;
  beginBriefReview: () => void;
  setReview: (review: PrototypeBriefReview) => void;
  saveReview: () => void;
  freezeReview: () => boolean;
  generateCandidates: () => boolean;
  previewCandidate: (candidateId: string | null) => void;
  adoptCandidate: (candidateId: string) => boolean;
  beginBriefRevision: () => void;
  candidateStatus: (
    candidate: PrototypeDraftCandidate,
  ) => PrototypeDraftCandidateStatus;
  resetPrototype: () => void;
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
  const timersRef = useRef<number[]>([]);

  const clearGenerationTimers = useCallback(() => {
    for (const timer of timersRef.current) window.clearTimeout(timer);
    timersRef.current = [];
  }, []);

  useEffect(() => clearGenerationTimers, [clearGenerationTimers]);

  const patchState = useCallback((patch: Partial<DemoPrototypeState>) => {
    dispatch({ type: "patch", patch });
  }, []);

  const beginBriefReview = useCallback(() => {
    dispatch({
      type: "set_review",
      review: createBriefReview(state.brief, state.answers),
    });
    dispatch({
      type: "patch",
      patch: {
        step: "review",
        furthestStep: Math.max(state.furthestStep, 3),
      },
    });
  }, [state.answers, state.brief, state.furthestStep]);

  const setReview = useCallback((review: PrototypeBriefReview) => {
    dispatch({ type: "set_review", review });
  }, []);

  const saveReview = useCallback(() => {
    dispatch({ type: "save_review" });
  }, []);

  const freezeReview = useCallback(() => {
    if (!state.review || !canFreezeBriefReview(state.review)) return false;
    dispatch({ type: "freeze_review" });
    return true;
  }, [state.review]);

  const generateCandidates = useCallback(() => {
    if (
      !state.review ||
      state.frozenBriefVersion === null ||
      state.generation.status === "generating" ||
      state.draftCandidates.some(
        (candidate) =>
          candidate.briefVersion === state.frozenBriefVersion,
      )
    ) {
      return false;
    }
    clearGenerationTimers();
    dispatch({ type: "start_generation" });
    const briefVersion = state.frozenBriefVersion;
    const review = state.review;
    timersRef.current = [
      window.setTimeout(
        () => dispatch({ type: "advance_generation", stage: 2 }),
        360,
      ),
      window.setTimeout(
        () => dispatch({ type: "advance_generation", stage: 3 }),
        720,
      ),
      window.setTimeout(() => {
        const candidates = buildPrototypeDraftCandidates(
          {
            creativeIntent: review.creativeIntent,
            reasoningProposition: review.reasoningProposition,
            authorAnswer: review.authorAnswer,
            constraints: review.creativeConstraints
              .map((constraint) => constraint.statement.trim())
              .filter(Boolean),
          },
          briefVersion,
        );
        dispatch({ type: "complete_generation", candidates });
        timersRef.current = [];
      }, 1080),
    ];
    return true;
  }, [clearGenerationTimers, state]);

  const previewCandidate = useCallback((candidateId: string | null) => {
    dispatch({ type: "preview_candidate", candidateId });
  }, []);

  const adoptCandidate = useCallback(
    (candidateId: string) => {
      const candidate = state.draftCandidates.find(
        (item) => item.id === candidateId,
      );
      if (!candidate || candidate.briefVersion !== state.frozenBriefVersion) {
        return false;
      }
      dispatch({ type: "adopt_candidate", candidateId });
      return true;
    },
    [state.draftCandidates, state.frozenBriefVersion],
  );

  const beginBriefRevision = useCallback(() => {
    clearGenerationTimers();
    dispatch({ type: "begin_revision" });
  }, [clearGenerationTimers]);

  const candidateStatus = useCallback(
    (candidate: PrototypeDraftCandidate) =>
      prototypeDraftCandidateStatus(state, candidate),
    [state],
  );

  const resetPrototype = useCallback(() => {
    clearGenerationTimers();
    dispatch({ type: "reset" });
  }, [clearGenerationTimers]);

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
    }),
    [
      activeCandidate,
      adoptCandidate,
      beginBriefReview,
      beginBriefRevision,
      candidateStatus,
      freezeReview,
      generateCandidates,
      patchState,
      previewCandidate,
      resetPrototype,
      saveReview,
      setReview,
      state,
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

