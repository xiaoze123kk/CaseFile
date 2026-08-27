import type {
  PublicAgentRun,
  PublicContextState,
  PublicVerificationStatus,
} from "@casefile/contracts";

import styles from "./workbench-agent.module.css";

export interface AgentVerificationProgress {
  status: PublicVerificationStatus;
  summary: string;
}

const activityLabels: Record<Exclude<PublicAgentRun["activity"], null>, string> = {
  understanding: "正在理解你的要求",
  reading: "正在阅读卷宗",
  checking: "正在检查前后一致性",
  preparing_changes: "正在整理修改建议",
  finalizing: "正在完成回复",
};

const contextLabels: Partial<Record<PublicContextState, string>> = {
  near_limit: "对话内容较长，正在控制上下文",
  compacted: "已整理较早的对话内容",
};

function runStageLabel(run: PublicAgentRun): string {
  if (run.status === "queued") return "回复已排队";
  if (run.status === "running") {
    return run.activity === null ? "正在准备回复" : activityLabels[run.activity];
  }
  if (run.status === "cancelling") return "正在取消";
  if (run.status === "succeeded") return "回复已完成";
  if (run.status === "cancelled") return "回复已取消";
  return "回复未完成";
}

function runSummary(run: PublicAgentRun): string | null {
  if (run.status === "failed") {
    return run.failure?.message ?? "这次回复未能完成，请稍后重试。";
  }
  if (run.status === "cancelled") return "已停止这次回复，没有生成新的结论。";
  if (run.status === "succeeded") return "完整结论已记录到对话。";
  return null;
}

export function WorkbenchAgentTaskStrip({
  run,
  contextState,
  verificationProgress,
  onCancel,
}: {
  run: PublicAgentRun | null;
  contextState?: PublicContextState | null;
  verificationProgress?: AgentVerificationProgress | null;
  onCancel?: () => void;
}) {
  if (run === null) return null;
  const active =
    run.status === "queued" ||
    run.status === "running" ||
    run.status === "cancelling";
  const summary = runSummary(run);
  const stage =
    verificationProgress?.status === "started"
      ? "正在复查修改影响"
      : verificationProgress?.summary || runStageLabel(run);
  const contextLabel = contextState ? contextLabels[contextState] : undefined;

  return (
    <section
      aria-atomic="true"
      aria-label="Agent 回复状态"
      aria-live="polite"
      className={styles.agentTaskStrip}
      data-status={run.status}
    >
      <div className={styles.agentTaskPrimary}>
        <i aria-hidden="true" />
        <span>
          <strong>{active ? `卷宗统筹 · ${stage}` : stage}</strong>
          {contextLabel ? <small>{contextLabel}</small> : null}
        </span>
      </div>
      {active && run.cancellable && onCancel ? (
        <button onClick={onCancel} type="button">
          停止回复
        </button>
      ) : null}
      {!active && summary ? (
        <details className={styles.agentTaskSummary}>
          <summary>回复摘要</summary>
          <p>{summary}</p>
        </details>
      ) : null}
    </section>
  );
}
