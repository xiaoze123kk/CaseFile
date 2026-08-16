"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  applyAgentPatchSet,
  createAgentThread,
  errorMessage,
  listAgentMessages,
  listAgentThreads,
  sendAgentMessage,
  undoAgentPatchSet,
  type AgentChatFocus,
  type AgentChatRoutingHint,
  type AgentMessageView,
  type AgentPatchSetView,
  type AgentSuggestedView,
  type AgentThreadView,
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
          }, controller.signal);
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
          "openai",
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
          return (
            <Fragment key={message.message_id}>
              {message.content !== null ? (
                <p className={styles.agentMessage} data-role={message.role}>
                  {message.content}
                </p>
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
                    ? `Agent 正在回复 · ${stageLabel(liveTask)}`
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
                  objectLabels={referenceLabels.objects}
                  onApply={(operationIds) =>
                    void applyPatchSet(patchSet, operationIds)
                  }
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

function AgentPatchReview({
  patchSet,
  objectLabels,
  busy,
  onApply,
  onUndo,
}: {
  patchSet: AgentPatchSetView;
  objectLabels: Record<string, string>;
  busy: boolean;
  onApply: (operationIds: number[]) => void;
  onUndo: () => void;
}) {
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
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
      <p className={styles.agentPatchReason}>{patchSet.reason_summary}</p>
      <div className={styles.agentPatchOps}>
        {patchSet.operations.map((operation) => {
          const decision = operation.decision ?? "pending";
          const checked =
            decision === "accepted" ||
            (actionable && selectedIds.includes(operation.operation_id));
          return (
            <label className={styles.agentPatchOp} key={operation.operation_id}>
              <input
                aria-label={`选择修改 ${objectLabels[operation.object_id ?? ""] ?? operation.object_id} ${operation.field_path}`}
                checked={checked}
                disabled={!actionable || busy}
                onChange={() => toggleOperation(operation.operation_id)}
                type="checkbox"
              />
              <span>
                <strong>
                  {objectLabels[operation.object_id ?? ""] ?? operation.object_id}
                  <code>{operation.field_path}</code>
                </strong>
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
            <button
              disabled={busy}
              onClick={() => onApply([])}
              type="button"
            >
              全部拒绝
            </button>
          </>
        ) : null}
        {patchSet.status === "applied" ? (
          <button disabled={busy} onClick={onUndo} type="button">
            撤销应用
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
