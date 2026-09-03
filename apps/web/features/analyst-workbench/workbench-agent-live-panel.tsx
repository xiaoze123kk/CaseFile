"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type {
  PublicAgentEvent,
  PublicAgentMessage,
  PublicAgentRun,
  PublicPatchReviewResult,
  PublicPatchSet,
} from "@casefile/contracts";

import {
  applyAgentPatchSet,
  createAgentThread,
  errorMessage,
  getAgentRun,
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
import type { AgentSurface } from "./workbench-agent-surface";
import { WorkbenchAgentThreadMenu } from "./workbench-agent-thread-menu";
import { WorkbenchAgentInspector } from "./workbench-agent-inspector";

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

const focusViewLabels: Record<string, string> = {
  timeline: "时间线",
  relations: "关系图",
  reasoning: "推理分析",
  map: "地图",
  export: "导出预览",
  compile: "编译中心",
  evidence: "证据对比",
};

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
  referenceLabels,
  onLocateObject,
  onFocusPatch = () => undefined,
  focusPatchSetId,
  focusFindingId,
  inspectorHost,
  threadHost,
  onDraftChanged,
  disabled = false,
  onClose,
  surface = "desk",
  onContinueInDesk = () => undefined,
  focusRequest = 0,
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
  onDraftChanged: () => Promise<void>;
  disabled?: boolean;
  onClose: () => void;
  surface?: AgentSurface;
  onContinueInDesk?: () => void;
  focusRequest?: number;
}) {
  const [threads, setThreads] = useState<AgentThreadView[]>([]);
  const [threadMenuRows, setThreadMenuRows] = useState<AgentThreadView[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null);
  const [messages, setMessages] = useState<PublicAgentMessage[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [liveRuns, setLiveRuns] = useState<Record<number, PublicAgentRun>>({});
  const [creatingThread, setCreatingThread] = useState(false);
  const [patchBusyId, setPatchBusyId] = useState<number | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [localFocusPatchSetId, setLocalFocusPatchSetId] = useState<number | null>(null);
  const [localFocusFindingId, setLocalFocusFindingId] = useState<string | null>(null);
  const [revisionByPatch, setRevisionByPatch] = useState<Record<number, number>>({});

  const followsRef = useRef(new Map<number, AbortController>());
  const followedRunIdsRef = useRef(new Set<number>());
  const messagesRequestRef = useRef(0);
  const selectedThreadIdRef = useRef<number | null>(null);

  useEffect(() => {
    selectedThreadIdRef.current = selectedThreadId;
  }, [selectedThreadId]);

  useEffect(
    () => () => {
      for (const controller of followsRef.current.values()) controller.abort();
    },
    [],
  );

  useEffect(() => {
    if (surface !== "desk") return;
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    }
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose, surface]);

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
  }, [projectId, selectedThreadId]);

  const startFollow = useCallback(
    (initialRun: PublicAgentRun, threadId: number) => {
      const runId = initialRun.run_id;
      if (followedRunIdsRef.current.has(runId)) return;
      followedRunIdsRef.current.add(runId);
      setLiveRuns((previous) => ({ ...previous, [runId]: initialRun }));
      const controller = new AbortController();
      followsRef.current.set(runId, controller);

      function receiveEvent(event: PublicAgentEvent) {
        if (event.event === "run.context" || event.event === "run.verification") return;
        setLiveRuns((previous) => {
          const current = previous[runId] ?? initialRun;
          if (event.event === "run.accepted" || event.event === "run.completed") {
            return { ...previous, [runId]: event.run };
          }
          if (event.event === "run.activity") {
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
          while (!controller.signal.aborted) {
            try {
              cursor = await streamAgentRunEvents(
                LOCAL_ACTOR_ID,
                projectId,
                runId,
                receiveEvent,
                controller.signal,
                cursor,
              );
              reconnectDelay = 250;
            } catch {
              if (controller.signal.aborted) break;
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
        } finally {
          followsRef.current.delete(runId);
          await refreshThreads();
          if (selectedThreadIdRef.current === threadId) {
            await reloadMessages();
          }
        }
      })();
    },
    [projectId, refreshThreads, reloadMessages],
  );

  useEffect(() => {
    const pending = pendingAssistantRun(messages);
    if (pending === null || selectedThreadId === null) return;
    startFollow(pending.run, selectedThreadId);
  }, [messages, selectedThreadId, startFollow]);

  const pendingEntry = pendingAssistantRun(messages);
  const pendingLiveRun =
    pendingEntry === null
      ? null
      : (liveRuns[pendingEntry.run.run_id] ?? pendingEntry.run);
  const busy =
    pendingLiveRun !== null && ACTIVE_RUN_STATUSES.has(pendingLiveRun.status);
  const finishing =
    pendingLiveRun !== null && TERMINAL_RUN_STATUSES.has(pendingLiveRun.status);

  const inputDisabled =
    selectedThreadId === null ||
    threadsLoading ||
    busy ||
    finishing ||
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
      if (!content || busy || finishing || threadId === null) return;
      setDraft("");
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
          focus,
          routingHint ?? { entrypoint: "free_text" },
        );
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
        setMessagesError(errorMessage(caught));
      }
    },
    [
      busy,
      draftId,
      draftRevision,
      finishing,
      focus,
      projectId,
      refreshThreads,
      selectedThreadId,
      startFollow,
    ],
  );

  const handledKickoffRef = useRef<number | null>(null);
  useEffect(() => {
    if (kickoff === null || handledKickoffRef.current === kickoff.id) return;
    if (selectedThreadId === null || busy || finishing || threadsLoading) return;
    handledKickoffRef.current = kickoff.id;
    const timer = window.setTimeout(
      () => void send(kickoff.prompt, kickoff.routingHint),
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
      updatePatchSet(result.patch);
      setRevisionByPatch((previous) => ({
        ...previous,
        [patchSet.patch_id]: result.revision,
      }));
      await onDraftChanged();
      await reloadMessages();
    } catch (caught) {
      const message = errorMessage(caught);
      setPatchError(message);
      setMessagesError(message);
    } finally {
      setPatchBusyId(null);
    }
  }

  async function simulatePatchSet(
    patchSet: PublicPatchSet,
    changeIds: number[] | null,
    acceptedWarningIds: string[] = [],
    confirmationNote?: string,
  ): Promise<PublicPatchReviewResult | null> {
    if (patchBusyId !== null) return null;
    setPatchBusyId(patchSet.patch_id);
    setPatchError(null);
    setMessagesError(null);
    try {
      return await simulateAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_id,
        draftId,
        patchSet.base_revision,
        changeIds,
        acceptedWarningIds,
        confirmationNote,
      );
    } catch (caught) {
      const message = errorMessage(caught);
      setPatchError(message);
      setMessagesError(message);
      return null;
    } finally {
      setPatchBusyId(null);
    }
  }

  async function undoPatchSet(patchSet: PublicPatchSet) {
    if (patchBusyId !== null || !patchSet.actions.can_undo) return;
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
      updatePatchSet(result.patch);
      setRevisionByPatch((previous) => ({
        ...previous,
        [patchSet.patch_id]: result.revision,
      }));
      await onDraftChanged();
      await reloadMessages();
    } catch (caught) {
      const message = errorMessage(caught);
      setPatchError(message);
      setMessagesError(message);
    } finally {
      setPatchBusyId(null);
    }
  }

  async function redoPatchSet(patchSet: PublicPatchSet) {
    if (patchBusyId !== null || !patchSet.actions.can_redo) return;
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
      updatePatchSet(result.patch);
      setRevisionByPatch((previous) => ({
        ...previous,
        [patchSet.patch_id]: result.revision,
      }));
      await onDraftChanged();
      await reloadMessages();
    } catch (caught) {
      setPatchError(errorMessage(caught));
    } finally {
      setPatchBusyId(null);
    }
  }

  const selectedThread = useMemo(
    () =>
      threads.find((thread) => thread.thread_id === selectedThreadId) ?? null,
    [selectedThreadId, threads],
  );
  const contextChips = useMemo(() => {
    const chips: string[] = [];
    const objectId = focus.object_ids[0];
    const eventId = focus.event_ids[0];
    const issueId = focus.validation_issue_ids[0];
    if (objectId) chips.push(referenceLabels.objects[objectId] ?? objectId);
    if (eventId) chips.push(referenceLabels.events[eventId] ?? eventId);
    if (issueId) chips.push(referenceLabels.issues[issueId] ?? issueId);
    if (focus.view && focusViewLabels[focus.view]) {
      chips.push(focusViewLabels[focus.view]);
    }
    return chips;
  }, [focus, referenceLabels]);

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
  const inspectorPortal = inspectorHost
    ? createPortal(
        <WorkbenchAgentInspector
          {...inspectorProps}
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
  const focusPatch = (id: number) => {
    setLocalFocusPatchSetId(id);
    setLocalFocusFindingId(null);
    onFocusPatch(id);
  };
  if (surface === "dock") {
    return (
      <>
        <WorkbenchAgentComposer
          busy={busy}
          contextChips={contextChips}
          disabled={disabled}
          draft={draft}
          onDraftChange={setDraft}
          onSend={() => {
            if (!draft.trim() || inputDisabled) return;
            onContinueInDesk();
            void send(draft);
          }}
          focusRequest={focusRequest}
          submitDisabled={inputDisabled}
          surface="dock"
        />
        {inspectorPortal}
        {threadPortal}
      </>
    );
  }

  return (
    <>
      {threadPortal}
      <WorkbenchAgentDesk
        composer={
          <WorkbenchAgentComposer
            busy={busy}
            contextChips={contextChips}
            disabled={inputDisabled}
            draft={draft}
            onDraftChange={setDraft}
            onSend={() => void send(draft)}
            focusRequest={focusRequest}
            surface="dock"
          />
        }
        conversation={
          <WorkbenchAgentConversation
            liveRuns={liveRuns}
            messages={messages}
            messagesError={messagesError}
            messagesLoading={messagesLoading}
            onReconnect={() => void bootstrap()}
            onReloadMessages={() => void reloadMessages()}
            onRetryMessage={retryMessage}
            selectedThreadTitle={selectedThread?.title ?? null}
            threadsError={threadsError}
            threadsLoading={threadsLoading}
          />
        }
        prompts={null}
        surface="desk"
        taskStrip={null}
      />
      {inspectorPortal ?? (
        inspectorFindings.length > 0 ||
        inspectorPatches.length > 0 ||
        localFocusPatchSetId !== null ||
        localFocusFindingId !== null
          ? (
              <WorkbenchAgentInspector
                {...inspectorProps}
                onFocusPatch={focusPatch}
              />
            )
          : null
      )}
    </>
  );
}
