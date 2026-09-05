"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type {
  PublicAgentEvent,
  PublicAgentMessage,
  PublicAgentRun,
  PublicPatchReviewResult,
  PublicPatchSet,
  PublicGoalSession,
} from "@casefile/contracts";

import {
  applyAgentPatchSet,
  createAgentThread,
  errorMessage,
  getAgentRun,
  cancelAgentRun,
  cancelAgentGoal,
  listAgentRunFeedback,
  listAgentMessages,
  listAgentThreads,
  redoAgentPatchSet,
  sendAgentMessage,
  simulateAgentPatchSet,
  streamAgentRunEvents,
  undoAgentPatchSet,
  updateAgentThread,
  type AgentChatFocus,
  type AgentChatRoutingHint,
  type AgentThreadView,
} from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";

import { WorkbenchAgentComposer } from "./workbench-agent-composer";
import {
  WorkbenchAgentConversation,
  agentAuditFindingsFor,
} from "./workbench-agent-conversation";
import { WorkbenchAgentDesk } from "./workbench-agent-desk";
import {
  WorkbenchAgentSurface,
  type AgentSurface,
} from "./workbench-agent-surface";
import { WorkbenchAgentThreadMenu } from "./workbench-agent-thread-menu";
import { WorkbenchAgentInspector, type PatchReviewState } from "./workbench-agent-inspector";
import { AgentMessagePatch } from "./workbench-agent-message-patch";
import { WorkbenchAgentDetailPortals, type AgentDetailNavigation } from "./workbench-agent-detail-portals";
import { WorkbenchAgentPortal } from "./workbench-agent-portal";
import { composerReducer, composerFocus, newComposerEntry } from "./workbench-agent-context";
import { emptyFeedback, reduceFeedback, activeFeedbackRefs, goalLabel, type RunFeedback } from "./workbench-agent-feedback";
import { useAgentGoalFeedback } from "./use-agent-goal-feedback";
import styles from "./workbench-agent.module.css";

const ACTIVE_RUN_STATUSES = new Set<PublicAgentRun["status"]>([
  "queued",
  "running",
  "cancelling",
]);

const TERMINAL_RUN_STATUSES = new Set<PublicAgentRun["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);

function mergeMessages(
  previous: PublicAgentMessage[],
  incoming: PublicAgentMessage[],
): PublicAgentMessage[] {
  const byId = new Map(previous.map((message) => [message.message_id, message]));
  for (const message of incoming) byId.set(message.message_id, message);
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence);
}

function pendingAssistantRun(
  messages: PublicAgentMessage[],
): { message: PublicAgentMessage; run: PublicAgentRun } | null {
  const pending = messages.find(
    (message) =>
      message.role === "assistant" &&
      message.status === "pending" &&
      message.run !== null &&
      ACTIVE_RUN_STATUSES.has(message.run.status),
  );
  return pending?.run ? { message: pending, run: pending.run } : null;
}

