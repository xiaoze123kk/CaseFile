"use client";

import {
  createContext,
  type Dispatch,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
} from "react";

import {
  type BriefTextField,
  canCompilePrototype,
  createDefaultPrototypeState,
  type DraftEvent,
  type PrototypeState,
} from "@/lib/prototype-model";

const STORAGE_KEY = "casefile.prototype.v1";

export type PrototypeAction =
  | { type: "hydrate"; state: PrototypeState }
  | { type: "set-idea-original"; value: string }
  | { type: "generate-suggestion" }
  | { type: "adopt-suggestion" }
  | { type: "reject-suggestion" }
  | { type: "update-brief"; field: BriefTextField; value: string }
  | { type: "toggle-decision"; id: string }
  | { type: "approve-brief" }
  | { type: "select-event"; id: string }
  | {
      type: "update-event";
      id: string;
      field: keyof Pick<
        DraftEvent,
        | "time"
        | "title"
        | "description"
        | "location"
        | "phase"
        | "participants"
        | "visibility"
        | "importance"
      >;
      value: string;
    }
  | { type: "save-event" }
  | { type: "apply-patch" }
  | { type: "start-validation" }
  | { type: "complete-validation" }
  | {
      type: "set-compiler-profile";
      profile: PrototypeState["compiler"]["profile"];
    }
  | { type: "toggle-artifact"; id: string }
  | { type: "start-compile" }
  | { type: "complete-compile" }
  | { type: "reset" };

function isPrototypeState(value: unknown): value is PrototypeState {
  return (
    typeof value === "object" &&
    value !== null &&
    "storageVersion" in value &&
    value.storageVersion === 1
  );
}

function nextRunId(current: string): string {
  const numeric = Number.parseInt(current.replace(/\D/g, ""), 10);
  return `VAL-${String((Number.isFinite(numeric) ? numeric : 18) + 1).padStart(4, "0")}`;
}

