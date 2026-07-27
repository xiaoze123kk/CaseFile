"use client";

import { useMemo, useState } from "react";

import { StatusBadge } from "@/components/prototype-ui";
import {
  agentThreadMatchesQuery,
  agentThreadNeedsAttention,
  type AgentThread,
  type AgentThreadStatus,
  type AgentThreadTaskType,
  isTerminalAgentThread,
  type PrototypeAgentState,
  sortAgentThreads,
} from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import styles from "./agent-collaboration-drawer.module.css";

interface AgentCollaborationDrawerProps {
  open: boolean;
  pinned: boolean;
  requestedThreadId?: string | null;
  onClose: () => void;
  onTogglePin: () => void;
}

interface AgentTaskOption {
  code: string;
  label: string;
  description: string;
  instruction: string;
  mutationTask: boolean;
  taskType: AgentThreadTaskType;
}

const taskOptions: AgentTaskOption[] = [
  {
    code: "AUDIT",
    label: "全局审查",
    description: "检查谜底闭环、信息公平性与发布风险。",
    instruction: "审查整个 Draft 的因果闭环、信息公平性和发布风险。",
    mutationTask: false,
    taskType: "audit",
  },
  {
    code: "GAPS",
    label: "补全缺口",
    description: "定位断裂引用，并生成可审阅变更集。",
    instruction: "扫描整个 Draft，补全知识状态、时间锚点和线索回收缺口。",
    mutationTask: true,
    taskType: "gaps",
  },
  {
    code: "FLOW",
    label: "连贯性检查",
    description: "只读检查事件顺序、阶段与角色知识状态。",
    instruction: "只读检查整个 Draft 的时间线、叙事阶段和角色知识状态。",
    mutationTask: false,
    taskType: "flow",
  },
  {
    code: "PATCH",
    label: "生成变更集",
    description: "围绕开放问题组织跨对象修改建议。",
    instruction: "根据当前开放问题生成跨对象结构化变更集。",
    mutationTask: true,
    taskType: "patch",
  },
];

const statusLabels: Record<PrototypeAgentState["status"], string> = {
  idle: "READY",
  preview: "PREVIEW",
  running: "RUNNING",
  review: "REVIEW",
  stale: "STALE",
  validating: "VALIDATING",
  completed: "COMPLETE",
};

const threadStatusLabels: Record<AgentThreadStatus, string> = {
  running: "运行中",
  review: "待审阅",
  completed: "已完成",
  cancelled: "已取消",
  failed: "运行失败",
};

const threadTaskLabels: Record<AgentThreadTaskType, string> = {
  audit: "全局审查",
  gaps: "补全缺口",
  flow: "连贯性检查",
  patch: "生成变更集",
  custom: "自由指令",
};

