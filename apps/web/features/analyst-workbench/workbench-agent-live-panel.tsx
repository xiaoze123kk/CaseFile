"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  applyAgentPatchSet,
  createAgentThread,
  errorMessage,
  listAgentMessages,
  listAgentThreads,
  sendAgentMessage,
  sendAgentRoutingFeedback,
  undoAgentPatchSet,
  type AgentAuditFindingView,
  type AgentChatFocus,
  type AgentChatRoutingHint,
  type AgentMessageView,
  type AgentPatchSetView,
  type AgentRoutingCorrectIntent,
  type AgentSuggestedView,
  type AgentThreadView,
  type TaskEventView,
  type TaskView,
} from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";
import {
  cancelTask,
  TaskCancelledError,
  waitForTask,
} from "@/features/case-session/case-session-api";

import styles from "./analyst-workbench.module.css";
import { agentPromptPresets } from "./workbench-agent-presets";
import { WorkbenchIcon } from "./workbench-icon";

const ACTIVE_TASK_STATUSES = new Set<TaskView["status"]>([
  "queued",
  "running",
  "cancelling",
]);

const TERMINAL_TASK_STATUSES = new Set<TaskView["status"]>([
  "succeeded",
  "failed",
  "cancelled",
]);

const agentViewLabels: Record<AgentSuggestedView, string> = {
  timeline: "时间线",
  relations: "关系图",
  reasoning: "推理分析",
  map: "地图",
  export: "导出预览",
  compile: "编译中心",
  evidence: "证据对比",
};

const patchStatusLabels: Record<AgentPatchSetView["status"], string> = {
  pending: "待审阅",
  stale: "已失效",
  applied: "已应用",
  undone: "已撤销",
  rejected: "已拒绝",
};

const operationDecisionLabels: Record<string, string> = {
  pending: "待决定",
  accepted: "已采纳",
  rejected: "已拒绝",
};

const routingIntentLabels: Record<AgentRoutingCorrectIntent, string> = {
  question: "问答",
  analysis: "分析",
  explain_issue: "问题解释",
  edit_request: "修改请求",
  validate_request: "验证请求",
  logic_audit: "逻辑漏洞复查",
  unsupported_action: "不可执行",
  clarify: "需要澄清",
  out_of_scope: "超出范围",
};

const routingSourceLabels: Record<string, string> = {
  rule_preset: "预设路由",
  rule_ui: "界面路由",
  llm: "AI 理解",
  fallback: "降级路由",
};

const auditFindingKindLabels = {
  dangling_ref: "断链",
  contradiction: "矛盾",
  temporal: "时序错误",
  motivation_gap: "动机缺口",
  scope_gap: "范围缺口",
} as const;

const auditFindingSeverityLabels = {
  S1: "致命",
  S2: "主要",
  S3: "次要",
} as const;

function auditFindingsFor(message: AgentMessageView): AgentAuditFindingView[] {
  const result = message.task?.result;
  if (result === null || result === undefined) return [];
  if (typeof result !== "object" || !("audit_findings" in result)) return [];
  const findings = (result as { audit_findings?: unknown }).audit_findings;
  if (!Array.isArray(findings)) return [];
  return findings.filter(
    (finding): finding is AgentAuditFindingView =>
      typeof finding === "object" &&
      finding !== null &&
      typeof (finding as AgentAuditFindingView).finding_id === "string" &&
      typeof (finding as AgentAuditFindingView).kind === "string" &&
      typeof (finding as AgentAuditFindingView).severity === "string" &&
      typeof (finding as AgentAuditFindingView).title === "string",
  );
}

function routingSummaryFor(message: AgentMessageView): {
  route_source: string | null;
  intent: string | null;
} | null {
  const result = message.task?.result;
  if (result === null || result === undefined) return null;
  const routing =
    typeof result === "object" && result !== null && "routing" in result
      ? (result as { routing?: unknown }).routing
      : null;
  if (routing === null || routing === undefined || typeof routing !== "object") {
    return null;
  }
  const summary = routing as {
    route_source?: unknown;
    intent?: unknown;
  };
  return {
    route_source:
      typeof summary.route_source === "string" ? summary.route_source : null,
    intent: typeof summary.intent === "string" ? summary.intent : null,
  };
}