export function prototypeReducer(
  state: PrototypeState,
  action: PrototypeAction,
): PrototypeState {
  switch (action.type) {
    case "hydrate":
      return action.state;
    case "set-idea-original":
      return {
        ...state,
        idea: {
          ...state.idea,
          original: action.value,
          suggestionStatus: "idle",
        },
      };
    case "generate-suggestion":
      return {
        ...state,
        idea: { ...state.idea, suggestionStatus: "pending" },
      };
    case "adopt-suggestion":
      return {
        ...state,
        idea: {
          ...state.idea,
          working: state.idea.suggestion,
          suggestionStatus: "adopted",
        },
        brief: {
          ...state.brief,
          oneLineConcept: state.idea.suggestion,
        },
      };
    case "reject-suggestion":
      return {
        ...state,
        idea: { ...state.idea, suggestionStatus: "rejected" },
      };
    case "update-brief":
      return {
        ...state,
        brief: {
          ...state.brief,
          [action.field]: action.value,
          approved: false,
        },
      };
    case "toggle-decision":
      return {
        ...state,
        brief: {
          ...state.brief,
          approved: false,
          decisions: state.brief.decisions.map((decision) =>
            decision.id === action.id
              ? { ...decision, checked: !decision.checked }
              : decision,
          ),
        },
      };
    case "approve-brief":
      return {
        ...state,
        brief: { ...state.brief, approved: true },
      };
    case "select-event":
      return {
        ...state,
        draft: { ...state.draft, selectedEventId: action.id },
      };
    case "update-event": {
      const nextRevision = state.draft.revision + 1;
      return {
        ...state,
        draft: {
          ...state.draft,
          revision: nextRevision,
          lastSavedAt: "待保存",
          events: state.draft.events.map((event) =>
            event.id === action.id
              ? { ...event, [action.field]: action.value }
              : event,
          ),
        },
        validation: {
          ...state.validation,
          status: "stale",
        },
        compiler: {
          ...state.compiler,
          status: "blocked",
        },
      };
    }
    case "save-event":
      return {
        ...state,
        draft: { ...state.draft, lastSavedAt: "刚刚" },
      };
    case "apply-patch": {
      const nextRevision = state.draft.revision + 1;
      return {
        ...state,
        draft: {
          ...state.draft,
          revision: nextRevision,
          lastSavedAt: "刚刚",
          events: state.draft.events.map((event) =>
            event.id === "EVL-1823"
              ? { ...event, visibility: "AI 核心 + 秦彻" }
              : event,
          ),
        },
        validation: {
          ...state.validation,
          status: "stale",
          patchDecision: "approved",
          issues: state.validation.issues.map((issue) =>
            issue.id === "VAL-KNOW-001"
              ? { ...issue, status: "pending-revalidation" }
              : issue,
          ),
        },
        compiler: { ...state.compiler, status: "blocked" },
      };
    }
    case "start-validation":
      return {
        ...state,
        validation: { ...state.validation, status: "running" },
      };
    case "complete-validation": {
      const protectedEvent = state.draft.events.find(
        (event) => event.id === "EVL-1823",
      );
      const knowledgeIssueResolved =
        protectedEvent?.visibility === "AI 核心 + 秦彻";
      const nextState: PrototypeState = {
        ...state,
        validation: {
          ...state.validation,
          status: "fresh",
          runId: nextRunId(state.validation.runId),
          snapshotRevision: state.draft.revision,
          lastRunAt: "刚刚",
          issues: state.validation.issues.map((issue) =>
            issue.id === "VAL-KNOW-001"
              ? {
                  ...issue,
                  status: knowledgeIssueResolved ? "resolved" : "open",
                }
              : issue,
          ),
        },
      };
      return {
        ...nextState,
        compiler: {
          ...nextState.compiler,
          status: canCompilePrototype(nextState) ? "idle" : "blocked",
        },
      };
    }
    case "set-compiler-profile":
      return {
        ...state,
        compiler: { ...state.compiler, profile: action.profile },
      };
    case "toggle-artifact":
      return {
        ...state,
        compiler: {
          ...state.compiler,
          artifacts: state.compiler.artifacts.map((artifact) =>
            artifact.id === action.id
              ? { ...artifact, selected: !artifact.selected }
              : artifact,
          ),
        },
      };
    case "start-compile":
      return canCompilePrototype(state)
        ? {
            ...state,
            compiler: { ...state.compiler, status: "building" },
          }
        : {
            ...state,
            compiler: { ...state.compiler, status: "blocked" },
          };
    case "complete-compile":
      return state.compiler.status === "building"
        ? {
            ...state,
            compiler: { ...state.compiler, status: "completed" },
          }
        : state;
    case "reset":
      return createDefaultPrototypeState();
    default:
      return state;
  }
}

interface PrototypeContextValue {
  state: PrototypeState;
  dispatch: Dispatch<PrototypeAction>;
  ready: boolean;
  reset: () => void;
}

const PrototypeContext = createContext<PrototypeContextValue | null>(null);

export function PrototypeProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    prototypeReducer,
    undefined,
    createDefaultPrototypeState,
  );
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed: unknown = JSON.parse(stored);
        if (isPrototypeState(parsed)) {
          dispatch({ type: "hydrate", state: parsed });
        }
      }
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    if (!ready) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }, [ready, state]);

  const value = useMemo<PrototypeContextValue>(
    () => ({
      state,
      dispatch,
      ready,
      reset: () => {
        window.localStorage.removeItem(STORAGE_KEY);
        dispatch({ type: "reset" });
      },
    }),
    [ready, state],
  );

  return (
    <PrototypeContext.Provider value={value}>
      {children}
    </PrototypeContext.Provider>
  );
}

export function usePrototype(): PrototypeContextValue {
  const context = useContext(PrototypeContext);
  if (!context) {
    throw new Error("usePrototype must be used inside PrototypeProvider");
  }
  return context;
}