export function AgentCollaborationDrawer({
  open,
  pinned,
  requestedThreadId,
  onClose,
  onTogglePin,
}: AgentCollaborationDrawerProps) {
  const { state, dispatch } = usePrototype();
  const [view, setView] = useState<"collaboration" | "threads">(
    requestedThreadId ? "threads" : "collaboration",
  );
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(
    requestedThreadId ?? null,
  );
  const [instruction, setInstruction] = useState("");
  const [customMutationTask, setCustomMutationTask] = useState(true);
  const agent = state.agent;
  const attentionCount = agent.history.filter(agentThreadNeedsAttention).length;

  const selectedChangeCount = useMemo(
    () => agent.changes.filter((change) => change.selected).length,
    [agent.changes],
  );

  if (!open) return null;

  function prepareTask(option: AgentTaskOption) {
    dispatch({
      type: "prepare-agent-task",
      label: option.label,
      instruction: option.instruction,
      mutationTask: option.mutationTask,
      taskType: option.taskType,
    });
  }

  function prepareCustomTask() {
    const value = instruction.trim();
    if (!value) return;
    dispatch({
      type: "prepare-agent-task",
      label: "自定义协作任务",
      instruction: value,
      mutationTask: customMutationTask,
      taskType: "custom",
    });
  }

  return (
    <>
      {!pinned ? (
        <button
          aria-label="关闭 Agent 协作抽屉"
          className={styles.backdrop}
          onClick={onClose}
          type="button"
        />
      ) : null}

      <aside
        aria-label="CaseFile Agent 协作"
        className={`${styles.drawer} ${pinned ? styles.pinned : ""}`}
      >
        <header className={styles.drawerHeader}>
          <div className={styles.agentIdentity}>
            <span aria-hidden="true">A</span>
            <div>
              <small>CASEFILE / COLLABORATOR</small>
              <strong>Agent 协作</strong>
            </div>
          </div>
          <StatusBadge
            tone={
              agent.status === "running" || agent.status === "validating"
                ? "red"
                : agent.status === "review" || agent.status === "stale"
                  ? "warning"
                  : "dark"
            }
          >
            {statusLabels[agent.status]}
          </StatusBadge>
          <button
            aria-pressed={pinned}
            className={styles.iconButton}
            onClick={onTogglePin}
            title={pinned ? "取消固定" : "固定抽屉"}
            type="button"
          >
            {pinned ? "◆" : "◇"}
          </button>
          <button
            aria-label="关闭 Agent 协作抽屉"
            className={styles.iconButton}
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </header>

        <nav className={styles.drawerTabs} aria-label="Agent 抽屉视图">
          <button
            aria-current={view === "collaboration" ? "page" : undefined}
            className={view === "collaboration" ? styles.activeDrawerTab : ""}
            onClick={() => setView("collaboration")}
            type="button"
          >
            <span>01</span>
            协作
          </button>
          <button
            aria-current={view === "threads" ? "page" : undefined}
            className={view === "threads" ? styles.activeDrawerTab : ""}
            onClick={() => setView("threads")}
            type="button"
          >
            <span>02</span>
            线程
            {attentionCount ? <b>{attentionCount}</b> : null}
          </button>
        </nav>

        <section className={styles.snapshotBar}>
          <div>
            <span>GLOBAL SCOPE</span>
            <strong>整个 Draft</strong>
          </div>
          <div>
            <span>SNAPSHOT</span>
            <strong>REV.{agent.baseRevision || state.draft.revision}</strong>
          </div>
          <div>
            <span>VALIDATION</span>
            <strong>{state.validation.runId}</strong>
          </div>
        </section>

        <div className={styles.drawerBody}>
          {view === "threads" &&
          (agent.status === "running" || agent.status === "validating") ? (
            <button
              className={styles.runningThreadRail}
              onClick={() => setView("collaboration")}
              type="button"
            >
              <span>
                {agent.status === "validating" ? "VALIDATING" : "RUNNING"} /{" "}
                {agent.taskId}
              </span>
              <strong>{agent.taskLabel}</strong>
              <i>
                <b style={{ width: `${agent.progress}%` }} />
              </i>
              <small>返回当前任务 ↗</small>
            </button>
          ) : null}

          {view === "threads" ? (
            <ThreadWorkspace
              currentRevision={state.draft.revision}
              onBackToCollaboration={() => setView("collaboration")}
              onSelectThread={setSelectedThreadId}
              selectedThreadId={selectedThreadId}
              threads={agent.history}
              validationRunId={state.validation.runId}
            />
          ) : null}

          {view === "collaboration" && agent.status === "idle" ? (
            <IdleWorkspace
              customMutationTask={customMutationTask}
              instruction={instruction}
              onInstructionChange={setInstruction}
              onMutationChange={setCustomMutationTask}
              onOpenThreads={() => setView("threads")}
              onPrepareCustom={prepareCustomTask}
              onPrepareTask={prepareTask}
              threadCount={agent.history.length}
            />
          ) : null}

          {view === "collaboration" && agent.status === "preview" ? (
            <section className={styles.preview}>
              <header>
                <span>EXECUTION PREVIEW / {agent.taskId}</span>
                <strong>{agent.taskLabel}</strong>
                <p>{agent.instruction}</p>
              </header>

              <div className={styles.previewGrid}>
                <article>
                  <span>01 / 事实基线</span>
                  <strong>Draft REV.{agent.baseRevision}</strong>
                  <small>运行期间始终绑定该 Revision</small>
                </article>
                <article>
                  <span>02 / 读取范围</span>
                  <strong>整个 Draft</strong>
                  <small>按任务检索并公开对象清单</small>
                </article>
                <article className={agent.mutationTask ? styles.locked : ""}>
                  <span>03 / 编辑策略</span>
                  <strong>{agent.mutationTask ? "全局只读锁" : "保持可编辑"}</strong>
                  <small>
                    {agent.mutationTask
                      ? "生成期间只能查看或取消"
                      : "只读分析不会阻断人工编辑"}
                  </small>
                </article>
                <article>
                  <span>04 / 预期产物</span>
                  <strong>
                    {agent.mutationTask ? "结构化变更集" : "全局分析报告"}
                  </strong>
                  <small>所有结论保留对象与规则来源</small>
                </article>
              </div>

              <div className={styles.previewNotice}>
                <b>!</b>
                <p>
                  Agent 拥有整个 Draft 的读取权限，但不会静默修改内容。
                  {agent.mutationTask
                    ? "启动后 Draft 将进入只读观察模式。"
                    : "本任务仅输出分析结论。"}
                </p>
              </div>

              <footer className={styles.actionRow}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => dispatch({ type: "reset-agent-session" })}
                  type="button"
                >
                  返回修改
                </button>
                <button
                  className={styles.primaryButton}
                  onClick={() => dispatch({ type: "start-agent-task" })}
                  type="button"
                >
                  确认并开始 ↗
                </button>
              </footer>
            </section>
          ) : null}

          {view === "collaboration" && agent.status === "running" ? (
            <RunningTask agent={agent} onCancel={() => dispatch({ type: "cancel-agent-task" })} />
          ) : null}

          {view === "collaboration" && agent.status === "review" ? (
            <section className={styles.review}>
              <header className={styles.sectionIntro}>
                <span>CHANGESET / {agent.taskId}</span>
                <strong>全局变更集等待审阅</strong>
                <p>
                  基于 REV.{agent.baseRevision}，读取 {agent.readObjectIds.length}{" "}
                  个对象，形成 {agent.changes.length} 项建议。
                </p>
              </header>

              <div className={styles.findingStrip}>
                {agent.findings.map((finding, index) => (
                  <span key={finding}>
                    <b>{String(index + 1).padStart(2, "0")}</b>
                    {finding}
                  </span>
                ))}
              </div>

              <div className={styles.changeToolbar}>
                <span>
                  已选择 <b>{selectedChangeCount}</b> / {agent.changes.length}
                </span>
                <button
                  onClick={() =>
                    dispatch({
                      type: "select-all-agent-changes",
                      selected: selectedChangeCount !== agent.changes.length,
                    })
                  }
                  type="button"
                >
                  {selectedChangeCount === agent.changes.length
                    ? "取消全选"
                    : "全部选择"}
                </button>
              </div>

              <div className={styles.changeList}>
                {agent.changes.map((change, index) => (
                  <label
                    className={`${styles.changeCard} ${change.selected ? styles.selectedChange : ""}`}
                    key={change.id}
                  >
                    <input
                      checked={change.selected}
                      onChange={() =>
                        dispatch({
                          type: "toggle-agent-change",
                          id: change.id,
                        })
                      }
                      type="checkbox"
                    />
                    <span className={styles.changeNumber}>
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <span className={styles.changeContent}>
                      <span>
                        {change.objectId} / {change.field.toUpperCase()}
                      </span>
                      <strong>{change.objectLabel}</strong>
                      <small>{change.rationale}</small>
                      <code>
                        <i>− {change.before}</i>
                        <b>＋ {change.after}</b>
                      </code>
                    </span>
                  </label>
                ))}
              </div>

              <footer className={styles.reviewFooter}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => dispatch({ type: "reject-agent-changes" })}
                  type="button"
                >
                  拒绝全部
                </button>
                <button
                  className={styles.primaryButton}
                  disabled={selectedChangeCount === 0}
                  onClick={() => dispatch({ type: "apply-agent-changes" })}
                  type="button"
                >
                  采纳 {selectedChangeCount} 项并验证 ↗
                </button>
              </footer>
            </section>
          ) : null}

          {view === "collaboration" && agent.status === "stale" ? (
            <section className={styles.stateMessage}>
              <span className={styles.stateGlyph}>↺</span>
              <small>REVISION CONFLICT</small>
              <strong>变更集需要重新基准化</strong>
              <p>
                该变更集基于 REV.{agent.baseRevision}，当前 Draft 已到 REV.
                {state.draft.revision}。旧建议不会覆盖新内容。
              </p>
              <button
                className={styles.primaryButton}
                onClick={() => dispatch({ type: "rebase-agent-task" })}
                type="button"
              >
                基于当前 Revision 重新运行
              </button>
            </section>
          ) : null}

          {view === "collaboration" && agent.status === "validating" ? (
            <section className={styles.stateMessage}>
              <span className={`${styles.stateGlyph} ${styles.pulse}`}>V</span>
              <small>DETERMINISTIC VALIDATOR</small>
              <strong>新 Revision 已写入，正在自动验证</strong>
              <p>
                Agent 无权声明自己的修改有效。Validator 正在独立检查 Schema、
                引用、时间线与知识状态规则。
              </p>
              <div className={styles.validationRail}>
                <span>SCHEMA</span>
                <span>REFERENCES</span>
                <span>TIMELINE</span>
                <span>KNOWLEDGE</span>
              </div>
            </section>
          ) : null}

          {view === "collaboration" && agent.status === "completed" ? (
            <section className={styles.completed}>
              <header className={styles.sectionIntro}>
                <span>THREAD / {agent.threadId}</span>
                <strong>{agent.stage}</strong>
                <p>
                  线程已绑定 REV.{agent.baseRevision}，所有读取范围、发现和人工决策均已保留。
                </p>
              </header>

              {agent.findings.length ? (
                <div className={styles.completedFindings}>
                  {agent.findings.map((finding) => (
                    <span key={finding}>✓ {finding}</span>
                  ))}
                </div>
              ) : null}

              <div className={styles.completionReceipt}>
                <span>
                  当前 Draft <b>REV.{state.draft.revision}</b>
                </span>
                <span>
                  Validator <b>{state.validation.runId}</b>
                </span>
                <span>
                  状态{" "}
                  <b>
                    {state.validation.status === "fresh"
                      ? "已验证"
                      : "需要验证"}
                  </b>
                </span>
              </div>

              <div className={styles.completedActions}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => {
                    setSelectedThreadId(agent.threadId);
                    setView("threads");
                  }}
                  type="button"
                >
                  查看线程
                </button>
                <button
                  className={styles.primaryButton}
                  onClick={() => dispatch({ type: "reset-agent-session" })}
                  type="button"
                >
                  开始新任务
                </button>
              </div>
            </section>
          ) : null}
        </div>
      </aside>
    </>
  );
}