function displayValue(value: unknown): string {
  const text =
    typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 96 ? `${text.slice(0, 95)}…` : text;
}

function stageLabel(task: TaskView | null): string {
  if (task === null) return "已排队";
  if (task.status === "queued") return "任务已排队";
  if (task.status === "running") return task.stage || "正在分析卷宗";
  if (task.status === "cancelling") return "正在取消";
  return "正在整理回复";
}

interface ContextOccupancy {
  usedTokens: number;
  budgetTokens: number | null;
}

function contextOccupancyFromEvent(
  event: TaskEventView,
): ContextOccupancy | null {
  if (event.event_type !== "context.built") return null;
  const payload = event.payload;
  if (typeof payload !== "object" || payload === null) return null;
  const usedTokens = (payload as { used_tokens?: unknown }).used_tokens;
  const budgetTokens = (payload as { budget_tokens?: unknown }).budget_tokens;
  return {
    usedTokens: typeof usedTokens === "number" ? usedTokens : 0,
    budgetTokens: typeof budgetTokens === "number" ? budgetTokens : null,
  };
}

function mergeMessages(
  previous: AgentMessageView[],
  incoming: AgentMessageView[],
): AgentMessageView[] {
  const byId = new Map(previous.map((message) => [message.message_id, message]));
  for (const message of incoming) byId.set(message.message_id, message);
  return [...byId.values()].sort((a, b) => a.sequence_no - b.sequence_no);
}

function pendingAssistantTask(
  messages: AgentMessageView[],
): { message: AgentMessageView; task: TaskView } | null {
  const pending = messages.find(
    (message) =>
      message.role === "assistant" &&
      message.status === "pending" &&
      message.task !== null &&
      ACTIVE_TASK_STATUSES.has(message.task.status),
  );
  return pending?.task ? { message: pending, task: pending.task } : null;
}

