"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PublicGoalDelivery, PublicGoalSession } from "@casefile/contracts";
import { getAgentGoal, listAgentGoalDeliveries, streamAgentGoalEvents } from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";

export function useAgentGoalFeedback(
  projectId: number, scope: string, goalId: number | null,
  onChange: (goal: PublicGoalSession) => void,
  onSuccessor: (goalId: number) => void,
) {
  const [snapshot, setSnapshot] = useState<{ scope: string; goal: PublicGoalSession; deliveries: PublicGoalDelivery[]; connected: boolean } | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const callbacks = useRef({ onChange, onSuccessor });
  useEffect(() => { callbacks.current = { onChange, onSuccessor }; }, [onChange, onSuccessor]);
  const refresh = useCallback(() => setRefreshKey((key) => key + 1), []);
  useEffect(() => {
    if (goalId === null) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    let current: PublicGoalSession | null = null;
    let requestSequence = 0;
    const terminal = new Set(["completed", "cancelled", "failed", "superseded"]);
    async function sync() {
      const requestId = ++requestSequence;
      const [goal, deliveries] = await Promise.all([
        getAgentGoal(LOCAL_ACTOR_ID, projectId, goalId!),
        listAgentGoalDeliveries(LOCAL_ACTOR_ID, projectId, goalId!),
      ]);
      if (controller.signal.aborted || requestId !== requestSequence) return;
      current = goal;
      setSnapshot({ scope, goal, deliveries, connected: true });
      callbacks.current.onChange(goal);
      const successor = deliveries.find((delivery) => delivery.successor_goal_id)?.successor_goal_id;
      if (successor) callbacks.current.onSuccessor(successor);
      const pending = deliveries.some((delivery) => ["queued", "claimed"].includes(delivery.status));
      if (timer) clearTimeout(timer);
      if (pending || !terminal.has(goal.status)) timer = setTimeout(() => void poll(), pending ? 1000 : 5000);
    }
    function disconnected() {
      if (!controller.signal.aborted) setSnapshot((previous) => previous?.scope === scope ? { ...previous, connected: false } : previous);
    }
    async function poll() {
      try { await sync(); } catch {
        disconnected();
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 2000);
      }
    }
    async function follow() {
      while (!controller.signal.aborted) {
        try {
          await sync();
          if (current && terminal.has(current.status)) return;
          await streamAgentGoalEvents(LOCAL_ACTOR_ID, projectId, goalId!, (event) => {
            cursor = event.sequence;
            void poll();
          }, controller.signal, cursor);
        } catch { disconnected(); }
        if (controller.signal.aborted) return;
        await new Promise<void>((resolve) => {
          const retry = setTimeout(resolve, 1000);
          controller.signal.addEventListener("abort", () => { clearTimeout(retry); resolve(); }, { once: true });
        });
      }
    }
    void follow();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [projectId, scope, goalId, refreshKey]);
  const visible = snapshot?.scope === scope && snapshot.goal.goal_id === goalId ? snapshot : null;
  return { goal: visible?.goal ?? null, deliveries: visible?.deliveries ?? [], connected: visible?.connected ?? false, refresh };
}
