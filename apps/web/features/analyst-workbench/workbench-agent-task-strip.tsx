import type { TaskView } from "@/lib/api-client";

import styles from "./workbench-agent.module.css";

export interface AgentTaskContextOccupancy {
  usedTokens: number;
  budgetTokens: number | null;
}

export interface AgentVerificationProgress {
  status: string;
  findingCount: number;
}

function taskStageLabel(task: TaskView): string {
  if (task.status === "queued") return "任务已排队";
  if (task.status === "running") return task.stage || "等待任务阶段";
  if (task.status === "cancelling") return "正在取消";
  if (task.status === "succeeded") return "任务已完成";
  if (task.status === "cancelled") return "任务已取消";
  return "任务失败";
}

function taskSummary(task: TaskView): string | null {
  if (task.status === "failed") {
    return "任务未能完成；回复记录中保留了失败原因。";
  }
  if (task.status === "cancelled") return "任务已取消，没有生成新的结论。";
  if (task.status === "succeeded") return "任务完成；完整结论已记录到对话。";
  return null;
}

export function WorkbenchAgentTaskStrip({
  task,
  contextOccupancy,
  verificationProgress,
  onCancel,
}: {
  task: TaskView | null;
  contextOccupancy?: AgentTaskContextOccupancy | null;
  verificationProgress?: AgentVerificationProgress | null;
  onCancel?: () => void;
}) {
  if (task === null) return null;
  const active =
    task.status === "queued" ||
    task.status === "running" ||
    task.status === "cancelling";
  const summary = taskSummary(task);
  const stage = taskStageLabel(task);
  const verificationStage =
    verificationProgress?.status === "started" ||
    verificationProgress?.status === "finding"
      ? `验证复查 · 已发现 ${verificationProgress.findingCount} 项`
      : stage;

  return (
    <section
      aria-atomic="true"
      aria-label="Agent 任务状态"
      aria-live="polite"
      className={styles.agentTaskStrip}
      data-status={task.status}
    >
      <div className={styles.agentTaskPrimary}>
        <i aria-hidden="true" />
        <span>
          <strong>{active ? `Agent 正在回复 · ${verificationStage}` : stage}</strong>
          {contextOccupancy ? (
            <small>
              上下文 {contextOccupancy.usedTokens}
              {contextOccupancy.budgetTokens === null
                ? " tokens"
                : `/${contextOccupancy.budgetTokens} tokens`}
            </small>
          ) : null}
        </span>
      </div>
      {active && onCancel ? (
        <button onClick={onCancel} type="button">
          取消
        </button>
      ) : null}
      {!active && summary ? (
        <details className={styles.agentTaskSummary}>
          <summary>执行摘要</summary>
          <p>{summary}</p>
        </details>
      ) : null}
    </section>
  );
}