function IdleWorkspace({
  customMutationTask,
  instruction,
  onInstructionChange,
  onMutationChange,
  onOpenThreads,
  onPrepareCustom,
  onPrepareTask,
  threadCount,
}: {
  customMutationTask: boolean;
  instruction: string;
  onInstructionChange: (value: string) => void;
  onMutationChange: (value: boolean) => void;
  onOpenThreads: () => void;
  onPrepareCustom: () => void;
  onPrepareTask: (option: AgentTaskOption) => void;
  threadCount: number;
}) {
  return (
    <section className={styles.idle}>
      <header className={styles.sectionIntro}>
        <span>DRAFT COMMAND / GLOBAL VIEW</span>
        <strong>今天要和 Agent 一起完成什么？</strong>
        <p>
          Agent 可以读取整个 Draft，但所有修改都会先形成独立变更集，决定权仍属于你。
        </p>
      </header>

      <div className={styles.draftSummary}>
        <article>
          <span>15</span>
          <small>对象</small>
        </article>
        <article>
          <span>04</span>
          <small>事件</small>
        </article>
        <article>
          <span>03</span>
          <small>开放问题</small>
        </article>
        <article>
          <span>01</span>
          <small>S1 阻断</small>
        </article>
      </div>

      <div className={styles.taskGrid}>
        {taskOptions.map((option) => (
          <button
            key={option.code}
            onClick={() => onPrepareTask(option)}
            type="button"
          >
            <span>{option.code}</span>
            <strong>{option.label}</strong>
            <small>{option.description}</small>
            <b>{option.mutationTask ? "CHANGESET ↗" : "READ ONLY ↗"}</b>
          </button>
        ))}
      </div>

      <section className={styles.customPrompt}>
        <label htmlFor="agent-instruction">自由指令 / DIRECTIVE</label>
        <textarea
          id="agent-instruction"
          onChange={(event) => onInstructionChange(event.target.value)}
          placeholder="例如：检查所有角色在第三阶段知道了什么，并修复提前泄露的信息。"
          rows={4}
          value={instruction}
        />
        <div>
          <label>
            <input
              checked={!customMutationTask}
              name="agent-mode"
              onChange={() => onMutationChange(false)}
              type="radio"
            />
            只读分析
          </label>
          <label>
            <input
              checked={customMutationTask}
              name="agent-mode"
              onChange={() => onMutationChange(true)}
              type="radio"
            />
            生成变更集
          </label>
          <button
            disabled={!instruction.trim()}
            onClick={onPrepareCustom}
            type="button"
          >
            预览任务 ↗
          </button>
        </div>
      </section>

      <button
        className={styles.threadIndexShortcut}
        onClick={onOpenThreads}
        type="button"
      >
        <span>
          <small>THREAD INDEX / CURRENT DRAFT</small>
          <strong>查找协作线程</strong>
        </span>
        <b>{String(threadCount).padStart(2, "0")} ↗</b>
      </button>
    </section>
  );
}

