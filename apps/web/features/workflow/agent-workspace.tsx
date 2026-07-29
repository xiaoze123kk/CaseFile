"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  errorMessage,
  streamTaskEvents,
  type ProviderName,
  type TaskEventView,
} from "@/lib/api-client";

import styles from "./real-workbench.module.css";
import {
  applyAgentPatchSet,
  createAgentThread,
  executionStageLabel,
  listAgentMessages,
  listAgentThreads,
  patchAgentThread,
  sendAgentMessage,
  terminalAgentEventTypes,
  threadIsArchived,
  threadIsFavorite,
  undoAgentPatchSet,
  type AgentMessageView,
  type AgentPatchOperationView,
  type AgentPatchSetView,
  type AgentReferenceView,
  type AgentThreadView,
  type ValidatorIssueView,
} from "./workbench-api";
import {
  objectHeadline,
  resolveObjectRef,
  type WorkbenchObject,
  type WorkbenchObjectRef,
  type WorkbenchSelection,
} from "./workbench-model";
import type { CaseFileDocument } from "@/lib/api-client";

const quickDirectives = [
  {
    label: "检查时间线",
    prompt: "检查整个卷宗的事实时间线，指出时间缺口、矛盾和因果断点。",
  },
  {
    label: "补全对象",
    prompt: "扫描整个卷宗，找出描述不足或关联缺失的对象，并提出可审阅的修改建议。",
  },
  {
    label: "审查谜底",
    prompt: "从完整卷宗出发，检查核心结论是否被事实和主张充分支撑。",
  },
] as const;

function threadStatusLabel(thread: AgentThreadView) {
  const labels: Record<AgentThreadView["status"], string> = {
    active: "可继续",
    archived: "已归档",
  };
  return labels[thread.status];
}

function formatMessageTime(value: string | null | undefined) {
  if (!value) return "";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    month: "numeric",
    day: "numeric",
    hour12: false,
  }).format(new Date(parsed));
}

function referenceLabel(
  document: CaseFileDocument,
  ref: WorkbenchObjectRef | null | undefined,
  fallback?: string,
) {
  return resolveObjectRef(document, ref)?.label ?? fallback ?? "关联对象";
}

export function agentReferenceTarget(
  document: CaseFileDocument,
  ref: AgentReferenceView,
) {
  const resolved = resolveObjectRef(document, ref);
  if (!resolved) return null;
  return {
    selection: {
      collection: resolved.collection,
      objectId: resolved.object.id,
    } satisfies WorkbenchSelection,
    preferTimeline: resolved.collection === "events",
  };
}

const recoverableTaskStatuses = new Set([
  "pending",
  "queued",
  "running",
]);

export function recoverActiveTaskRunId(messages: AgentMessageView[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const task = message.task;
    if (!task) continue;
    if (
      recoverableTaskStatuses.has(task.status ?? "") ||
      (!task.status && recoverableTaskStatuses.has(message.status))
    ) {
      return task.task_run_id;
    }
  }
  return null;
}

function formatSuggestedValue(
  document: CaseFileDocument,
  value: unknown,
): string {
  if (value === null || value === undefined || value === "") return "留空";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (
      Number.isFinite(parsed) &&
      /^\d{4}-\d{2}-\d{2}T/.test(value)
    ) {
      return new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(parsed));
    }
    return value;
  }
  if (Array.isArray(value)) {
    if (!value.length) return "无";
    return value
      .map((item) =>
        item &&
        typeof item === "object" &&
        ("object_id" in item || "object_type" in item)
          ? referenceLabel(document, item as WorkbenchObjectRef)
          : formatSuggestedValue(document, item),
      )
      .join("、");
  }
  if (
    typeof value === "object" &&
    ("object_id" in value || "object_type" in value)
  ) {
    return referenceLabel(document, value as WorkbenchObjectRef);
  }
  if (
    typeof value === "object" &&
    "start" in value
  ) {
    const time = value as {
      start?: unknown;
      end?: unknown;
      precision?: unknown;
    };
    const start = formatSuggestedValue(document, time.start);
    const end = time.end ? formatSuggestedValue(document, time.end) : "";
    return `${start}${end ? ` 至 ${end}` : ""}`;
  }
  return "结构化内容已更新";
}