export function AgentLivePanel({
  projectId,
  draftId,
  draftRevision,
  focus,
  kickoff,
  referenceLabels,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
  onLocateView,
  onDraftChanged,
  onClose,
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
  onLocateIssue: (issueId: string) => void;
  onLocateView: (view: AgentSuggestedView) => void;
  onDraftChanged: () => Promise<void>;
  onClose: () => void;
}) {
  const [threads, setThreads] = useState<AgentThreadView[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null);
  const [messages, setMessages] = useState<AgentMessageView[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [liveTasks, setLiveTasks] = useState<Record<number, TaskView>>({});
  const [creatingThread, setCreatingThread] = useState(false);
  const [patchBusyId, setPatchBusyId] = useState<number | null>(null);
  const [feedbackByMessage, setFeedbackByMessage] = useState<
    Record<number, AgentRoutingCorrectIntent>
  >({});
  const [contextByTask, setContextByTask] = useState<
    Record<number, ContextOccupancy | null>
  >({});

  const followsRef = useRef(new Map<number, AbortController>());
  const followedTaskIdsRef = useRef(new Set<number>());
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

  const refreshThreads = useCallback(async () => {
    try {
      const rows = await listAgentThreads(LOCAL_ACTOR_ID, projectId);
      setThreads(rows);
    } catch {
      // A background refresh must not overwrite the panel with an error toast.
    }
  }, [projectId]);

  const reloadMessages = useCallback(async () => {
    const threadId = selectedThreadIdRef.current;
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
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    if (selectedThreadId === null) return;
    let active = true;
    const requestId = ++messagesRequestRef.current;
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
    return () => {
      active = false;
    };
  }, [projectId, selectedThreadId]);

  const startFollow = useCallback(
    (taskRunId: number, threadId: number) => {
      if (followedTaskIdsRef.current.has(taskRunId)) return;
      followedTaskIdsRef.current.add(taskRunId);
      const controller = new AbortController();
      followsRef.current.set(taskRunId, controller);
      void (async () => {
        try {
          await waitForTask(projectId, taskRunId, (task) => {
            setLiveTasks((previous) => ({
              ...previous,
              [task.task_run_id]: task,
            }));
          }, controller.signal, (event) => {
            const occupancy = contextOccupancyFromEvent(event);
            if (occupancy !== null) {
              setContextByTask((previous) => ({
                ...previous,
                [taskRunId]: occupancy,
              }));
            }
          });
        } catch (caught) {
          if (caught instanceof TaskCancelledError) {
            setLiveTasks((previous) => ({
              ...previous,
              [caught.task.task_run_id]: caught.task,
            }));
          }
          // Provider/validation failures surface through the assistant
          // message's own task.failure after the authoritative reload below.
        } finally {
          followsRef.current.delete(taskRunId);
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
    const pending = pendingAssistantTask(messages);
    if (pending === null) return;
    startFollow(pending.task.task_run_id, pending.message.thread_id);
  }, [messages, startFollow]);

  const pendingEntry = pendingAssistantTask(messages);
  const pendingLiveTask =
    pendingEntry === null
      ? null
      : (liveTasks[pendingEntry.task.task_run_id] ?? pendingEntry.task);
  const busy =
    pendingLiveTask !== null && ACTIVE_TASK_STATUSES.has(pendingLiveTask.status);
  const finishing =
    pendingLiveTask !== null &&
    TERMINAL_TASK_STATUSES.has(pendingLiveTask.status);

  const inputDisabled =
    selectedThreadId === null ||
    threadsLoading ||
    busy ||
    finishing ||
    creatingThread;

  function upsertThread(thread: AgentThreadView) {
    setThreads((previous) => [
      thread,
      ...previous.filter((row) => row.thread_id !== thread.thread_id),
    ]);
  }

  async function createThread() {
    if (creatingThread) return;
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
      setSelectedThreadId(created.thread_id);
    } catch (caught) {
      setMessagesError(errorMessage(caught));
    } finally {
      setCreatingThread(false);
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
        upsertThread(result.thread);
        if (selectedThreadIdRef.current === threadId) {
          messagesRequestRef.current += 1;
          setMessages((previous) =>
            mergeMessages(previous, [
              result.user_message,
              result.assistant_message,
            ]),
          );
        }
        startFollow(result.task.task_run_id, result.thread.thread_id);
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
      selectedThreadId,
      startFollow,
    ],
  );

  const handledKickoffRef = useRef<number | null>(null);
  useEffect(() => {
    if (kickoff === null || handledKickoffRef.current === kickoff.id) return;
    if (selectedThreadId === null || busy || finishing || threadsLoading) return;
    handledKickoffRef.current = kickoff.id;
    void send(kickoff.prompt, kickoff.routingHint);
  }, [
    busy,
    finishing,
    kickoff,
    selectedThreadId,
    send,
    threadsLoading,
  ]);

  async function cancelCurrentTask() {
    if (pendingEntry === null) return;
    setMessagesError(null);
    try {
      const task = await cancelTask(projectId, pendingEntry.task.task_run_id);
      setLiveTasks((previous) => ({
        ...previous,
        [task.task_run_id]: task,
      }));
    } catch (caught) {
      setMessagesError(errorMessage(caught));
    }
  }

  function retryMessage(message: AgentMessageView) {
    const previousUser = [...messages]
      .sort((a, b) => b.sequence_no - a.sequence_no)
      .find(
        (candidate) =>
          candidate.role === "user" &&
          candidate.status === "completed" &&
          candidate.sequence_no < message.sequence_no &&
          candidate.content !== null,
      );
    if (previousUser?.content) void send(previousUser.content);
  }

  async function submitRoutingFeedback(
    message: AgentMessageView,
    correctIntent: AgentRoutingCorrectIntent,
  ) {
    setMessagesError(null);
    try {
      await sendAgentRoutingFeedback(
        LOCAL_ACTOR_ID,
        projectId,
        message.thread_id,
        message.message_id,
        correctIntent,
      );
      setFeedbackByMessage((previous) => ({
        ...previous,
        [message.message_id]: correctIntent,
      }));
    } catch (caught) {
      setMessagesError(errorMessage(caught));
      throw caught;
    }
  }

  function updatePatchSet(patchSet: AgentPatchSetView) {
    setMessages((previous) =>
      previous.map((message) =>
        message.patch_set?.patch_set_id === patchSet.patch_set_id
          ? { ...message, patch_set: patchSet }
          : message,
      ),
    );
  }

  async function applyPatchSet(
    patchSet: AgentPatchSetView,
    operationIds: number[],
  ) {
    if (patchBusyId !== null) return;
    setPatchBusyId(patchSet.patch_set_id);
    setMessagesError(null);
    try {
      const result = await applyAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_set_id,
        draftId,
        patchSet.base_draft_revision,
        operationIds,
      );
      updatePatchSet(result);
      await onDraftChanged();
      await reloadMessages();
    } catch (caught) {
      setMessagesError(errorMessage(caught));
    } finally {
      setPatchBusyId(null);
    }
  }

  async function undoPatchSet(patchSet: AgentPatchSetView) {
    if (
      patchBusyId !== null ||
      patchSet.applied_to_revision === null
    ) {
      return;
    }
    setPatchBusyId(patchSet.patch_set_id);
    setMessagesError(null);
    try {
      const result = await undoAgentPatchSet(
        LOCAL_ACTOR_ID,
        projectId,
        patchSet.patch_set_id,
        draftId,
        patchSet.applied_to_revision,
      );
      updatePatchSet(result);
      await onDraftChanged();
      await reloadMessages();
    } catch (caught) {
      setMessagesError(errorMessage(caught));
    } finally {
      setPatchBusyId(null);
    }
  }

  const promptsDisabled = inputDisabled || creatingThread;

  const selectedThread = useMemo(
    () =>
      threads.find((thread) => thread.thread_id === selectedThreadId) ?? null,
    [selectedThreadId, threads],
  );

  return (
    <section
      aria-label="卷宗统筹 Agent 对话"
      className={`${styles.agentPanel} ${styles.agentPanelLive}`}
    >
      <header className={styles.agentHeader}>
        <div>
          <span>卷宗统筹</span>
          <strong>Agent 对话</strong>
        </div>
        <button
          aria-label="关闭 Agent 对话"
          onClick={onClose}
          type="button"
        >
          <WorkbenchIcon name="close" />
        </button>
      </header>
      <div className={styles.agentThreadBar}>
        <select
          aria-label="选择 Agent 对话"
          className={styles.agentThreadSelect}
          disabled={threadsLoading || threads.length === 0}
          onChange={(event) => setSelectedThreadId(Number(event.target.value))}
          value={selectedThreadId ?? ""}
        >
          {threads.map((thread) => (
            <option key={thread.thread_id} value={thread.thread_id}>
              {thread.title}
              {thread.status === "archived" ? "（已归档）" : ""}
            </option>
          ))}
        </select>
        <button
          className={styles.agentThreadNew}
          disabled={creatingThread || threadsLoading}
          onClick={() => void createThread()}
          type="button"
        >
          {creatingThread ? "创建中…" : "新对话"}
        </button>
      </div>
      <div aria-live="polite" className={styles.agentMessages}>
        {threadsLoading ? (
          <p className={styles.agentThinking}>正在读取 Agent 对话…</p>
        ) : null}
        {!threadsLoading && threadsError ? (
          <div className={styles.agentFailure} role="status">
            <strong>无法连接 Agent</strong>
            <span>{threadsError}</span>
            <button onClick={() => void bootstrap()} type="button">
              重新连接
            </button>
          </div>
        ) : null}
        {messages.map((message) => {
          if (message.role === "system") return null;
          const liveTask =
            message.task === null
              ? null
              : (liveTasks[message.task.task_run_id] ?? message.task);
          const patchSet = message.patch_set;
          const auditFindings = auditFindingsFor(message);
          return (
            <Fragment key={message.message_id}>
              {message.content !== null ? (
                <p className={styles.agentMessage} data-role={message.role}>
                  {message.content}
                </p>
              ) : null}
              {message.role === "assistant" &&
              message.status === "completed" &&
              auditFindings.length > 0 ? (
                <AgentAuditFindings
                  findings={auditFindings}
                  issueLabels={referenceLabels.issues}
                  objectLabels={referenceLabels.objects}
                  eventLabels={referenceLabels.events}
                  onLocateEvent={onLocateEvent}
                  onLocateIssue={onLocateIssue}
                  onLocateObject={onLocateObject}
                />
              ) : null}
              {message.role === "assistant" &&
              message.status === "completed" &&
              routingSummaryFor(message) !== null ? (
                <RoutingFeedback
                  intent={routingSummaryFor(message)?.intent ?? null}
                  onSubmitted={(correctIntent) =>
                    submitRoutingFeedback(message, correctIntent)
                  }
                  routeSource={routingSummaryFor(message)?.route_source ?? null}
                  submittedIntent={feedbackByMessage[message.message_id]}
                />
              ) : null}
              {message.role === "assistant" &&
              message.status === "completed" &&
              (message.referenced_object_ids.length > 0 ||
                message.referenced_event_ids.length > 0 ||
                message.referenced_validation_issue_ids.length > 0 ||
                message.suggested_view !== null) ? (
                <div className={styles.agentRefs} aria-label="回答引用">
                  {message.referenced_object_ids.map((objectId) => (
                    <button
                      data-ref-kind="object"
                      key={`object:${objectId}`}
                      onClick={() => onLocateObject(objectId)}
                      type="button"
                    >
                      对象 · {referenceLabels.objects[objectId] ?? objectId}
                    </button>
                  ))}
                  {message.referenced_event_ids.map((eventId) => (
                    <button
                      data-ref-kind="event"
                      key={`event:${eventId}`}
                      onClick={() => onLocateEvent(eventId)}
                      type="button"
                    >
                      事件 · {referenceLabels.events[eventId] ?? eventId}
                    </button>
                  ))}
                  {message.referenced_validation_issue_ids.map((issueId) => (
                    <button
                      data-ref-kind="issue"
                      key={`issue:${issueId}`}
                      onClick={() => onLocateIssue(issueId)}
                      type="button"
                    >
                      验证 · {referenceLabels.issues[issueId] ?? issueId}
                    </button>
                  ))}
                  {message.suggested_view !== null ? (
                    <button
                      data-ref-kind="view"
                      onClick={() =>
                        onLocateView(message.suggested_view ?? "timeline")
                      }
                      type="button"
                    >
                      视图 ·{" "}
                      {agentViewLabels[message.suggested_view ?? "timeline"]}
                    </button>
                  ) : null}
                </div>
              ) : null}
              {message.role === "assistant" &&
              message.status === "pending" ? (
                <p className={styles.agentThinking} role="status">
                  {busy
                    ? `Agent 正在回复 · ${stageLabel(liveTask)}${
                        contextByTask[message.task?.task_run_id ?? -1]
                          ? ` · 上下文 ${contextByTask[message.task?.task_run_id ?? -1]?.usedTokens ?? 0}${
                              contextByTask[message.task?.task_run_id ?? -1]?.budgetTokens !== null &&
                              contextByTask[message.task?.task_run_id ?? -1]?.budgetTokens !== undefined
                                ? `/${contextByTask[message.task?.task_run_id ?? -1]?.budgetTokens}`
                                : ""
                            } tokens`
                          : ""
                      }`
                    : "Agent 正在整理回复…"}
                </p>
              ) : null}
              {message.role === "assistant" &&
              message.status === "failed" ? (
                <div className={styles.agentFailure} role="status">
                  <strong>回复失败</strong>
                  <span>
                    {message.task?.failure?.message ?? "Agent 未能完成这次回复。"}
                  </span>
                  <button onClick={() => retryMessage(message)} type="button">
                    重试
                  </button>
                </div>
              ) : null}
              {message.role === "assistant" &&
              message.status === "completed" &&
              patchSet ? (
                <AgentPatchReview
                  busy={patchBusyId === patchSet.patch_set_id}
                  key={`${patchSet.patch_set_id}:${patchSet.status}`}
                  objectLabels={referenceLabels.objects}
                  onApply={(operationIds) =>
                    void applyPatchSet(patchSet, operationIds)
                  }
                  onLocateObject={onLocateObject}
                  onRetry={() => retryMessage(message)}
                  onUndo={() => void undoPatchSet(patchSet)}
                  patchSet={patchSet}
                />
              ) : null}
            </Fragment>
          );
        })}
        {!threadsLoading &&
        !threadsError &&
        !messagesLoading &&
        messagesError ? (
          <div className={styles.agentFailure} role="status">
            <strong>读取失败</strong>
            <span>{messagesError}</span>
            <button onClick={() => void reloadMessages()} type="button">
              重试
            </button>
          </div>
        ) : null}
        {!threadsLoading &&
        !threadsError &&
        !messagesLoading &&
        !messagesError &&
        messages.length === 0 ? (
          <p className={styles.agentEmpty}>
            {selectedThread === null
              ? "先创建一个 Agent 对话。"
              : "从上方预设指令或输入框开始布置卷宗任务。"}
          </p>
        ) : null}
      </div>
      <div className={styles.agentPrompts} aria-label="统筹指令">
        {agentPromptPresets.map((preset) => (
          <button
            disabled={promptsDisabled}
            key={preset.id}
            onClick={() => void send(preset.prompt, preset.routingHint)}
            type="button"
          >
            {preset.label}
          </button>
        ))}
      </div>
      <form
        className={styles.agentInput}
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <input
          aria-label="给卷宗统筹 Agent 的指令"
          disabled={inputDisabled}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={
            busy
              ? "Agent 正在回复，请稍候…"
              : "布置卷宗任务…"
          }
          value={draft}
        />
        <button
          disabled={inputDisabled || !draft.trim()}
          type="submit"
        >
          {busy ? "回复中" : "发送"}
        </button>
        {busy && pendingEntry !== null ? (
          <button
            className={styles.agentCancel}
            onClick={() => void cancelCurrentTask()}
            type="button"
          >
            取消
          </button>
        ) : null}
      </form>
    </section>
  );
}

function RoutingFeedback({
  routeSource,
  intent,
  submittedIntent,
  onSubmitted,
}: {
  routeSource: string | null;
  intent: string | null;
  submittedIntent?: AgentRoutingCorrectIntent;
  onSubmitted: (intent: AgentRoutingCorrectIntent) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [selection, setSelection] = useState<AgentRoutingCorrectIntent | "">("");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (selection === "" || submitting) return;
    setSubmitting(true);
    try {
      await onSubmitted(selection);
      setEditing(false);
    } catch {
      // The panel surfaces the API error near the message list.
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.agentRouteMeta} aria-label="意图路由">
      <span className={styles.agentRouteChip}>
        {routingSourceLabels[routeSource ?? ""] ?? routeSource ?? "路由"}
        {intent !== null
          ? ` · ${routingIntentLabels[intent as AgentRoutingCorrectIntent] ?? intent}`
          : ""}
      </span>
      {submittedIntent ? (
        <span className={styles.agentRouteSubmitted}>
          已记录反馈：{routingIntentLabels[submittedIntent]}
        </span>
      ) : editing ? (
        <span className={styles.agentRouteFeedback}>
          <select
            aria-label="正确的意图"
            onChange={(event) =>
              setSelection(event.target.value as AgentRoutingCorrectIntent)
            }
            value={selection}
          >
            <option value="" disabled>
              选择正确意图
            </option>
            {(
              Object.entries(routingIntentLabels) as Array<
                [AgentRoutingCorrectIntent, string]
              >
            ).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <button
            disabled={selection === "" || submitting}
            onClick={() => void submit()}
            type="button"
          >
            {submitting ? "提交中…" : "提交反馈"}
          </button>
          <button
            disabled={submitting}
            onClick={() => setEditing(false)}
            type="button"
          >
            取消
          </button>
        </span>
      ) : (
        <button
          className={styles.agentRouteFeedbackButton}
          onClick={() => setEditing(true)}
          type="button"
        >
          路由错误
        </button>
      )}
    </div>
  );
}

function AgentAuditFindings({
  findings,
  objectLabels,
  eventLabels,
  issueLabels,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
}: {
  findings: AgentAuditFindingView[];
  objectLabels: Record<string, string>;
  eventLabels: Record<string, string>;
  issueLabels: Record<string, string>;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onLocateIssue: (issueId: string) => void;
}) {
  return (
    <article className={styles.agentAuditCard} aria-label="逻辑漏洞复查发现">
      <header className={styles.agentAuditHeader}>
        <strong>逻辑漏洞复查发现</strong>
        <span>{findings.length} 项</span>
      </header>
      <ol className={styles.agentAuditList}>
        {findings.map((finding) => (
          <li
            className={styles.agentAuditFinding}
            data-kind={finding.kind}
            data-severity={finding.severity}
            key={finding.finding_id}
          >
            <div className={styles.agentAuditFindingMeta}>
              <b>{finding.finding_id}</b>
              <span>{auditFindingKindLabels[finding.kind] ?? finding.kind}</span>
              <span>
                {auditFindingSeverityLabels[finding.severity] ??
                  finding.severity}
              </span>
              {finding.needs_manual_review ? (
                <span data-manual="true">待人工确认</span>
              ) : null}
            </div>
            <strong>{finding.title}</strong>
            <p>{finding.statement}</p>
            {finding.evidence_object_ids.length > 0 ||
            finding.evidence_event_ids.length > 0 ||
            finding.evidence_validation_issue_ids.length > 0 ? (
              <div className={styles.agentRefs}>
                {finding.evidence_object_ids.map((objectId) => (
                  <button
                    data-ref-kind="object"
                    key={`object:${objectId}`}
                    onClick={() => onLocateObject(objectId)}
                    type="button"
                  >
                    对象 · {objectLabels[objectId] ?? objectId}
                  </button>
                ))}
                {finding.evidence_event_ids.map((eventId) => (
                  <button
                    data-ref-kind="event"
                    key={`event:${eventId}`}
                    onClick={() => onLocateEvent(eventId)}
                    type="button"
                  >
                    事件 · {eventLabels[eventId] ?? eventId}
                  </button>
                ))}
                {finding.evidence_validation_issue_ids.map((issueId) => (
                  <button
                    data-ref-kind="issue"
                    key={`issue:${issueId}`}
                    onClick={() => onLocateIssue(issueId)}
                    type="button"
                  >
                    验证 · {issueLabels[issueId] ?? issueId}
                  </button>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </article>
  );
}

function AgentPatchReview({
  patchSet,
  objectLabels,
  busy,
  onApply,
  onUndo,
  onRetry,
  onLocateObject,
}: {
  patchSet: AgentPatchSetView;
  objectLabels: Record<string, string>;
  busy: boolean;
  onApply: (operationIds: number[]) => void;
  onUndo: () => void;
  onRetry?: () => void;
  onLocateObject?: (objectId: string) => void;
}) {
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [confirmingReject, setConfirmingReject] = useState(false);
  const [issuesExpanded, setIssuesExpanded] = useState(false);
  const actionable = patchSet.status === "pending" && !patchSet.is_stale;
  const allOperationIds = patchSet.operations.map(
    (operation) => operation.operation_id,
  );

  function toggleOperation(operationId: number) {
    setSelectedIds((previous) =>
      previous.includes(operationId)
        ? previous.filter((id) => id !== operationId)
        : [...previous, operationId],
    );
  }

  function rejectAll() {
    if (confirmingReject) {
      setConfirmingReject(false);
      onApply([]);
      return;
    }
    setConfirmingReject(true);
  }

  return (
    <article
      className={styles.agentPatchCard}
      data-status={patchSet.status}
      data-stale={patchSet.is_stale || undefined}
    >
      <header className={styles.agentPatchHeader}>
        <strong>修改建议</strong>
        <span>
          {patchStatusLabels[patchSet.status]}
          {patchSet.is_stale ? " · 草稿已变化" : ""}
          {patchSet.status === "applied" &&
          patchSet.applied_to_revision !== null
            ? ` · R${patchSet.applied_from_revision}→R${patchSet.applied_to_revision}`
            : ""}
        </span>
      </header>
      <p className={styles.agentPatchReason}>
        基于草稿 R{patchSet.base_draft_revision} 生成
        {patchSet.reason_summary ? `：${patchSet.reason_summary}` : ""}
      </p>
      <div className={styles.agentPatchOps}>
        {patchSet.operations.map((operation) => {
          const decision = operation.decision ?? "pending";
          const checked =
            decision === "accepted" ||
            (actionable && selectedIds.includes(operation.operation_id));
          const label =
            objectLabels[operation.object_id ?? ""] ?? operation.object_id ?? "对象";
          return (
            <label className={styles.agentPatchOp} key={operation.operation_id}>
              <input
                aria-label={`选择修改 ${label} ${operation.field_path}`}
                checked={checked}
                disabled={!actionable || busy}
                onChange={() => toggleOperation(operation.operation_id)}
                type="checkbox"
              />
              <span>
                <strong>
                  {label}
                  <code>{operation.field_path}</code>
                </strong>
                <span className={styles.agentPatchOpMeta}>
                  {operation.object_type ? (
                    <span>{operation.object_type}</span>
                  ) : null}
                  {operation.expected_object_revision !== null ? (
                    <span>对象 R{operation.expected_object_revision}</span>
                  ) : null}
                  <span>{operation.operation_type}</span>
                  {operation.object_id !== null && onLocateObject ? (
                    <button
                      aria-label={`定位对象 ${label}`}
                      onClick={() => onLocateObject(operation.object_id ?? "")}
                      type="button"
                    >
                      在工作台定位
                    </button>
                  ) : null}
                </span>
                <small>
                  {displayValue(operation.old_value)} →{" "}
                  {displayValue(operation.new_value)}
                </small>
                <em>{operation.reason}</em>
              </span>
              <b data-decision={decision}>
                {operationDecisionLabels[decision]}
              </b>
            </label>
          );
        })}
      </div>
      {patchSet.validator_issues.length > 0 ? (
        <div className={styles.agentPatchIssues}>
          <button
            aria-expanded={issuesExpanded}
            onClick={() => setIssuesExpanded((expanded) => !expanded)}
            type="button"
          >
            {issuesExpanded ? "收起" : "查看"}验证警告（
            {patchSet.validator_issues.length}）
          </button>
          {issuesExpanded ? (
            <ul>
              {patchSet.validator_issues.map((issue, index) => {
                const title =
                  typeof issue.title === "string"
                    ? issue.title
                    : `验证警告 ${index + 1}`;
                const message =
                  typeof issue.message === "string" ? issue.message : null;
                const ruleId =
                  typeof issue.rule_id === "string" ? issue.rule_id : null;
                return (
                  <li key={`${ruleId ?? "issue"}:${index}`}>
                    <strong>{title}</strong>
                    {ruleId !== null ? <code>{ruleId}</code> : null}
                    {message !== null ? <span>{message}</span> : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}
      {patchSet.validation_warning ? (
        <p className={styles.agentPatchWarning}>
          应用后仍有 {patchSet.validator_issues.length} 条验证警告，工作台会同步刷新。
        </p>
      ) : null}
      <div className={styles.agentPatchActions}>
        {actionable ? (
          <>
            <button
              disabled={busy}
              onClick={() => onApply(allOperationIds)}
              type="button"
            >
              全部采纳
            </button>
            <button
              disabled={busy || selectedIds.length === 0}
              onClick={() => onApply(selectedIds)}
              type="button"
            >
              采纳所选（{selectedIds.length}）
            </button>
            {confirmingReject ? (
              <>
                <button
                  className={styles.agentPatchDanger}
                  disabled={busy}
                  onClick={rejectAll}
                  type="button"
                >
                  确认拒绝
                </button>
                <button
                  disabled={busy}
                  onClick={() => setConfirmingReject(false)}
                  type="button"
                >
                  取消
                </button>
              </>
            ) : (
              <button disabled={busy} onClick={rejectAll} type="button">
                全部拒绝
              </button>
            )}
          </>
        ) : null}
        {patchSet.status === "applied" ? (
          <button disabled={busy} onClick={onUndo} type="button">
            撤销应用
          </button>
        ) : null}
        {patchSet.is_stale && onRetry ? (
          <button disabled={busy} onClick={onRetry} type="button">
            重新生成建议
          </button>
        ) : null}
        {patchSet.is_stale ? (
          <span className={styles.agentPatchStale}>
            建议基于旧草稿生成，请重新请求。
          </span>
        ) : null}
      </div>
    </article>
  );
}
