import type {
  WorkbenchAuditEntryView,
  WorkbenchContextView,
  WorkbenchSourceView,
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
        <p>正在从当前 Draft 的服务端读模型读取事实。</p>
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
        <strong>当前 Draft 暂不可验证</strong>
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
      <button onClick={onRetry} type="button">重新验证当前 Draft</button>
    </div>
  );
}

export function WorkbenchSourcesPanel({
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
        emptyDetail="当前冻结 Brief 没有登记 SourceRecord。"
        emptyTitle="暂无来源记录"
        loadingTitle="正在读取来源正文"
        onRetry={onRetry}
        state={state}
      />
    );
  }
  if (context.sources.length === 0 && context.contract_source_refs.length === 0) {
    return (
      <div className={styles.realEmptyState}>
        <strong>当前 Draft 没有可展示的来源</strong>
        <p>这里只展示冻结 Brief 实际引用的 SourceRecord，不会补入本地样例。</p>
        <button onClick={onRetry} type="button">重新读取</button>
      </div>
    );
  }
  return (
    <div className={styles.sourceInspector}>
      <p>来源正文来自冻结 Brief 记录的 SourceRecord；表名、主键和内容哈希共同构成可追溯标识。</p>
      {context.sources.map((source) => (
        <SourceRecordCard key={source.trace_id} source={source} />
      ))}
      {context.contract_source_refs.length ? (
        <section className={styles.contractSourceRefs} aria-label="CaseFile 来源片段引用">
          <header>
            <strong>CaseFile 来源片段引用</strong>
            <small>{context.contract_source_refs.length} 个稳定标识</small>
          </header>
          <ul>
            {context.contract_source_refs.map((reference) => (
              <li key={reference.source_fragment_id}>
                <code>{reference.source_fragment_id}</code>
                <span>{reference.paths.join("、")}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function SourceRecordCard({ source }: { source: WorkbenchSourceView }) {
  return (
    <article>
      <header>
        <span>{sourceKindLabel(source.source_kind)}</span>
        <small>{formatDateTime(source.created_at)}</small>
      </header>
      <h2>{source.trace_id}</h2>
      <pre className={styles.sourceRecordBody}>{source.content_text}</pre>
      <dl className={styles.sourceTraceFacts}>
        <div><dt>内容哈希</dt><dd><code>{source.content_hash}</code></dd></div>
        <div><dt>父来源</dt><dd>{source.parent_source_record_id ? `source_records:${source.parent_source_record_id}` : "无"}</dd></div>
        <div><dt>生成任务</dt><dd>{source.generated_by_task_run_id ? `task_runs:${source.generated_by_task_run_id}` : "作者直接提交"}</dd></div>
      </dl>
    </article>
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
        emptyDetail="当前 Draft 还没有只追加的操作或审计事实。"
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
        <strong>当前 Draft 尚无审计事实</strong>
        <p>这里仅展示 audit_events 与 draft_operations 的现有记录。</p>
        <button onClick={onRetry} type="button">重新读取</button>
      </div>
    );
  }
  return (
    <div className={styles.auditInspector}>
      <div className={styles.auditStatus}>
        <span>当前 Draft 修订</span>
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

function sourceKindLabel(kind: WorkbenchSourceView["source_kind"]) {
  if (kind === "human_original") return "作者原稿";
  if (kind === "human_revision") return "作者修订";
  return "Agent 润色候选";
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

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}

function formatClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
}
