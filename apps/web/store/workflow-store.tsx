"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { ProviderName, TaskType } from "@/lib/api-client";

const STORAGE_KEY = "casefile.real.workflow.v3";
const PREVIOUS_STORAGE_KEY = "casefile.real.workflow.v2";
const LEGACY_STORAGE_KEY = "casefile.real.workflow.v1";

type BriefTaskType = Exclude<TaskType, "casefile_chat">;

export type WorkflowTaskPointers = Record<BriefTaskType, number | null>;

interface WorkflowSession {
  actorId: number;
  projectId: number | null;
  taskRunIds: WorkflowTaskPointers;
  provider: ProviderName;
}

interface WorkflowContextValue extends WorkflowSession {
  ready: boolean;
  setProject: (projectId: number) => void;
  setTask: (taskType: BriefTaskType, taskRunId: number | null) => void;
  setProvider: (provider: ProviderName) => void;
  clear: () => void;
}

const emptyTaskRunIds: WorkflowTaskPointers = {
  brief_polish: null,
  brief_anchor_extract: null,
  brief_intake_questions: null,
  brief_intake_synthesize: null,
  brief_to_draft: null,
};

const initialSession: WorkflowSession = {
  actorId: 1,
  projectId: null,
  taskRunIds: emptyTaskRunIds,
  provider: "openai",
};

const WorkflowContext = createContext<WorkflowContextValue | null>(null);

export function WorkflowProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(initialSession);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const stored =
        window.localStorage.getItem(STORAGE_KEY) ??
        window.localStorage.getItem(PREVIOUS_STORAGE_KEY) ??
        window.localStorage.getItem(LEGACY_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Partial<WorkflowSession> & {
          taskRunId?: number | null;
        };
        const parsedPointers = parsed.taskRunIds;
        setSession({
          actorId: Number.isInteger(parsed.actorId) ? Number(parsed.actorId) : 1,
          projectId: Number.isInteger(parsed.projectId) ? Number(parsed.projectId) : null,
          taskRunIds: {
            brief_polish: Number.isInteger(parsedPointers?.brief_polish)
              ? Number(parsedPointers?.brief_polish)
              : null,
            brief_anchor_extract: Number.isInteger(
              parsedPointers?.brief_anchor_extract,
            )
              ? Number(parsedPointers?.brief_anchor_extract)
              : null,
            brief_intake_questions: Number.isInteger(
              parsedPointers?.brief_intake_questions,
            )
              ? Number(parsedPointers?.brief_intake_questions)
              : null,
            brief_intake_synthesize: Number.isInteger(
              parsedPointers?.brief_intake_synthesize,
            )
              ? Number(parsedPointers?.brief_intake_synthesize)
              : null,
            brief_to_draft: Number.isInteger(parsedPointers?.brief_to_draft)
              ? Number(parsedPointers?.brief_to_draft)
              : Number.isInteger(parsed.taskRunId)
                ? Number(parsed.taskRunId)
                : null,
          },
          provider: parsed.provider === "deepseek" ? "deepseek" : "openai",
        });
      }
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    if (ready) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  }, [ready, session]);

  const value = useMemo<WorkflowContextValue>(
    () => ({
      ...session,
      ready,
      setProject: (projectId) =>
        setSession((current) => ({
          ...current,
          projectId,
          taskRunIds: { ...emptyTaskRunIds },
        })),
      setTask: (taskType, taskRunId) =>
        setSession((current) => ({
          ...current,
          taskRunIds: {
            ...current.taskRunIds,
            [taskType]: taskRunId,
          },
        })),
      setProvider: (provider) => setSession((current) => ({ ...current, provider })),
      clear: () =>
        setSession((current) => ({
          ...initialSession,
          taskRunIds: { ...emptyTaskRunIds },
          provider: current.provider,
        })),
    }),
    [ready, session],
  );

  return <WorkflowContext.Provider value={value}>{children}</WorkflowContext.Provider>;
}

export function useWorkflowSession() {
  const value = useContext(WorkflowContext);
  if (!value) throw new Error("useWorkflowSession must be used within WorkflowProvider");
  return value;
}
