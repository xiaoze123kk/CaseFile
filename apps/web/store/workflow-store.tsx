"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

const STORAGE_KEY = "casefile.real.workflow.v1";

interface WorkflowSession {
  actorId: number;
  projectId: number | null;
  taskRunId: number | null;
}

interface WorkflowContextValue extends WorkflowSession {
  ready: boolean;
  setProject: (projectId: number) => void;
  setTask: (taskRunId: number | null) => void;
  clear: () => void;
}

const initialSession: WorkflowSession = {
  actorId: 1,
  projectId: null,
  taskRunId: null,
};

const WorkflowContext = createContext<WorkflowContextValue | null>(null);

export function WorkflowProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(initialSession);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Partial<WorkflowSession>;
        setSession({
          actorId: Number.isInteger(parsed.actorId) ? Number(parsed.actorId) : 1,
          projectId: Number.isInteger(parsed.projectId) ? Number(parsed.projectId) : null,
          taskRunId: Number.isInteger(parsed.taskRunId) ? Number(parsed.taskRunId) : null,
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
        setSession((current) => ({ ...current, projectId, taskRunId: null })),
      setTask: (taskRunId) => setSession((current) => ({ ...current, taskRunId })),
      clear: () => setSession(initialSession),
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