export function AgentLivePanel({
  projectId,
  draftId,
  draftRevision,
  focus,
  kickoff,
  onLocateObject,
  onFocusPatch = () => undefined,
  onFocusFinding,
  focusPatchSetId,
  focusFindingId,
  inspectorHost,
  threadHost,
  presentationHost,
  onDraftChanged,
  disabled = false,
  onClose,
  surface = "center",
  onContinueInDesk = () => undefined,
  focusRequest = 0,
  details,
  onAgentFocus,
}: {
  projectId: number;
  draftId: number;
  draftRevision: number;
  focus: AgentChatFocus;
  kickoff: { id: number; prompt: string; routingHint?: AgentChatRoutingHint } | null;
  referenceLabels: {
    objects: Record<string, string>;
    events: Record<string, string>;
    issues: Record<string, string>;
  };
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onFocusPatch?: (patchSetId: number) => void;
  onFocusFinding?: (findingId: string) => void;
  focusPatchSetId?: number | null;
  focusFindingId?: string | null;
  inspectorHost?: HTMLElement | null;
  threadHost?: HTMLElement | null;
  presentationHost?: HTMLElement | null;
  onDraftChanged: () => Promise<void>;
  disabled?: boolean;
  onClose: () => void;
  surface?: AgentSurface;
  onContinueInDesk?: () => void;
  focusRequest?: number;
  details?: AgentDetailNavigation;
  onAgentFocus?: (ids: string[]) => void;
}) {
  const [threads, setThreads] = useState<AgentThreadView[]>([]);
  const [threadMenuRows, setThreadMenuRows] = useState<AgentThreadView[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null);
  const [messages, setMessages] = useState<PublicAgentMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [composers, dispatchComposer] = useReducer(composerReducer, {});
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [liveRuns, setLiveRuns] = useState<Record<number, PublicAgentRun>>({});
  const [feedback, setFeedback] = useState<Record<number, RunFeedback>>({});
  const [runConnections, setRunConnections] = useState<Record<number, boolean>>({});
  const [goalHints, setGoalHints] = useState<Record<number, number>>({});
  const [deliveryMode, setDeliveryMode] = useState<"steer" | "replace">("steer");
  const [resumeAcknowledgement, setResumeAcknowledgement] = useState("");
  const [sendingText, setSendingText] = useState<{ threadId: number; text: string } | null>(null);
  const [sendError, setSendError] = useState<{ threadId: number; message: string } | null>(null);
  const feedbackRef = useRef<Record<number, RunFeedback>>({});
  const [creatingThread, setCreatingThread] = useState(false);
  const [patchBusyId, setPatchBusyId] = useState<number | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [localFocusPatchSetId, setLocalFocusPatchSetId] = useState<number | null>(null);
  const [localFocusFindingId, setLocalFocusFindingId] = useState<string | null>(null);
  const [revisionByPatch, setRevisionByPatch] = useState<Record<number, number>>({});
  const [patchReviews, setPatchReviews] = useState<Record<string, PatchReviewState>>({});
  const [patchFocusRequest, setPatchFocusRequest] = useState(0);

  const followsRef = useRef(new Map<number, AbortController>());
  const followedRunIdsRef = useRef(new Set<number>());
  const messagesRequestRef = useRef(0);
  const selectedThreadIdRef = useRef<number | null>(null);
  const patchScopeRef = useRef({ active: true });
  const [patchIdentity, setPatchIdentity] = useState({ projectId, draftId });
  if (patchIdentity.projectId !== projectId || patchIdentity.draftId !== draftId) {
    setPatchIdentity({ projectId, draftId });
    setPatchBusyId(null);
    setPatchError(null);
  }
  useEffect(() => {
    const scope = { active: true };
    patchScopeRef.current = scope;
    return () => { scope.active = false; };
  }, [projectId, draftId]);

  const composerThreadId = selectedThreadId ?? 0;
  const composer = composers[composerThreadId] ?? newComposerEntry(focus);
  const draft = composer.text;
  const setDraft = (text: string) => dispatchComposer({ type: "text", threadId: composerThreadId, candidate: focus, text });
  const previousFocus = useRef(focus);
  useEffect(() => {
    // Switching threads restores its own candidate; only a new workspace selection updates it.
    if (JSON.stringify(previousFocus.current) !== JSON.stringify(focus)) {
      previousFocus.current = focus;
      dispatchComposer({ type: "candidate", threadId: selectedThreadId ?? 0, candidate: focus });
    }
  }, [focus, selectedThreadId]);

  useEffect(() => {
    selectedThreadIdRef.current = selectedThreadId;
    if (selectedThreadId !== null) dispatchComposer({ type: "initialize", threadId: selectedThreadId, candidate: previousFocus.current });
  }, [selectedThreadId]);

  useEffect(
    () => () => {
      for (const controller of followsRef.current.values()) controller.abort();
    },
    [],
  );

  useEffect(() => {
    if (surface === "dock" || details) return;
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    }
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose, surface, details]);

  const refreshThreads = useCallback(
    async (options: { query?: string; includeArchived?: boolean } = {}) => {
      try {
        const rows = await listAgentThreads(LOCAL_ACTOR_ID, projectId, options);
        if (options.query !== undefined || options.includeArchived !== undefined) {
          setThreadMenuRows(rows);
        } else {
          setThreads(rows);
          setThreadMenuRows(rows);
        }
      } catch {
        // A background refresh must not overwrite the panel with an error toast.
      }
    },
    [projectId],
  );

  const reloadMessages = useCallback(async (requestedThreadId?: number) => {
    const threadId = requestedThreadId ?? selectedThreadIdRef.current;
    if (threadId === null) return;
    const requestId = ++messagesRequestRef.current;
    setMessagesLoading(true);
    setMessagesError(null);
    try {
      const rows = await listAgentMessages(
        LOCAL_ACTOR_ID,
        projectId,
        threadId,
      );
      if (requestId === messagesRequestRef.current) setMessages(rows);
    } catch (caught) {
      if (requestId === messagesRequestRef.current) {
        setMessagesError(errorMessage(caught));
      }
    } finally {
      if (requestId === messagesRequestRef.current) {
        setMessagesLoading(false);
      }
    }
  }, [projectId]);

  const bootstrap = useCallback(async () => {
    setThreadsLoading(true);
    setThreadsError(null);
    try {
      let rows = await listAgentThreads(LOCAL_ACTOR_ID, projectId);
      if (rows.length === 0) {
        const created = await createAgentThread(
          LOCAL_ACTOR_ID,
          projectId,
          draftId,
          draftRevision,
        );
        rows = [created];
      }
      setThreads(rows);
      setThreadMenuRows(rows);
      setSelectedThreadId((current) =>
        current !== null && rows.some((row) => row.thread_id === current)
          ? current
          : (rows[0]?.thread_id ?? null),
      );
    } catch (caught) {
      setThreadsError(errorMessage(caught));
    } finally {
      setThreadsLoading(false);
    }
  }, [draftId, draftRevision, projectId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void bootstrap(), 0);
    return () => window.clearTimeout(timer);
  }, [bootstrap]);

  useEffect(() => {
    if (selectedThreadId === null) return;
    let active = true;
    const requestId = ++messagesRequestRef.current;
    const timer = window.setTimeout(() => {
      if (!active) return;
      setMessages([]);
      setMessagesLoading(true);
      setMessagesError(null);
      void listAgentMessages(LOCAL_ACTOR_ID, projectId, selectedThreadId)
        .then((rows) => {
          if (active && requestId === messagesRequestRef.current) {
            setMessages(rows);
          }
        })
        .catch((caught: unknown) => {
          if (active && requestId === messagesRequestRef.current) {
            setMessagesError(errorMessage(caught));
          }
        })
        .finally(() => {
          if (active && requestId === messagesRequestRef.current) {
            setMessagesLoading(false);
          }
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [draftId, draftRevision, projectId, selectedThreadId]);

  const startFollow = useCallback(
    (initialRun: PublicAgentRun, threadId: number) => {
      const runId = initialRun.run_id;
      if (followedRunIdsRef.current.has(runId)) return;
      followedRunIdsRef.current.add(runId);
      setLiveRuns((previous) => ({ ...previous, [runId]: initialRun }));
      const controller = new AbortController();
      followsRef.current.set(runId, controller);

      function receiveEvent(event: PublicAgentEvent) {
        if (controller.signal.aborted) return;
        setRunConnections((previous) => ({ ...previous, [runId]: true }));
        const next = reduceFeedback(feedbackRef.current[runId] ?? emptyFeedback(), event);
        feedbackRef.current[runId] = next;
        setFeedback((previous) => ({ ...previous, [runId]: next }));
        if (next.gap) throw new Error("回复片段需要重新同步");
        if (event.event === "run.context" || event.event === "run.verification" || event.event.startsWith("message.")) return;
        setLiveRuns((previous) => {
          const current = previous[runId] ?? initialRun;
          if (event.event === "run.accepted" || event.event === "run.completed") {
            return { ...previous, [runId]: event.run };
          }
          if (event.event === "run.activity" || event.event === "run.activity_detail") {
            return {
              ...previous,
              [runId]: { ...current, status: "running", activity: event.activity },
            };
          }
          if (event.event === "run.failed") {
            return {
              ...previous,
              [runId]: {
                ...current,
                status: "failed",
                activity: null,
                cancellable: false,
                failure: event.failure,
              },
            };
          }
          if (event.event !== "run.cancelled") return previous;
          return {
            ...previous,
            [runId]: {
              ...current,
              status: "cancelled",
              activity: null,
              cancellable: false,
              failure: null,
            },
          };
        });
      }

      void (async () => {
        let cursor = 0;
        let reconnectDelay = 250;
        try {
          feedbackRef.current[runId] = emptyFeedback();
          while (!controller.signal.aborted) {
            try {
              cursor = await streamAgentRunEvents(
                LOCAL_ACTOR_ID,
                projectId,
                runId,
                (event) => { receiveEvent(event); cursor = event.sequence; },
                controller.signal,
                cursor,
              );
              reconnectDelay = 250;
            } catch {
              setRunConnections((previous) => ({ ...previous, [runId]: false }));
              if (controller.signal.aborted) break;
              if (feedbackRef.current[runId]?.gap) {
                feedbackRef.current[runId] = emptyFeedback();
                cursor = 0;
              }
            }
            try {
              const current = await getAgentRun(LOCAL_ACTOR_ID, projectId, runId);
              setLiveRuns((previous) => ({ ...previous, [runId]: current }));
              if (TERMINAL_RUN_STATUSES.has(current.status)) break;
            } catch {
              if (controller.signal.aborted) break;
            }
            await new Promise<void>((resolve) => {
              const timer = window.setTimeout(resolve, reconnectDelay);
              controller.signal.addEventListener(
                "abort",
                () => {
                  window.clearTimeout(timer);
                  resolve();
                },
                { once: true },
              );
            });
            reconnectDelay = Math.min(reconnectDelay * 2, 2_000);
          }
        } catch {
          if (!controller.signal.aborted) setMessagesError("工作过程同步失败，请重新连接。");
        } finally {
          followsRef.current.delete(runId);
          followedRunIdsRef.current.delete(runId);
          setRunConnections((previous) => ({ ...previous, [runId]: false }));
          await refreshThreads();
          if (selectedThreadIdRef.current === threadId) {
            await reloadMessages();
          }
        }
      })();
    },
    [projectId, refreshThreads, reloadMessages],
  );

  const goalScope = `${projectId}:${draftId}:${draftRevision}:${selectedThreadId}`;
  const latestGoalId = goalHints[selectedThreadId ?? 0] ?? [...messages].reverse().find((message) => message.run?.goal_id)?.run?.goal_id ?? null;
  const lastGoalSignature = useRef("");
  const onGoalChange = useCallback((current: PublicGoalSession) => {
    const signature = `${current.goal_id}:${current.revision}:${current.status}:${current.active_run_id}`;
    if (lastGoalSignature.current !== signature) {
      lastGoalSignature.current = signature;
      void reloadMessages();
    }
    if (current.active_run_id && selectedThreadId !== null) {
      void getAgentRun(LOCAL_ACTOR_ID, projectId, current.active_run_id).then((run) => {
        if (selectedThreadIdRef.current === selectedThreadId && ACTIVE_RUN_STATUSES.has(run.status)) startFollow(run, selectedThreadId);
      }).catch(() => undefined);
    }
  }, [projectId, reloadMessages, selectedThreadId, startFollow]);
  const goalFeedback = useAgentGoalFeedback(projectId, goalScope, latestGoalId, onGoalChange,
    (id) => { if (selectedThreadId !== null) setGoalHints((previous) => ({ ...previous, [selectedThreadId]: id })); });
  const goal = goalFeedback.goal;
  const refreshGoal = goalFeedback.refresh;
  const canIntervene = Boolean(goalFeedback.connected && goal && (deliveryMode === "replace" ? goal.can_replace : goal.can_steer));
  const canFollowUp = Boolean(goalFeedback.connected && goal?.can_follow_up);

  useEffect(() => {
    const pending = pendingAssistantRun(messages);
    if (pending === null || selectedThreadId === null) return;
    startFollow(pending.run, selectedThreadId);
  }, [messages, selectedThreadId, startFollow]);

  useEffect(() => {
    let active = true;
    for (const message of messages) {
      const run = message.run;
      if (!run || !TERMINAL_RUN_STATUSES.has(run.status) || feedbackRef.current[run.run_id]) continue;
      void listAgentRunFeedback(LOCAL_ACTOR_ID, projectId, run.run_id).then((events) => {
        if (!active || feedbackRef.current[run.run_id]) return;
        const restored = events.reduce(reduceFeedback, emptyFeedback());
        feedbackRef.current[run.run_id] = restored;
        setFeedback((previous) => ({ ...previous, [run.run_id]: restored }));
      }).catch(() => undefined);
    }
    return () => { active = false; };
  }, [messages, projectId]);

  const pendingEntry = pendingAssistantRun(messages);
  const pendingLiveRun =
    pendingEntry === null
      ? null
      : (liveRuns[pendingEntry.run.run_id] ?? pendingEntry.run);
  const busy =
    pendingLiveRun !== null && ACTIVE_RUN_STATUSES.has(pendingLiveRun.status);
  const attentionIds = pendingLiveRun && busy && runConnections[pendingLiveRun.run_id] &&
    (!goal || (goalFeedback.connected && ["running", "interpreting"].includes(goal.status)))
    ? activeFeedbackRefs(feedback[pendingLiveRun.run_id], draftId, draftRevision) : [];
  const attentionKey = JSON.stringify(attentionIds);
  useEffect(() => {
    onAgentFocus?.(JSON.parse(attentionKey) as string[]);
    return () => onAgentFocus?.([]);
  }, [attentionKey, onAgentFocus, selectedThreadId, draftId, draftRevision]);
  const finishing =
    pendingLiveRun !== null && TERMINAL_RUN_STATUSES.has(pendingLiveRun.status);

  const inputDisabled =
    selectedThreadId === null ||
    (latestGoalId !== null && !goalFeedback.connected) ||
    threadsLoading ||
    (busy && !canIntervene) || sending ||
    (finishing && !canIntervene && !canFollowUp) ||
    (goal !== null && !["completed", "cancelled", "failed", "superseded"].includes(goal.status) && !canIntervene) ||
    (goal?.status === "stale" && deliveryMode !== "replace" && resumeAcknowledgement !== goalScope) ||
    creatingThread ||
    threads.find((thread) => thread.thread_id === selectedThreadId)?.status ===
      "archived";

  function upsertThread(thread: AgentThreadView) {
    setThreads((previous) => [
      thread,
      ...previous.filter((row) => row.thread_id !== thread.thread_id),
    ]);
    setThreadMenuRows((previous) => [
      thread,
      ...previous.filter((row) => row.thread_id !== thread.thread_id),
    ]);
  }

  async function createThread(): Promise<AgentThreadView | null> {
    if (creatingThread) return null;
    setCreatingThread(true);
    setMessagesError(null);
    try {
      const created = await createAgentThread(
        LOCAL_ACTOR_ID,
        projectId,
        draftId,
        draftRevision,
      );
      upsertThread(created);
      setMessagesLoading(true);
      setSelectedThreadId(created.thread_id);
      onContinueInDesk();
      return created;
    } catch (caught) {
      setMessagesError(errorMessage(caught));
      return null;
    } finally {
      setCreatingThread(false);
    }
  }

  async function renameThread(thread: AgentThreadView, title: string) {
    const updated = await updateAgentThread(
      LOCAL_ACTOR_ID,
      projectId,
      thread.thread_id,
      draftId,
      draftRevision,
      { title },
    );
    upsertThread(updated);
  }

  async function setThreadPinned(thread: AgentThreadView, isPinned: boolean) {
    const updated = await updateAgentThread(
      LOCAL_ACTOR_ID,
      projectId,
      thread.thread_id,
      draftId,
      draftRevision,
      { is_pinned: isPinned },
    );
    upsertThread(updated);
  }

  async function setThreadArchived(thread: AgentThreadView, archived: boolean) {
    const updated = await updateAgentThread(
      LOCAL_ACTOR_ID,
      projectId,
      thread.thread_id,
      draftId,
      draftRevision,
      { archived },
    );
    upsertThread(updated);
    if (archived && selectedThreadId === thread.thread_id) {
      setSelectedThreadId(null);
      setMessages([]);
    }
  }

  const send = useCallback(
    async (prompt: string, routingHint?: AgentChatRoutingHint) => {
      const content = prompt.trim();
      const threadId = selectedThreadId;
      if (!content || inputDisabled || threadId === null || sendingRef.current) return;
      const frozenFocus = composerFocus(composers[threadId] ?? newComposerEntry(focus));
      sendingRef.current = true;
      setSending(true);
      setSendingText({ threadId, text: content });
      setSendError(null);
      setMessagesError(null);
      try {
        const result = await sendAgentMessage(
          LOCAL_ACTOR_ID,
          projectId,
          threadId,
          draftId,
          draftRevision,
          content,
          "deepseek",
          frozenFocus,
          routingHint ?? { entrypoint: "free_text" },
          goal && (canIntervene || canFollowUp) ? {
            delivery_mode: canFollowUp && !canIntervene ? "follow_up" : deliveryMode,
            expected_goal_id: goal.goal_id, expected_goal_revision: goal.revision,
          } : undefined,
        );
        if (result.goal) setGoalHints((previous) => ({ ...previous, [threadId]: result.goal!.goal_id }));
        refreshGoal();
        dispatchComposer({ type: "sent", threadId, candidate: focus, text: prompt });
        if (selectedThreadIdRef.current === threadId) {
          messagesRequestRef.current += 1;
          setMessages((previous) =>
            mergeMessages(previous, [
              result.user_message,
              result.assistant_message,
            ]),
          );
        }
        void refreshThreads();
        if (result.assistant_message.run !== null) {
          startFollow(result.assistant_message.run, threadId);
        }
      } catch (caught) {
        setSendError({ threadId, message: errorMessage(caught) });
      } finally {
        sendingRef.current = false;
        setSending(false);
        setSendingText(null);
      }
    },
    [
      inputDisabled, goal, canIntervene, canFollowUp, deliveryMode, refreshGoal,
      composers,
      draftId,
      draftRevision,
      focus,
      projectId,
      refreshThreads,
      selectedThreadId,
      startFollow,
    ],
  );

  const handledKickoffRef = useRef<number | null>(null);
  useEffect(() => {
    if (kickoff === null) { handledKickoffRef.current = null; return; }
    if (handledKickoffRef.current === kickoff.id) return;
    if (selectedThreadId === null || busy || finishing || threadsLoading) return;
    const timer = window.setTimeout(
      () => {
        handledKickoffRef.current = kickoff.id;
        void send(kickoff.prompt, kickoff.routingHint);
      },
      0,
    );
    return () => window.clearTimeout(timer);
  }, [
    busy,
    finishing,
    kickoff,
    selectedThreadId,
    send,
    threadsLoading,
  ]);

  function retryMessage(message: PublicAgentMessage) {
    const previousUser = [...messages]
      .sort((a, b) => b.sequence - a.sequence)
      .find(
        (candidate) =>
          candidate.role === "user" &&
          candidate.status === "completed" &&
          candidate.sequence < message.sequence &&
          candidate.body !== null,
      );
    if (previousUser?.body) void send(previousUser.body);
  }

  function updatePatchSet(patchSet: PublicPatchSet) {
    setMessages((previous) =>
      previous.map((message) =>
        message.patch?.patch_id === patchSet.patch_id
          ? { ...message, patch: patchSet }
          : message,
      ),
    );
  }

  async function applyPatchSet(
    patchSet: PublicPatchSet,
    changeIds: number[] | null,
    confirmation: {
      confirmationToken?: string;
      acceptedWarningIds?: string[];
      confirmationNote?: string;
    } = {},
  ) {
    if (patchBusyId !== null) return;
    const scope = patchScopeRef.current;
    if (!scope.active) return;
    setPatchBusyId(patchSet.patch_id);
    setPatchError(null);
    setMessagesError(null);
    try {
      const result = await applyAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_id,
        draftId,
        patchSet.base_revision,
        changeIds,
        confirmation.confirmationToken,
        confirmation.acceptedWarningIds ?? [],
        confirmation.confirmationNote,
      );
      if (!scope.active) return;
      updatePatchSet(result.patch);
      if (result.goal && selectedThreadId !== null) setGoalHints((previous) => ({ ...previous, [selectedThreadId]: result.goal!.goal_id }));
      if (result.continuation_run && selectedThreadId !== null) startFollow(result.continuation_run, selectedThreadId);
      refreshGoal();
      setRevisionByPatch((previous) => ({
        ...previous,
        [patchSet.patch_id]: result.revision,
      }));
      await onDraftChanged();
      if (!scope.active) return;
      await reloadMessages();
    } catch (caught) {
      if (!scope.active) return;
      const message = errorMessage(caught);
      setPatchError(message);
      setMessagesError(message);
    } finally {
      if (scope.active) setPatchBusyId(null);
    }
  }

  async function simulatePatchSet(
    patchSet: PublicPatchSet,
    changeIds: number[] | null,
    acceptedWarningIds: string[] = [],
    confirmationNote?: string,
  ): Promise<PublicPatchReviewResult | null> {
    if (patchBusyId !== null) return null;
    const scope = patchScopeRef.current;
    if (!scope.active) return null;
    setPatchBusyId(patchSet.patch_id);
    setPatchError(null);
    setMessagesError(null);
    try {
      const result = await simulateAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_id,
        draftId,
        patchSet.base_revision,
        changeIds,
        acceptedWarningIds,
        confirmationNote,
      );
      return scope.active ? result : null;
    } catch (caught) {
      if (!scope.active) return null;
      const message = errorMessage(caught);
      setPatchError(message);
      setMessagesError(message);
      return null;
    } finally {
      if (scope.active) setPatchBusyId(null);
    }
  }

  async function undoPatchSet(patchSet: PublicPatchSet) {
    if (patchBusyId !== null || !patchSet.actions.can_undo) return;
    const scope = patchScopeRef.current;
    if (!scope.active) return;
    setPatchBusyId(patchSet.patch_id);
    setPatchError(null);
    setMessagesError(null);
    try {
      const result = await undoAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_id,
        draftId,
        revisionByPatch[patchSet.patch_id] ?? draftRevision,
      );
      if (!scope.active) return;
      updatePatchSet(result.patch);
      setRevisionByPatch((previous) => ({
        ...previous,
        [patchSet.patch_id]: result.revision,
      }));
      await onDraftChanged();
      if (!scope.active) return;
      await reloadMessages();
    } catch (caught) {
      if (!scope.active) return;
      const message = errorMessage(caught);
      setPatchError(message);
      setMessagesError(message);
    } finally {
      if (scope.active) setPatchBusyId(null);
    }
  }

  async function redoPatchSet(patchSet: PublicPatchSet) {
    if (patchBusyId !== null || !patchSet.actions.can_redo) return;
    const scope = patchScopeRef.current;
    if (!scope.active) return;
    setPatchBusyId(patchSet.patch_id);
    setPatchError(null);
    try {
      const result = await redoAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_id,
        draftId,
        revisionByPatch[patchSet.patch_id] ?? draftRevision,
      );
      if (!scope.active) return;
      updatePatchSet(result.patch);
      setRevisionByPatch((previous) => ({
        ...previous,
        [patchSet.patch_id]: result.revision,
      }));
      await onDraftChanged();
      if (!scope.active) return;
      await reloadMessages();
    } catch (caught) {
      if (!scope.active) return;
      setPatchError(errorMessage(caught));
    } finally {
      if (scope.active) setPatchBusyId(null);
    }
  }

  const selectedThread = useMemo(
    () =>
      threads.find((thread) => thread.thread_id === selectedThreadId) ?? null,
    [selectedThreadId, threads],
  );

  const searchThreads = useCallback(
    async (query: string, includeArchived: boolean) => {
      await refreshThreads({ query, includeArchived });
    },
    [refreshThreads],
  );

  const inspectorPatches = useMemo(
    () => messages.flatMap((message) =>
      message.patch ? [{ message, patchSet: message.patch }] : [],
    ),
    [messages],
  );
  const inspectorFindings = useMemo(
    () => messages.flatMap((message) =>
      agentAuditFindingsFor(message).map((finding) => ({ message, finding })),
    ),
    [messages],
  );
  const inspectorProps = {
    busyPatchSetId: patchBusyId,
    patchError,
    findings: inspectorFindings,
    focusFindingId: focusFindingId ?? localFocusFindingId,
    focusPatchSetId: focusPatchSetId ?? localFocusPatchSetId,
    onApply: (
      patchSet: PublicPatchSet,
      changeIds: number[] | null,
      confirmation?: {
        confirmationToken?: string;
        acceptedWarningIds?: string[];
        confirmationNote?: string;
      },
    ) => void applyPatchSet(patchSet, changeIds, confirmation),
    onSimulate: (
      patchSet: PublicPatchSet,
      changeIds: number[] | null,
      warningIds?: string[],
      note?: string,
    ) => simulatePatchSet(patchSet, changeIds, warningIds, note),
    onLocateObject: onLocateObject,
    onRetry: retryMessage,
    onUndo: (patchSet: PublicPatchSet) => void undoPatchSet(patchSet),
    onRedo: (patchSet: PublicPatchSet) => void redoPatchSet(patchSet),
    patches: inspectorPatches,
  };
  const inspectorHasContent = inspectorPatches.length > 0 ||
    inspectorFindings.length > 0 || Boolean(patchError);
  const focusPatch = (id: number) => {
    setLocalFocusPatchSetId(id);
    setLocalFocusFindingId(null);
    onFocusPatch(id);
  };
  function renderMessagePatch(message: PublicAgentMessage, conversation = false) {
    return <AgentMessagePatch message={message} inspector={inspectorProps}
      scope={`${selectedThreadId}:${draftId}:${draftRevision}`} reviews={patchReviews} onReviewsChange={setPatchReviews}
      conversation={conversation}
      onDetails={conversation && message.patch ? () => focusPatch(message.patch!.patch_id) : undefined}
      onAdjust={conversation && message.patch ? () => {
        setDraft(`${draft.trim() ? `${draft}\n\n` : ""}请调整「${message.patch!.title}」这组修改建议：`);
        setPatchFocusRequest((previous) => previous + 1);
      } : undefined}
    />;
  }
  const inspectorPortal = inspectorHost && inspectorHasContent
    ? createPortal(
        <WorkbenchAgentInspector
          {...inspectorProps}
          renderPatch={(message) => renderMessagePatch(message)}
          onFocusPatch={(id) => { setLocalFocusPatchSetId(id); setLocalFocusFindingId(null); onFocusPatch(id); }}
        />,
        inspectorHost,
      )
    : null;
  const threadPortal = threadHost
    ? createPortal(
        <WorkbenchAgentThreadMenu
          disabled={threadsLoading || creatingThread}
          onCreate={createThread}
          onRename={renameThread}
          onSearch={searchThreads}
          onSelect={(thread) => {
            upsertThread(thread);
            if (thread.thread_id !== selectedThreadId) {
              setMessages([]);
              setSelectedThreadId(thread.thread_id);
            }
            void reloadMessages(thread.thread_id);
            onContinueInDesk();
          }}
          onSetArchived={setThreadArchived}
          onSetPinned={setThreadPinned}
          selectedThread={selectedThread}
          selectedThreadId={selectedThreadId}
          threads={threadMenuRows}
          placement="toolbar"
        />,
        threadHost,
      )
    : null;
  const presentation = (
        <WorkbenchAgentDesk
        composer={
          <WorkbenchAgentComposer
            busy={busy}
            deliveryControl={goal && (goal.can_steer || goal.can_replace) ? <select
              aria-label="运行中消息用途" value={deliveryMode} disabled={!goalFeedback.connected || sending}
              onChange={(event) => setDeliveryMode(event.target.value as "steer" | "replace")}
            >
              <option value="steer" disabled={!goal.can_steer}>补充当前要求</option>
              <option value="replace" disabled={!goal.can_replace}>替换当前任务</option>
            </select> : null}
            disabled={disabled || threadsLoading || selectedThreadId === null}
            submitDisabled={inputDisabled}
            draft={draft}
            onDraftChange={setDraft}
            onSend={() => {
              if (!draft.trim() || inputDisabled || disabled) return;
              onContinueInDesk();
              void send(draft);
            }}
            focusRequest={focusRequest + patchFocusRequest}
            surface="dock"
          />
        }
        conversation={
          <WorkbenchAgentConversation
            liveRuns={liveRuns}
            feedback={feedback}
            sendingText={sendingText?.threadId === selectedThreadId ? sendingText.text : null}
            sendError={sendError?.threadId === selectedThreadId ? sendError.message : null}
            patchError={patchError}
            messages={messages}
            messagesError={messagesError}
            messagesLoading={messagesLoading}
            onReconnect={() => void bootstrap()}
            onReloadMessages={() => void reloadMessages()}
            onRetryMessage={retryMessage}
            selectedThreadTitle={selectedThread?.title ?? null}
            threadsError={threadsError}
            threadsLoading={threadsLoading}
            onFocusPatch={focusPatch}
            renderPatch={(message) => renderMessagePatch(message, true)}
            onFocusFinding={onFocusFinding}
            taskControls={goal || pendingLiveRun ? <div className={styles.agentLiveStatus} aria-label="Agent 状态">
              <span role="status">{goal ? goalLabel(goal) : pendingLiveRun?.status === "queued" ? "回复已排队" : busy ? "正在处理你的要求" : "正在同步结果"}</span>
              {goal && !goalFeedback.connected ? <small>正在重新连接，暂不能提交新要求</small> : null}
              {goal?.status === "stale" && resumeAcknowledgement !== goalScope ? <button type="button" onClick={() => setResumeAcknowledgement(goalScope)}>基于当前工作稿继续</button> : null}
              {goal?.active_patch_id ? <button type="button" onClick={() => focusPatch(goal.active_patch_id!)}>审阅修改建议</button> : null}
              {goalFeedback.deliveries.map((delivery) => <small key={delivery.delivery_id}>
                {delivery.mode === "replace" ? "替换要求" : "补充要求"} · {delivery.status === "queued" ? "已收到，等待当前步骤结束" : delivery.status === "claimed" ? "正在处理" : delivery.status === "consumed" ? "已生效" : "未生效"}
              </small>)}
              {(goal?.cancellable || (!goal && pendingLiveRun?.cancellable)) ? <button type="button" onClick={() => {
                const action = goal ? cancelAgentGoal(LOCAL_ACTOR_ID, projectId, goal.goal_id) : cancelAgentRun(LOCAL_ACTOR_ID, projectId, pendingLiveRun!.run_id);
                void action.then(() => { refreshGoal(); return reloadMessages(); }).catch((caught: unknown) => setMessagesError(errorMessage(caught)));
              }}>停止{goal ? "目标" : "回复"}</button> : null}
            </div> : null}
          />
        }
        prompts={null}
        surface={surface}
        taskStrip={null}
      />
      );
  const wrappedPresentation = (
    <WorkbenchAgentSurface surface={surface} working={busy && (!goal || ["running", "interpreting"].includes(goal.status))}>{presentation}</WorkbenchAgentSurface>
  );

  return (
    <>
      {threadPortal}
      <WorkbenchAgentPortal host={presentationHost}>{wrappedPresentation}</WorkbenchAgentPortal>
      {details ? <WorkbenchAgentDetailPortals details={details} inspector={inspectorProps}
        scope={`${selectedThreadId}:${draftId}:${draftRevision}`} loading={messagesLoading}
        reviews={patchReviews} onReviewsChange={setPatchReviews}
        onAddContext={(items) => { for (const item of items) dispatchComposer({ type: "add", threadId: composerThreadId, candidate: focus, item }); }}
      /> : null}
      {details ? null : inspectorPortal ?? (
        inspectorFindings.length > 0 ||
        inspectorPatches.length > 0 ||
        localFocusPatchSetId !== null ||
        localFocusFindingId !== null
          ? (
              <WorkbenchAgentInspector
                {...inspectorProps}
                renderPatch={(message) => renderMessagePatch(message)}
                onFocusPatch={focusPatch}
              />
            )
          : null
      )}
    </>
  );
}
