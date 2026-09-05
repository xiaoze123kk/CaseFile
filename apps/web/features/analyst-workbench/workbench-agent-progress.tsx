import type { PublicAgentRun } from "@casefile/contracts";
import type { ReactNode } from "react";
import { activityLabels, type RunFeedback } from "./workbench-agent-feedback";
import styles from "./workbench-agent.module.css";

export function AgentProgress({ feedback, run, controls }: { feedback?: RunFeedback; run: PublicAgentRun | null; controls?: ReactNode }) {
  if (!run) return controls ?? null;
  const active = ["queued", "running", "cancelling"].includes(run.status);
  if (!active) return controls ?? null;
  const activity = feedback?.activities.filter((item) => item.status === "started").at(-1);
  const completed = feedback?.activities.filter((item) => item.status !== "started") ?? [];
  const label = run.status === "queued" ? "回复已排队" : run.status === "cancelling" ? "正在安全停止" :
    `正在${activityLabels[activity?.activity ?? run.activity ?? "understanding"]}`;
  const renderActivity = (item: NonNullable<RunFeedback>["activities"][number]) => (
    <li key={item.activity_id} data-status={item.status}>
      <span aria-hidden="true">{item.status === "failed" ? "!" : item.status === "completed" ? "✓" : "·"}</span>
      {activityLabels[item.activity]}{item.status === "failed" ? "未完成" : item.status === "completed" ? "已结束" : "中"}
      {item.activity === "reading" && item.status === "completed" && item.object_ids.length > 0 ? ` · 读取 ${item.object_ids.length} 个对象` : ""}
    </li>
  );
  return <section className={styles.agentProgress} aria-label="工作记录" data-active={active}>
    <div role="status" className={styles.progressHeading}><i aria-hidden="true" />{label}</div>
    {active ? <ul>{completed.slice(-2).map(renderActivity)}</ul> : null}
    {feedback?.context === "compacted" ? <small>已整理较早的对话内容</small> : null}
    {feedback?.context === "near_limit" ? <small>对话内容较长，正在整理上下文</small> : null}
    {feedback?.verification ? <p role="status" data-status={feedback.verification.verification_status}>{feedback.verification.summary}</p> : null}
    {feedback?.activities.length ? <details><summary>查看工作过程</summary><ul>{feedback.activities.map(renderActivity)}</ul></details> : null}
    {controls}
  </section>;
}
