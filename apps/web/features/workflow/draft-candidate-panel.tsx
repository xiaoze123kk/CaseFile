"use client";

import type { DraftCandidateView } from "@/lib/api-client";

import styles from "./brief-workspace.module.css";

interface DraftCandidatePanelProps {
  candidates: DraftCandidateView[];
  selectedTaskRunId: number | null;
  adopting: boolean;
  onSelect: (taskRunId: number) => void;
  onRequestAdopt: (candidate: DraftCandidateView) => void;
  onOpenWorkbench: () => void;
}

const countLabels: Array<[string, string]> = [
  ["entities", "实体"],
  ["events", "事件"],
  ["information_units", "信息"],
  ["reasoning_paths", "推理链"],
];

function completedTime(value: string | null) {
  if (!value) return "时间待记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间待记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function candidateState(candidate: DraftCandidateView) {
  if (candidate.is_current) return "当前工作稿";
  if (!candidate.is_current_brief) return "旧 Brief";
  if (candidate.is_adopted) return "历史采用";
  return "待采用";
}

export function DraftCandidatePanel({
  candidates,
  selectedTaskRunId,
  adopting,
  onSelect,
  onRequestAdopt,
  onOpenWorkbench,
}: DraftCandidatePanelProps) {
  const selected =
    candidates.find(
      (candidate) => candidate.task_run_id === selectedTaskRunId,
    ) ??
    candidates[0] ??
    null;

  return (
    <section
      aria-label="候选草稿档案"
      className={styles.candidateArchive}
    >
      <header className={styles.candidateArchiveHead}>
        <div>
          <span>Draft contact sheet</span>
          <strong>候选草稿档案</strong>
        </div>
        <b>{String(candidates.length).padStart(2, "0")} 份</b>
      </header>

      {candidates.length ? (
        <div className={styles.candidateArchiveBody}>
          <ol className={styles.candidateIndex}>
            {candidates.map((candidate, index) => (
              <li key={candidate.task_run_id}>
                <button
                  aria-pressed={
                    candidate.task_run_id === selected?.task_run_id
                  }
                  className={
                    candidate.task_run_id === selected?.task_run_id
                      ? styles.candidateIndexActive
                      : undefined
                  }
                  onClick={() => onSelect(candidate.task_run_id)}
                  type="button"
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <strong>{candidate.title}</strong>
                    <small>
                      {candidate.provider} · {completedTime(candidate.completed_at)}
                    </small>
                  </div>
                  <em>{candidateState(candidate)}</em>
                </button>
              </li>
            ))}
          </ol>

          {selected ? (
            <article className={styles.candidatePreview}>
              <header>
                <div>
                  <small>
                    候选 #{selected.task_run_id} · Brief v
                    {selected.brief_version_no}
                  </small>
                  <h3>{selected.title}</h3>
                </div>
                <span
                  className={
                    selected.is_current
                      ? styles.candidateCurrentStamp
                      : undefined
                  }
                >
                  {candidateState(selected)}
                </span>
              </header>

              <p className={styles.candidateQuestion}>
                {selected.reasoning_questions[0] ??
                  "该候选未声明核心推理命题。"}
              </p>

              <dl className={styles.candidateCounts}>
                {countLabels.map(([key, label]) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd>{selected.object_counts[key] ?? 0}</dd>
                  </div>
                ))}
              </dl>

              <div className={styles.candidateConstraints}>
                <small>创作约束摘录</small>
                {selected.constraint_statements.length ? (
                  <ul>
                    {selected.constraint_statements
                      .slice(0, 2)
                      .map((statement) => (
                        <li key={statement}>{statement}</li>
                      ))}
                  </ul>
                ) : (
                  <p>未设置额外约束。</p>
                )}
              </div>

              <footer>
                <span>
                  {selected.model_id} · 执行 {selected.attempt_count} 次
                </span>
                {selected.is_current ? (
                  <button onClick={onOpenWorkbench} type="button">
                    打开当前工作稿 →
                  </button>
                ) : selected.can_adopt ? (
                  <button
                    disabled={adopting}
                    onClick={() => onRequestAdopt(selected)}
                    type="button"
                  >
                    {adopting ? "正在采用…" : "采用为当前工作稿 →"}
                  </button>
                ) : (
                  <small>
                    {selected.is_current_brief
                      ? "已进入采用历史"
                      : "Brief 已更新，不可采用"}
                  </small>
                )}
              </footer>
            </article>
          ) : null}
        </div>
      ) : (
        <div className={styles.candidateArchiveEmpty}>
          <b>尚无候选草稿</b>
          <p>冻结 Brief 后可多次生成；每次结果都会独立保留在这里。</p>
        </div>
      )}
    </section>
  );
}