function operationSelectionKey(operation: AgentPatchOperationView) {
  return String(operation.operation_id);
}

export function fieldPathLabel(operation: AgentPatchOperationView) {
  if (operation.field_label) return operation.field_label;
  const field = operation.field_path.split("/").filter(Boolean).at(-1);
  const labels: Record<string, string> = {
    description: "说明",
    name: "名称",
    statement: "陈述",
    title: "标题",
    truth_status: "事实状态",
    support_refs: "支撑信息",
    time: "发生时间",
  };
  return field ? labels[field] ?? "对象字段" : "对象内容";
}

function ConfirmApplyDialog({
  objectCount,
  fieldCount,
  busy,
  onCancel,
  onConfirm,
}: {
  objectCount: number;
  fieldCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className={styles.confirmBackdrop} role="presentation">
      <section
        aria-labelledby="confirm-agent-apply-title"
        aria-modal="true"
        className={styles.confirmDialog}
        role="dialog"
      >
        <header>
          <span>应用修改</span>
          <h3 id="confirm-agent-apply-title">确认写入本批建议？</h3>
        </header>
        <div className={styles.confirmImpact}>
          <article>
            <strong>{objectCount}</strong>
            <span>个对象</span>
          </article>
          <article>
            <strong>{fieldCount}</strong>
            <span>处字段</span>
          </article>
        </div>
        <p>确认后会立即更新当前卷宗。完成后仍可撤销整批修改。</p>
        <footer>
          <button disabled={busy} onClick={onCancel} type="button">
            返回审阅
          </button>
          <button
            className={styles.confirmPrimary}
            disabled={busy}
            onClick={onConfirm}
            type="button"
          >
            {busy ? "正在应用…" : "确认应用"}
          </button>
        </footer>
      </section>
    </div>
  );
}

