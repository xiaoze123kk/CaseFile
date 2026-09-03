import type {
  WorkbenchAuditEntryView,
  WorkbenchContextView,
} from "@/lib/api-client";

import styles from "./workbench-context-panels.module.css";

export interface WorkbenchContextState {
  data: WorkbenchContextView | null;
  error: string | null;
  loading: boolean;
}

function ContextStateMessage({
  state,
  loadingTitle,
  emptyTitle,
  emptyDetail,
  onRetry,
}: {
  state: WorkbenchContextState;
  loadingTitle: string;
  emptyTitle: string;
  emptyDetail: string;
  onRetry: () => void;
}) {
  if (state.loading) {
    return (
      <div aria-busy="true" className={styles.realEmptyState}>
        <strong>{loadingTitle}</strong>
        <p>正在从当前工作稿的服务端读模型读取事实。</p>
      </div>
    );
  }
  if (state.error) {
    return (
      <div className={styles.realEmptyState} role="alert">
        <strong>工作台事实读取失败</strong>
        <p>{state.error}</p>
        <button onClick={onRetry} type="button">重新读取</button>
      </div>
    );
  }
  return (
    <div className={styles.realEmptyState}>
      <strong>{emptyTitle}</strong>
      <p>{emptyDetail}</p>
    </div>
  );
}

export function WorkbenchValidationPanel({
  state,
  onRetry,
}: {
  state: WorkbenchContextState;
  onRetry: () => void;
}) {
  const validation = state.data?.validation ?? null;
  if (!validation) {
    return (
      <ContextStateMessage
        emptyDetail="当前项目没有可验证的冻结 Draft。"
        emptyTitle="暂无验证结果"
        loadingTitle="正在执行确定性验证"
        onRetry={onRetry}
        state={state}
      />
    );
  }
  if (validation.status === "unavailable") {
    return (
      <div className={styles.realEmptyState}>
        <strong>当前工作稿暂不可验证</strong>
        <p>草稿尚未关联已确认的 Brief；采用候选后可重新验证。</p>
        <button onClick={onRetry} type="button">重新验证</button>
      </div>
    );
  }
  if (validation.status === "passed") {
    return (
      <div className={styles.realEmptyState} data-tone="success">
        <strong>确定性验证已通过</strong>
        <p>CaseFile {validation.schema_version} 的结构、对象引用与确定性语义门禁均通过。</p>
        <button onClick={onRetry} type="button">重新验证</button>
      </div>
    );
  }
  return (
    <div className={styles.realValidationPanel}>
      <header>
        <span>CASEFILE VALIDATOR</span>
        <strong>{validation.issue_count} 个确定性问题</strong>
        <small>{validation.validator}</small>
      </header>
      <ol>
        {validation.issues.map((issue) => (
          <li key={issue.issue_id}>
            <span>{issue.severity === "error" ? "错误" : issue.severity}</span>
            <div>
              <strong>{issue.message}</strong>
              <code>{issue.code} · {issue.path || "/"}</code>
            </div>
          </li>
        ))}
      </ol>
      <button onClick={onRetry} type="button">重新验证当前工作稿</button>
    </div>
  );
}

export function WorkbenchAuditPanel({
  state,
  onRetry,
}: {
  state: WorkbenchContextState;
  onRetry: () => void;
}) {
  const context = state.data;
  if (!context) {
    return (
      <ContextStateMessage
        emptyDetail="当前工作稿还没有只追加的操作或审计事实。"
        emptyTitle="暂无审计记录"
        loadingTitle="正在读取审计事实"
        onRetry={onRetry}
        state={state}
      />
    );
  }
  if (context.audit_entries.length === 0) {
    return (
      <div className={styles.realEmptyState}>
        <strong>当前工作稿尚无审计事实</strong>
        <p>这里仅展示 audit_events 与 draft_operations 的现有记录。</p>
        <button onClick={onRetry} type="button">重新读取</button>
      </div>
    );
  }
  return (
    <div className={styles.auditInspector}>
      <div className={styles.auditStatus}>
        <span>当前工作稿修订</span>
        <strong>R{context.draft_revision}</strong>
        <small>{context.audit_entries.length} 条真实只追加事实</small>
      </div>
      <ol>{context.audit_entries.map((entry) => <AuditEntry key={entry.entry_id} entry={entry} />)}</ol>
    </div>
  );
}

function AuditEntry({ entry }: { entry: WorkbenchAuditEntryView }) {
  return (
    <li>
      <time dateTime={entry.occurred_at}>{formatClock(entry.occurred_at)}</time>
      <i aria-hidden="true" />
      <div>
        <span>{actorLabel(entry)}</span>
        <strong>{actionLabel(entry.action)}</strong>
        <small>{auditDetail(entry)}</small>
        <code className={styles.auditProvenance}>来源 {entry.source_table} #{entry.record_id}</code>
      </div>
    </li>
  );
}

function actorLabel(entry: WorkbenchAuditEntryView) {
  if (entry.actor.kind === "user") return `用户 #${entry.actor.user_id ?? "—"}`;
  return entry.actor.ref ? `${entry.actor.kind} · ${entry.actor.ref}` : entry.actor.kind;
}

function actionLabel(action: string) {
  const labels: Record<string, string> = {
    add: "新增对象内容",
    remove: "移除对象内容",
    replace: "修改对象字段",
    agent_generate_from_brief: "Agent 从 Brief 生成工作稿",
    agent_adopt_brief_candidate: "采用 Draft 候选",
    agent_patch_apply: "应用 Agent 补丁",
    agent_patch_undo: "撤销 Agent 补丁",
  };
  return labels[action] ?? action;
}

function auditDetail(entry: WorkbenchAuditEntryView) {
  if (entry.source_table === "draft_operations") {
    const objectId = stringDetail(entry.details.object_id) ?? "Draft";
    const fieldPath = stringDetail(entry.details.field_path) || "/";
    return `${objectId} · ${fieldPath} · R${String(entry.details.base_revision)} → R${String(entry.details.result_revision)}`;
  }
  const trace = entry.trace_id ? ` · trace ${entry.trace_id}` : "";
  return `${entry.target_type} #${String(entry.target_id)}${trace}`;
}

function stringDetail(value: unknown) {
  return typeof value === "string" ? value : null;
}

function formatClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}