type ThreadStatusFilter =
  | "all"
  | "attention"
  | AgentThreadStatus
  | "archived";

function ThreadWorkspace({
  currentRevision,
  onBackToCollaboration,
  onSelectThread,
  selectedThreadId,
  threads,
  validationRunId,
}: {
  currentRevision: number;
  onBackToCollaboration: () => void;
  onSelectThread: (id: string | null) => void;
  selectedThreadId: string | null;
  threads: AgentThread[];
  validationRunId: string;
}) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] =
    useState<ThreadStatusFilter>("all");
  const [taskFilter, setTaskFilter] = useState<AgentThreadTaskType | "all">(
    "all",
  );
  const [revisionFilter, setRevisionFilter] = useState<number | "all">("all");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [visibleCount, setVisibleCount] = useState(20);

  const selectedThread = threads.find(
    (thread) => thread.id === selectedThreadId,
  );
  const revisions = useMemo(
    () =>
      [...new Set(threads.map((thread) => thread.baseRevision))].sort(
        (left, right) => right - left,
      ),
    [threads],
  );

  const filteredThreads = useMemo(() => {
    const filtered = threads.filter((thread) => {
      if (!agentThreadMatchesQuery(thread, query)) return false;
      if (favoritesOnly && !thread.favorite) return false;
      if (taskFilter !== "all" && thread.taskType !== taskFilter) return false;
      if (
        revisionFilter !== "all" &&
        thread.baseRevision !== revisionFilter
      ) {
        return false;
      }
      if (statusFilter === "archived") return thread.archived;
      if (thread.archived) return false;
      if (statusFilter === "attention") return agentThreadNeedsAttention(thread);
      if (statusFilter !== "all" && thread.status !== statusFilter) return false;
      return true;
    });
    return sortAgentThreads(filtered);
  }, [
    favoritesOnly,
    query,
    revisionFilter,
    statusFilter,
    taskFilter,
    threads,
  ]);

  if (selectedThread) {
    return (
      <ThreadDetail
        key={selectedThread.id}
        currentRevision={currentRevision}
        onBack={() => onSelectThread(null)}
        onBackToCollaboration={onBackToCollaboration}
        onSelectThread={onSelectThread}
        thread={selectedThread}
        threads={threads}
        validationRunId={validationRunId}
      />
    );
  }

  const visibleThreads = filteredThreads.slice(0, visibleCount);
  const attentionCount = threads.filter(
    (thread) => !thread.archived && agentThreadNeedsAttention(thread),
  ).length;
  const activeCount = threads.filter((thread) => !thread.archived).length;

  return (
    <section className={styles.threadWorkspace}>
      <header className={styles.threadIndexHeader}>
        <span>THREAD INDEX / CURRENT DRAFT</span>
        <strong>协作线程</strong>
        <p>
          按任务、Revision 和确定性验证回执查找 Agent
          结论。旧线程保持只读，不会混入当前 Draft。
        </p>
        <div>
          <span>
            <b>{activeCount}</b> 活跃记录
          </span>
          <span>
            <b>{attentionCount}</b> 需要处理
          </span>
          <span>
            <b>{threads.filter((thread) => thread.favorite).length}</b> 已收藏
          </span>
        </div>
      </header>

      <section className={styles.threadSearchPanel}>
        <label>
          <span aria-hidden="true">⌕</span>
          <input
            aria-label="搜索协作线程"
            onChange={(event) => {
              setQuery(event.target.value);
              setVisibleCount(20);
            }}
            placeholder="搜索标题、结论、对象 ID、REV 或 Validator…"
            value={query}
          />
          {query ? (
            <button
              aria-label="清除线程搜索"
              onClick={() => setQuery("")}
              type="button"
            >
              ×
            </button>
          ) : null}
        </label>
        <div>
          <select
            aria-label="按线程状态筛选"
            onChange={(event) => {
              setStatusFilter(event.target.value as ThreadStatusFilter);
              setVisibleCount(20);
            }}
            value={statusFilter}
          >
            <option value="all">全部状态</option>
            <option value="attention">需要处理</option>
            <option value="running">运行中</option>
            <option value="review">待审阅</option>
            <option value="completed">已完成</option>
            <option value="cancelled">已取消</option>
            <option value="failed">运行失败</option>
            <option value="archived">已归档</option>
          </select>
          <select
            aria-label="按任务类型筛选"
            onChange={(event) => {
              setTaskFilter(
                event.target.value as AgentThreadTaskType | "all",
              );
              setVisibleCount(20);
            }}
            value={taskFilter}
          >
            <option value="all">全部任务</option>
            {Object.entries(threadTaskLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="按 Draft Revision 筛选"
            onChange={(event) => {
              setRevisionFilter(
                event.target.value === "all"
                  ? "all"
                  : Number(event.target.value),
              );
              setVisibleCount(20);
            }}
            value={revisionFilter}
          >
            <option value="all">全部 Revision</option>
            {revisions.map((revision) => (
              <option key={revision} value={revision}>
                REV.{revision}
              </option>
            ))}
          </select>
          <button
            aria-pressed={favoritesOnly}
            className={favoritesOnly ? styles.activeFavoriteFilter : ""}
            onClick={() => {
              setFavoritesOnly((value) => !value);
              setVisibleCount(20);
            }}
            type="button"
          >
            ☆ 仅看收藏
          </button>
        </div>
      </section>

      <div className={styles.threadListMeta}>
        <span>
          找到 <b>{filteredThreads.length}</b> 条线程
        </span>
        <small>待处理优先 · 其余按最近更新</small>
      </div>

      {visibleThreads.length ? (
        <div className={styles.threadList}>
          {visibleThreads.map((thread) => (
            <ThreadListItem
              key={thread.id}
              onOpen={() => onSelectThread(thread.id)}
              thread={thread}
            />
          ))}
        </div>
      ) : (
        <div className={styles.threadEmpty}>
          <b>00</b>
          <strong>没有匹配的协作线程</strong>
          <span>调整关键词或筛选条件后再试。</span>
        </div>
      )}

      {visibleCount < filteredThreads.length ? (
        <button
          className={styles.loadMoreThreads}
          onClick={() => setVisibleCount((count) => count + 20)}
          type="button"
        >
          加载更多 · {filteredThreads.length - visibleCount} ↘
        </button>
      ) : null}
    </section>
  );
}

function ThreadListItem({
  onOpen,
  thread,
}: {
  onOpen: () => void;
  thread: AgentThread;
}) {
  const { dispatch } = usePrototype();
  return (
    <article
      className={styles.threadListItem}
      data-attention={agentThreadNeedsAttention(thread) ? "true" : undefined}
      data-status={thread.status}
    >
      <button className={styles.threadListMain} onClick={onOpen} type="button">
        <span className={styles.threadSequence}>{thread.id.slice(-2)}</span>
        <span className={styles.threadListContent}>
          <span>
            {thread.id} / {threadTaskLabels[thread.taskType]}
          </span>
          <strong>{thread.label}</strong>
          <small>{thread.summary}</small>
          <span className={styles.threadListFacts}>
            <i>REV.{thread.baseRevision}</i>
            <i>{thread.updatedAt}</i>
            <i>{thread.findings.length} 发现</i>
            <i>{thread.changeCount} 变更</i>
          </span>
        </span>
        <span className={styles.threadStatus}>
          {threadStatusLabels[thread.status]}
        </span>
      </button>
      <button
        aria-label={thread.favorite ? "取消收藏线程" : "收藏线程"}
        aria-pressed={thread.favorite}
        className={styles.threadFavorite}
        onClick={() =>
          dispatch({ type: "toggle-agent-thread-favorite", id: thread.id })
        }
        type="button"
      >
        {thread.favorite ? "★" : "☆"}
      </button>
    </article>
  );
}

function ThreadDetail({
  currentRevision,
  onBack,
  onBackToCollaboration,
  onSelectThread,
  thread,
  threads,
  validationRunId,
}: {
  currentRevision: number;
  onBack: () => void;
  onBackToCollaboration: () => void;
  onSelectThread: (id: string) => void;
  thread: AgentThread;
  threads: AgentThread[];
  validationRunId: string;
}) {
  const { dispatch } = usePrototype();
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(thread.label);
  const sourceThread = threads.find(
    (item) => item.id === thread.sourceThreadId,
  );

  function saveTitle() {
    const value = titleDraft.trim();
    if (!value) return;
    dispatch({ type: "rename-agent-thread", id: thread.id, label: value });
    setEditingTitle(false);
  }

  function rerunThread() {
    dispatch({
      type: "prepare-agent-task",
      label: `重新运行：${thread.label}`,
      instruction: thread.instruction,
      mutationTask:
        thread.taskType === "gaps" ||
        thread.taskType === "patch" ||
        thread.changeCount > 0,
      taskType: thread.taskType,
      sourceThreadId: thread.id,
    });
    onBackToCollaboration();
  }

  return (
    <section className={styles.threadDetail}>
      <button className={styles.threadBack} onClick={onBack} type="button">
        ← 返回线程索引
      </button>

      <header className={styles.threadDetailHeader}>
        <span>
          THREAD / {thread.id}
          {thread.favorite ? " / FAVORITE" : ""}
        </span>
        {editingTitle ? (
          <div className={styles.threadTitleEditor}>
            <input
              autoFocus
              onChange={(event) => setTitleDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") saveTitle();
                if (event.key === "Escape") setEditingTitle(false);
              }}
              value={titleDraft}
            />
            <button onClick={saveTitle} type="button">
              保存
            </button>
            <button onClick={() => setEditingTitle(false)} type="button">
              取消
            </button>
          </div>
        ) : (
          <div>
            <strong>{thread.label}</strong>
            <button onClick={() => setEditingTitle(true)} type="button">
              重命名
            </button>
          </div>
        )}
        <p>{thread.instruction}</p>
      </header>

      {thread.baseRevision !== currentRevision ? (
        <div className={styles.oldRevisionNotice}>
          <b>REVISION BOUND</b>
          <span>
            该线程绑定 REV.{thread.baseRevision}，当前 Draft 已是 REV.
            {currentRevision}。历史内容保持只读，继续任务会创建关联线程。
          </span>
        </div>
      ) : null}

      <section className={styles.threadLedger}>
        <div>
          <span>状态</span>
          <strong>{threadStatusLabels[thread.status]}</strong>
        </div>
        <div>
          <span>任务类型</span>
          <strong>{threadTaskLabels[thread.taskType]}</strong>
        </div>
        <div>
          <span>事实基线</span>
          <strong>REV.{thread.baseRevision}</strong>
        </div>
        <div>
          <span>结果 Revision</span>
          <strong>
            {thread.outcomeRevision ? `REV.${thread.outcomeRevision}` : "—"}
          </strong>
        </div>
        <div>
          <span>Validator</span>
          <strong>{thread.validatorRunId ?? "未产生"}</strong>
        </div>
        <div>
          <span>最近更新</span>
          <strong>{thread.updatedAt}</strong>
        </div>
      </section>

      {sourceThread ? (
        <button
          className={styles.sourceThread}
          onClick={() => onSelectThread(sourceThread.id)}
          type="button"
        >
          <span>来源线程</span>
          <strong>{sourceThread.label}</strong>
          <small>{sourceThread.id} ↗</small>
        </button>
      ) : null}

      <section className={styles.threadOutcome}>
        <span>OUTCOME / 人工决策回执</span>
        <strong>{thread.summary}</strong>
        <small>
          {thread.changeCount
            ? `${thread.changeCount} 项结构化变更已记录`
            : "本线程未直接写入 Draft"}
          {thread.validatorRunId
            ? ` · ${thread.validatorRunId} 已保留`
            : ` · 当前验证 ${validationRunId}`}
        </small>
      </section>

      {thread.findings.length ? (
        <section className={styles.threadFindings}>
          <header>
            <span>关键结论</span>
            <small>{thread.findings.length} FINDINGS</small>
          </header>
          {thread.findings.map((finding, index) => (
            <p key={finding}>
              <b>{String(index + 1).padStart(2, "0")}</b>
              {finding}
            </p>
          ))}
        </section>
      ) : null}

      <section className={styles.threadObjects}>
        <header>
          <span>读取对象</span>
          <small>{thread.objectIds.length} OBJECTS</small>
        </header>
        <div>
          {thread.objectIds.map((objectId) => (
            <span key={objectId}>{objectId}</span>
          ))}
        </div>
      </section>

      <footer className={styles.threadDetailActions}>
        <button
          onClick={() =>
            dispatch({ type: "toggle-agent-thread-favorite", id: thread.id })
          }
          type="button"
        >
          {thread.favorite ? "★ 已收藏" : "☆ 收藏线程"}
        </button>
        {thread.archived ? (
          <button
            onClick={() =>
              dispatch({ type: "restore-agent-thread", id: thread.id })
            }
            type="button"
          >
            恢复归档
          </button>
        ) : isTerminalAgentThread(thread) ? (
          <button
            onClick={() =>
              dispatch({ type: "archive-agent-thread", id: thread.id })
            }
            type="button"
          >
            归档线程
          </button>
        ) : null}
        <button
          className={styles.primaryButton}
          onClick={rerunThread}
          type="button"
        >
          基于当前 Draft 重新运行 ↗
        </button>
      </footer>
    </section>
  );
}