export function AgentPatchSuggestionCard({
  actorId,
  currentRevision,
  document,
  patchSet,
  projectId,
  onDraftChanged,
  onRequestRepair,
  onRegenerate,
}: {
  actorId: number;
  currentRevision: number;
  document: CaseFileDocument;
  patchSet: AgentPatchSetView;
  projectId: number;
  onDraftChanged: () => void;
  onRequestRepair: (content: string) => void;
  onRegenerate: () => void;
}) {
  const initialSelected = useMemo(
    () =>
      new Set(
        patchSet.operations
          .filter((operation) => operation.decision !== "rejected")
          .map(operationSelectionKey),
      ),
    [patchSet],
  );
  const [selected, setSelected] = useState(initialSelected);
  const [status, setStatus] = useState(patchSet.status);
  const [issues, setIssues] = useState<ValidatorIssueView[]>(
    patchSet.validator_issues,
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [appliedRevision, setAppliedRevision] = useState<number | null>(
    patchSet.applied_to_revision,
  );
  const stale =
    patchSet.is_stale ||
    status === "stale" ||
    (status === "pending" &&
      patchSet.base_draft_revision !== currentRevision);
  const selectedOperations = patchSet.operations.filter((operation) =>
    selected.has(operationSelectionKey(operation)),
  );
  const allSelected =
    selectedOperations.length === patchSet.operations.length &&
    patchSet.operations.length > 0;
  const objectCount = new Set(
    selectedOperations.map(
      (operation) =>
        operation.object_ref.object_id ?? operation.operation_id,
    ),
  ).size;
  const canUndo =
    appliedRevision !== null && currentRevision === appliedRevision;

  function toggleOperation(operationId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(operationId)) next.delete(operationId);
      else next.add(operationId);
      return next;
    });
  }

  async function applySelection(operationIds?: number[]) {
    setBusy(true);
    setActionError(null);
    try {
      const selectedIds =
        operationIds ??
        (allSelected
          ? null
          : selectedOperations.map((operation) => operation.operation_id));
      const response = await applyAgentPatchSet(
        projectId,
        actorId,
        patchSet.patch_set_id,
        selectedIds,
        currentRevision,
      );
      setStatus(
        response.validator_issues.length
          ? "validation_warning"
          : response.status,
      );
      setIssues(response.validator_issues);
      setAppliedRevision(
        response.applied_to_revision ?? response.draft_revision,
      );
      setConfirmOpen(false);
      onDraftChanged();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function undoSelection() {
    setBusy(true);
    setActionError(null);
    try {
      const response = await undoAgentPatchSet(
        projectId,
        actorId,
        patchSet.patch_set_id,
        currentRevision,
      );
      setStatus(response.status);
      setIssues(response.validator_issues);
      onDraftChanged();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className={`${styles.patchCard} ${
        stale ? styles.stalePatchCard : ""
      }`}
    >
      <header>
        <span>修改建议</span>
        <strong>{patchSet.reason_summary}</strong>
        <small>
          {stale
            ? "卷宗内容已变化，本批建议需要重新生成"
            : `${patchSet.operations.length} 处可审阅修改`}
        </small>
      </header>

      {stale ? (
        <div className={styles.stalePatchNotice}>
          <strong>建议已经过期</strong>
          <p>你在 Agent 运行期间继续编辑了卷宗。旧建议不会覆盖新内容。</p>
          <button onClick={onRegenerate} type="button">
            基于最新卷宗重新生成
          </button>
        </div>
      ) : (
        <>
          <div className={styles.patchSelectionBar}>
            <span>
              已选 <strong>{selectedOperations.length}</strong> /{" "}
              {patchSet.operations.length}
            </span>
            <button
              onClick={() =>
                setSelected(allSelected ? new Set() : initialSelected)
              }
              type="button"
            >
              {allSelected ? "取消全选" : "选择全部"}
            </button>
          </div>
          <div className={styles.patchOperations}>
            {patchSet.operations.map((operation) => (
              <PatchOperation
                checked={selected.has(operationSelectionKey(operation))}
                document={document}
                key={operationSelectionKey(operation)}
                onToggle={() =>
                  toggleOperation(operationSelectionKey(operation))
                }
                operation={operation}
              />
            ))}
          </div>
        </>
      )}

      {issues.length ? (
        <section className={styles.validatorIssues} role="alert">
          <header>
            <span aria-hidden="true">!</span>
            <div>
              <strong>修改已保留，但仍有问题需要处理</strong>
              <small>可以撤销本批修改，或让 Agent 继续修复。</small>
            </div>
          </header>
          {issues.map((issue, index) => (
            <article key={issue.issue_id ?? `${issue.title}-${index}`}>
              <strong>{issue.title}</strong>
              {issue.explanation ? <p>{issue.explanation}</p> : null}
              {issue.fix_hint ? <small>{issue.fix_hint}</small> : null}
            </article>
          ))}
          <button
            onClick={() =>
              onRequestRepair(
                `请修复刚才应用的建议仍然留下的问题：${issues
                  .map((issue) => issue.title)
                  .join("、")}。`,
              )
            }
            type="button"
          >
            让 Agent 修复
          </button>
        </section>
      ) : null}

      {actionError ? (
        <p className={styles.patchError} role="alert">
          {actionError}
        </p>
      ) : null}

      {!stale ? (
        <footer className={styles.patchActions}>
          {status === "applied" || status === "validation_warning" ? (
            <>
              <span>本批修改已写入卷宗</span>
              {canUndo ? (
                <button disabled={busy} onClick={undoSelection} type="button">
                  {busy ? "处理中…" : "撤销本批修改"}
                </button>
              ) : (
                <small>
                  {appliedRevision !== null &&
                  currentRevision < appliedRevision
                    ? "正在同步最新卷宗…"
                    : "卷宗已有后续修改，不能直接撤销本批"}
                </small>
              )}
            </>
          ) : status === "undone" ? (
            <span>本批修改已撤销</span>
          ) : status === "rejected" ? (
            <span>本批建议已全部标记为不采用</span>
          ) : (
            <>
              <button
                disabled={busy}
                onClick={() => {
                  setSelected(new Set());
                  void applySelection([]);
                }}
                type="button"
              >
                {busy ? "处理中…" : "全部不采用"}
              </button>
              <button
                className={styles.patchApply}
                disabled={!selectedOperations.length}
                onClick={() => setConfirmOpen(true)}
                type="button"
              >
                {allSelected ? "接受并应用全部" : "应用所选修改"}
              </button>
            </>
          )}
        </footer>
      ) : null}

      {confirmOpen ? (
        <ConfirmApplyDialog
          busy={busy}
          fieldCount={selectedOperations.length}
          objectCount={objectCount}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void applySelection()}
        />
      ) : null}
    </section>
  );
}

function PatchOperation({
  checked,
  document,
  operation,
  onToggle,
}: {
  checked: boolean;
  document: CaseFileDocument;
  operation: AgentPatchOperationView;
  onToggle: () => void;
}) {
  return (
    <label className={styles.patchOperation}>
      <input checked={checked} onChange={onToggle} type="checkbox" />
      <span className={styles.patchOperationCheck} aria-hidden="true">
        {checked ? "✓" : ""}
      </span>
      <span className={styles.patchOperationBody}>
        <strong>
          {referenceLabel(document, operation.object_ref)}
          <small>{fieldPathLabel(operation)}</small>
        </strong>
        <span className={styles.patchDiff}>
          <del>{formatSuggestedValue(document, operation.old_value)}</del>
          <i aria-hidden="true">→</i>
          <ins>{formatSuggestedValue(document, operation.new_value)}</ins>
        </span>
      </span>
    </label>
  );
}

function ThreadRail({
  threads,
  selectedThreadId,
  query,
  onQueryChange,
  onSelect,
  onCreate,
  creating,
}: {
  threads: AgentThreadView[];
  selectedThreadId: number | null;
  query: string;
  onQueryChange: (value: string) => void;
  onSelect: (threadId: number) => void;
  onCreate: () => void;
  creating: boolean;
}) {
  const visibleThreads = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("zh-CN");
    return threads
      .filter((thread) => !threadIsArchived(thread))
      .filter(
        (thread) =>
          !normalized ||
          thread.title.toLocaleLowerCase("zh-CN").includes(normalized),
      )
      .sort((left, right) => {
        const favoriteRank =
          Number(threadIsFavorite(right)) - Number(threadIsFavorite(left));
        if (favoriteRank) return favoriteRank;
        return String(
          right.last_message_at ?? right.updated_at ?? "",
        ).localeCompare(
          String(left.last_message_at ?? left.updated_at ?? ""),
        );
      });
  }, [query, threads]);

  return (
    <aside className={styles.threadRail} aria-label="Agent 对话线程">
      <header>
        <span>协作线程</span>
        <button disabled={creating} onClick={onCreate} type="button">
          {creating ? "创建中…" : "新对话"}
        </button>
      </header>
      <label className={styles.threadSearch}>
        <span aria-hidden="true">⌕</span>
        <input
          aria-label="搜索 Agent 线程"
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索线程…"
          type="search"
          value={query}
        />
      </label>
      <div className={styles.threadList}>
        {visibleThreads.map((thread) => (
          <button
            aria-current={
              selectedThreadId === thread.thread_id ? "true" : undefined
            }
            className={
              selectedThreadId === thread.thread_id
                ? styles.activeThread
                : undefined
            }
            key={thread.thread_id}
            onClick={() => onSelect(thread.thread_id)}
            type="button"
          >
            <span className={styles.threadMark} aria-hidden="true">
              {threadIsFavorite(thread) ? "◆" : "◇"}
            </span>
            <span>
              <strong>{thread.title || "新对话"}</strong>
              <small>
                {formatMessageTime(
                  thread.last_message_at ?? thread.updated_at,
                ) || threadStatusLabel(thread)}
              </small>
            </span>
            <i>{threadStatusLabel(thread)}</i>
          </button>
        ))}
        {!visibleThreads.length ? (
          <p>还没有匹配的协作线程。</p>
        ) : null}
      </div>
    </aside>
  );
}

function MessageRecord({
  document,
  message,
  patchProps,
  onOpenReference,
}: {
  document: CaseFileDocument;
  message: AgentMessageView;
  patchProps: Omit<
    Parameters<typeof AgentPatchSuggestionCard>[0],
    "patchSet"
  >;
  onOpenReference: (ref: AgentReferenceView) => void;
}) {
  const roleLabel =
    message.role === "user"
      ? "你的指令"
      : message.role === "assistant"
        ? "Agent 记录"
        : "运行记录";
  return (
    <article
      className={`${styles.messageRecord} ${
        message.role === "user"
          ? styles.userMessage
          : message.role === "system"
            ? styles.systemMessage
            : styles.agentMessage
      }`}
    >
      <header>
        <span>{roleLabel}</span>
        <time>{formatMessageTime(message.created_at)}</time>
      </header>
      <div className={styles.messageContent}>{message.content}</div>
      {message.references?.length ? (
        <div className={styles.messageReferences}>
          <span>引用对象</span>
          {message.references.map((ref, index) => (
            <button
              key={`${ref.object_type ?? "object"}-${ref.object_id ?? index}`}
              onClick={() => onOpenReference(ref)}
              type="button"
            >
              {referenceLabel(document, ref)}
              <span aria-hidden="true">↗</span>
            </button>
          ))}
        </div>
      ) : null}
      {message.patch_set ? (
        <AgentPatchSuggestionCard
          {...patchProps}
          key={`${message.patch_set.patch_set_id}:${message.patch_set.status}`}
          patchSet={message.patch_set}
        />
      ) : null}
    </article>
  );
}