function RunningTask({
  agent,
  onCancel,
}: {
  agent: PrototypeAgentState;
  onCancel: () => void;
}) {
  const stages = [
    "建立 Draft 全局索引",
    "梳理事件与知识状态",
    "检查因果与信息流",
    agent.mutationTask ? "组织结构化变更集" : "组织分析结论",
  ];

  return (
    <section className={styles.running}>
      <header className={styles.sectionIntro}>
        <span>BACKGROUND TASK / {agent.taskId}</span>
        <strong>{agent.taskLabel}</strong>
        <p>{agent.instruction}</p>
      </header>

      {agent.mutationTask ? (
        <div className={styles.readOnlyCallout}>
          <b>READ ONLY</b>
          <span>
            Agent 正在基于 REV.{agent.baseRevision} 生成变更集。期间只能查看
            Draft，取消任务后将立即恢复编辑。
          </span>
        </div>
      ) : null}

      <div className={styles.progressBlock}>
        <div>
          <span>任务进度</span>
          <strong>{agent.progress}%</strong>
        </div>
        <i>
          <b style={{ width: `${agent.progress}%` }} />
        </i>
        <small>{agent.stage}</small>
      </div>

      <ol className={styles.stageList}>
        {stages.map((stage, index) => {
          const threshold = [12, 34, 68, 92][index];
          const active =
            agent.progress >= threshold &&
            (index === stages.length - 1 ||
              agent.progress < [34, 68, 92, 101][index]);
          const complete =
            index < stages.length - 1 &&
            agent.progress >= [34, 68, 92][index];
          return (
            <li
              className={
                complete
                  ? styles.completeStage
                  : active
                    ? styles.activeStage
                    : undefined
              }
              key={stage}
            >
              <span>{complete ? "✓" : String(index + 1).padStart(2, "0")}</span>
              <strong>{stage}</strong>
              <small>
                {complete ? "已完成" : active ? "进行中" : "等待"}
              </small>
            </li>
          );
        })}
      </ol>

      <section className={styles.contextManifest}>
        <header>
          <span>本次读取清单</span>
          <small>{agent.readObjectIds.length} OBJECTS OBSERVED</small>
        </header>
        <div>
          {agent.readObjectIds.map((objectId) => (
            <span key={objectId}>{objectId}</span>
          ))}
        </div>
      </section>

      <button
        className={styles.cancelButton}
        onClick={onCancel}
        type="button"
      >
        取消任务并解除锁定
      </button>
    </section>
  );
}