export function AgentWorkspace({
  actorId,
  currentRevision,
  document,
  focusEvent,
  onClearFocus,
  onDraftChanged,
  onOpenSelection,
  projectId,
  provider,
}: {
  actorId: number;
  currentRevision: number;
  document: CaseFileDocument;
  focusEvent: WorkbenchObject | null;
  onClearFocus: () => void;
  onDraftChanged: () => void;
  onOpenSelection: (
    selection: WorkbenchSelection,
    preferTimeline?: boolean,
  ) => void;
  projectId: number;
  provider: ProviderName;
}) {
  const queryClient = useQueryClient();
  const [railOpen, setRailOpen] = useState(false);
  const [threadQuery, setThreadQuery] = useState("");
  const [selectedThreadId, setSelectedThreadId] = useState<number | null>(null);
  const [composer, setComposer] = useState("");
  const [activeTasksByThread, setActiveTasksByThread] = useState<
    Record<number, number>
  >({});
  const [executionEventsByThread, setExecutionEventsByThread] = useState<
    Record<number, TaskEventView[]>
  >({});
  const [streamErrorsByThread, setStreamErrorsByThread] = useState<
    Record<number, string>
  >({});
  const [renaming, setRenaming] = useState(false);
  const [threadTitle, setThreadTitle] = useState("");
  const messageEndRef = useRef<HTMLDivElement | null>(null);

  const threadListQuery = useQuery({
    queryKey: ["agent-threads", actorId, projectId],
    queryFn: () => listAgentThreads(projectId, actorId),
  });
  const threads = useMemo(
    () => threadListQuery.data ?? [],
    [threadListQuery.data],
  );
  const effectiveThreadId =
    selectedThreadId ??
    (
      threads.find((thread) => !threadIsArchived(thread)) ??
      threads[0]
    )?.thread_id ??
    null;
  const selectedThread =
    threads.find((thread) => thread.thread_id === effectiveThreadId) ?? null;
  const messageListQuery = useQuery({
    queryKey: ["agent-messages", actorId, projectId, effectiveThreadId],
    queryFn: () =>
      listAgentMessages(projectId, actorId, effectiveThreadId as number),
    enabled: effectiveThreadId !== null,
  });
  const messages = useMemo(
    () => messageListQuery.data ?? [],
    [messageListQuery.data],
  );
  const recoveredTaskRunId = useMemo(
    () => recoverActiveTaskRunId(messages),
    [messages],
  );
  const activeTaskRunId =
    effectiveThreadId === null
      ? null
      : activeTasksByThread[effectiveThreadId] ?? recoveredTaskRunId;
  const executionEvents =
    effectiveThreadId === null
      ? []
      : executionEventsByThread[effectiveThreadId] ?? [];
  const streamError =
    effectiveThreadId === null
      ? null
      : streamErrorsByThread[effectiveThreadId] ?? null;

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, activeTaskRunId]);

  useEffect(() => {
    if (activeTaskRunId === null || effectiveThreadId === null) return;
    const streamThreadId = effectiveThreadId;
    const streamTaskRunId = activeTaskRunId;
    const controller = new AbortController();
    let stopped = false;
    void streamTaskEvents(
      `/projects/${projectId}/tasks/${streamTaskRunId}/stream`,
      actorId,
      (event) => {
        if (stopped) return;
        setExecutionEventsByThread((current) => {
          const threadEvents = current[streamThreadId] ?? [];
          if (
            threadEvents.some((item) => item.event_id === event.event_id)
          ) {
            return current;
          }
          return {
            ...current,
            [streamThreadId]: [...threadEvents, event].sort(
              (left, right) => left.sequence_no - right.sequence_no,
            ),
          };
        });
        if (terminalAgentEventTypes.has(event.event_type)) {
          setActiveTasksByThread((current) => {
            if (current[streamThreadId] !== streamTaskRunId) {
              return current;
            }
            const next = { ...current };
            delete next[streamThreadId];
            return next;
          });
          void queryClient.invalidateQueries({
            queryKey: [
              "agent-messages",
              actorId,
              projectId,
              streamThreadId,
            ],
          });
          void queryClient.invalidateQueries({
            queryKey: ["agent-threads", actorId, projectId],
          });
          onDraftChanged();
          controller.abort();
        }
      },
      controller.signal,
    ).catch((error) => {
      if (
        error instanceof DOMException &&
        error.name === "AbortError"
      ) {
        return;
      }
      setStreamErrorsByThread((current) => ({
        ...current,
        [streamThreadId]: errorMessage(error),
      }));
    });
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [
    activeTaskRunId,
    actorId,
    onDraftChanged,
    projectId,
    queryClient,
    effectiveThreadId,
  ]);

  const createThreadMutation = useMutation({
    mutationFn: () => createAgentThread(projectId, actorId),
    onSuccess: async (thread) => {
      await queryClient.invalidateQueries({
        queryKey: ["agent-threads", actorId, projectId],
      });
      setSelectedThreadId(thread.thread_id);
      setRailOpen(false);
    },
  });

  const threadPatchMutation = useMutation({
    mutationFn: ({
      threadId,
      changes,
    }: {
      threadId: number;
      changes: {
        title?: string;
        is_pinned?: boolean;
        archived?: boolean;
      };
    }) => patchAgentThread(projectId, actorId, threadId, changes),
    onSuccess: async (thread) => {
      await queryClient.invalidateQueries({
        queryKey: ["agent-threads", actorId, projectId],
      });
      if (threadIsArchived(thread)) setSelectedThreadId(null);
    },
  });

  const sendMutation = useMutation({
    mutationFn: async ({
      content,
      requestedThreadId,
    }: {
      content: string;
      requestedThreadId: number | null;
    }) => {
      const thread =
        requestedThreadId === null
          ? await createAgentThread(projectId, actorId)
          : null;
      const threadId = thread?.thread_id ?? requestedThreadId;
      if (threadId === null) throw new Error("无法建立新的 Agent 对话。");
      const response = await sendAgentMessage(
        projectId,
        actorId,
        threadId,
        content,
        provider,
      );
      return { threadId, response };
    },
    onSuccess: async ({ threadId, response }) => {
      const taskRunId = response.task.task_run_id;
      setSelectedThreadId(threadId);
      setComposer("");
      setExecutionEventsByThread((current) => ({
        ...current,
        [threadId]: [],
      }));
      setStreamErrorsByThread((current) => {
        if (!(threadId in current)) return current;
        const next = { ...current };
        delete next[threadId];
        return next;
      });
      setActiveTasksByThread((current) => ({
        ...current,
        [threadId]: taskRunId,
      }));
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["agent-threads", actorId, projectId],
        }),
        queryClient.invalidateQueries({
          queryKey: ["agent-messages", actorId, projectId, threadId],
        }),
      ]);
    },
  });

  function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    const value = composer.trim();
    if (!value || sendMutation.isPending) return;
    const content = focusEvent
      ? `${value}\n\n请重点讨论事件「${objectHeadline(focusEvent)}」，但仍以完整卷宗为上下文。`
      : value;
    sendMutation.mutate({
      content,
      requestedThreadId: effectiveThreadId,
    });
  }

  function requestAgent(content: string) {
    setComposer(content);
    window.requestAnimationFrame(() => {
      const textarea = documentGlobal.getElementById(
        "agent-workbench-composer",
      ) as HTMLTextAreaElement | null;
      textarea?.focus();
    });
  }

  function openReference(ref: AgentReferenceView) {
    const target = agentReferenceTarget(document, ref);
    if (!target) return;
    onOpenSelection(target.selection, target.preferTimeline);
  }

  const documentGlobal =
    typeof window === "undefined" ? ({} as Document) : window.document;

  const patchProps = {
    actorId,
    currentRevision,
    document,
    projectId,
    onDraftChanged,
    onRequestRepair: requestAgent,
    onRegenerate: () =>
      requestAgent("请基于最新卷宗重新生成上一批修改建议。"),
  };

  return (
    <div
      className={`${styles.agentWorkspace} ${
        railOpen ? styles.agentWorkspaceWithRail : ""
      }`}
    >
      {railOpen ? (
        <ThreadRail
          creating={createThreadMutation.isPending}
          onCreate={() => createThreadMutation.mutate()}
          onQueryChange={setThreadQuery}
          onSelect={(threadId) => {
            setSelectedThreadId(threadId);
            setRenaming(false);
            setRailOpen(false);
          }}
          query={threadQuery}
          selectedThreadId={effectiveThreadId}
          threads={threads}
        />
      ) : null}
      <section className={styles.conversationDesk}>
        <header className={styles.conversationHeader}>
          <button
            aria-expanded={railOpen}
            className={styles.threadRailToggle}
            onClick={() => setRailOpen((value) => !value)}
            type="button"
          >
            <span aria-hidden="true">☷</span>
            线程
            {threads.length ? <small>{threads.length}</small> : null}
          </button>
          <div className={styles.threadTitle}>
            {renaming && selectedThread ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  const title = threadTitle.trim();
                  if (!title) return;
                  threadPatchMutation.mutate({
                    threadId: selectedThread.thread_id,
                    changes: { title },
                  });
                  setRenaming(false);
                }}
              >
                <input
                  aria-label="线程名称"
                  autoFocus
                  onChange={(event) => setThreadTitle(event.target.value)}
                  value={threadTitle}
                />
                <button type="submit">保存</button>
              </form>
            ) : (
              <>
                <span>完整卷宗上下文</span>
                <strong>{selectedThread?.title || "新的协作记录"}</strong>
              </>
            )}
          </div>
          {selectedThread ? (
            <div className={styles.threadHeaderActions}>
              <button
                aria-label="重命名当前线程"
                onClick={() => {
                  setThreadTitle(selectedThread.title);
                  setRenaming(true);
                }}
                type="button"
              >
                改名
              </button>
              <button
                aria-pressed={threadIsFavorite(selectedThread)}
                onClick={() =>
                  threadPatchMutation.mutate({
                    threadId: selectedThread.thread_id,
                    changes: {
                      is_pinned: !threadIsFavorite(selectedThread),
                    },
                  })
                }
                type="button"
              >
                {threadIsFavorite(selectedThread) ? "已收藏" : "收藏"}
              </button>
              <button
                onClick={() =>
                  threadPatchMutation.mutate({
                    threadId: selectedThread.thread_id,
                    changes: { archived: true },
                  })
                }
                type="button"
              >
                归档
              </button>
            </div>
          ) : null}
        </header>

        <div className={styles.contextLedger}>
          <span aria-hidden="true">◎</span>
          <div>
            <strong>每次发送前读取最新 CaseFile</strong>
            <small>选择对象只改变当前关注点，不会缩小 Agent 的读取范围。</small>
          </div>
        </div>

        <div className={styles.conversationScroll}>
          {threadListQuery.isError ? (
            <div className={styles.agentUnavailable} role="alert">
              <strong>Agent 协作服务暂时无法读取</strong>
              <p>{errorMessage(threadListQuery.error)}</p>
              <button onClick={() => threadListQuery.refetch()} type="button">
                重新连接
              </button>
            </div>
          ) : messageListQuery.isLoading ? (
            <div className={styles.agentLoading} aria-busy="true">
              正在展开协作记录…
            </div>
          ) : messages.length ? (
            messages.map((message) => (
              <MessageRecord
                document={document}
                key={message.message_id}
                message={message}
                onOpenReference={openReference}
                patchProps={patchProps}
              />
            ))
          ) : (
            <section className={styles.agentWelcome}>
              <span className={styles.agentMonogram} aria-hidden="true">
                A
              </span>
              <div>
                <small>CASEFILE / EDITORIAL AGENT</small>
                <h2>从整卷事实开始协作</h2>
                <p>
                  直接说明你想检查、改写或补全什么。Agent 会读取完整卷宗，
                  修改则整理成可逐项或整批审阅的建议单。
                </p>
              </div>
              <div className={styles.quickDirectives}>
                {quickDirectives.map((directive) => (
                  <button
                    key={directive.label}
                    onClick={() => setComposer(directive.prompt)}
                    type="button"
                  >
                    <span>{directive.label}</span>
                    <small>写入指令 ↘</small>
                  </button>
                ))}
              </div>
            </section>
          )}

          {activeTaskRunId !== null ? (
            <details className={styles.executionRecord} open>
              <summary>
                <span className={styles.executionPulse} aria-hidden="true" />
                Agent 正在处理
                <small>查看执行记录</small>
              </summary>
              <div>
                {executionEvents.length ? (
                  executionEvents.map((event) => (
                    <p key={event.event_id}>
                      <span aria-hidden="true">✓</span>
                      {executionStageLabel(event)}
                    </p>
                  ))
                ) : (
                  <p>
                    <span aria-hidden="true">·</span>
                    正在读取完整卷宗
                  </p>
                )}
              </div>
            </details>
          ) : null}
          {streamError ? (
            <p className={styles.agentRunError} role="alert">
              {streamError}
            </p>
          ) : null}
          <div ref={messageEndRef} />
        </div>

        <form className={styles.agentComposer} onSubmit={submitMessage}>
          {focusEvent ? (
            <div className={styles.focusReference}>
              <span>当前关注</span>
              <strong>{objectHeadline(focusEvent)}</strong>
              <button
                aria-label="移除当前关注事件"
                onClick={onClearFocus}
                type="button"
              >
                ×
              </button>
            </div>
          ) : null}
          <label htmlFor="agent-workbench-composer">
            给 Agent 一条指令
          </label>
          <div>
            <textarea
              id="agent-workbench-composer"
              onChange={(event) => setComposer(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  submitMessage();
                }
              }}
              placeholder="例如：检查时间线里还有哪些无法解释的空白…"
              rows={3}
              value={composer}
            />
            <button
              disabled={!composer.trim() || sendMutation.isPending}
              type="submit"
            >
              {sendMutation.isPending ? "发送中…" : "发送"}
            </button>
          </div>
          {sendMutation.isError ? (
            <p role="alert">{errorMessage(sendMutation.error)}</p>
          ) : (
            <small>Enter 发送 · Shift + Enter 换行 · 修改始终需要人工决定</small>
          )}
        </form>
      </section>
    </div>
  );
}
