"use client";

import type { DraftCandidateView } from "@/lib/api-client";

import styles from "./brief-workspace.module.css";

interface DraftCandidatePanelProps {
  candidates: DraftCandidateView[];
  selectedTaskRunId: number | null;
  adopting: boolean;
  onSelect: (taskRunId: number | null) => void;
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
  if (!candidate.is_current_brief) return "旧简报";
  if (candidate.is_adopted) return "历史采用";
  return "待采用";
}

function candidateSortGroup(candidate: DraftCandidateView) {
  if (candidate.is_current) return 0;
  if (candidate.can_adopt) return 1;
  if (candidate.is_current_brief && candidate.is_adopted) return 2;
  return 3;
}

function candidateCompletedAt(candidate: DraftCandidateView) {
  const timestamp = candidate.completed_at
    ? new Date(candidate.completed_at).getTime()
    : Number.NaN;
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

export function sortDraftCandidates(candidates: DraftCandidateView[]) {
  return [...candidates].sort((left, right) => {
    const groupDifference =
      candidateSortGroup(left) - candidateSortGroup(right);
    if (groupDifference !== 0) return groupDifference;
    const timeDifference =
      candidateCompletedAt(right) - candidateCompletedAt(left);
    return timeDifference || right.task_run_id - left.task_run_id;
  });
}

export function defaultDraftCandidateTaskRunId(
  candidates: DraftCandidateView[],
) {
  const ordered = sortDraftCandidates(candidates);
  return (
    ordered.find((candidate) => candidate.can_adopt)?.task_run_id ??
    ordered.find((candidate) => candidate.is_current)?.task_run_id ??
    ordered[0]?.task_run_id ??
    null
  );
}

export function nextDraftCandidateTaskRunId(
  candidates: DraftCandidateView[],
  selectedTaskRunId: number | null,
  observedTaskRunIds: ReadonlySet<number>,
) {
  if (!candidates.length) return null;
  const ordered = sortDraftCandidates(candidates);
  if (!observedTaskRunIds.size) {
    return defaultDraftCandidateTaskRunId(ordered);
  }
  const newCandidate = ordered.find(
    (candidate) =>
      candidate.can_adopt &&
      !observedTaskRunIds.has(candidate.task_run_id),
  );
  if (newCandidate) return newCandidate.task_run_id;
  if (
    selectedTaskRunId !== null &&
    !ordered.some(
      (candidate) => candidate.task_run_id === selectedTaskRunId,
    )
  ) {
    return defaultDraftCandidateTaskRunId(ordered);
  }
  return selectedTaskRunId;
}

export function DraftCandidatePanel({
  candidates,
  selectedTaskRunId,
  adopting,
  onSelect,
  onRequestAdopt,
  onOpenWorkbench,
}: DraftCandidatePanelProps) {
  const orderedCandidates = sortDraftCandidates(candidates);
  const pendingCount = candidates.filter(
    (candidate) => candidate.can_adopt,
  ).length;

  return (
    <section
      aria-label="候选草稿档案"
      className={styles.candidateArchive}
    >
      <header className={styles.candidateArchiveHead}>
        <div>
          <span>候选决策卷</span>
          <strong>候选卷签</strong>
        </div>
        <div className={styles.candidateArchiveTally}>
          {pendingCount ? <span>{pendingCount} 待采用</span> : null}
          <b>{String(candidates.length).padStart(2, "0")} 份</b>
        </div>
      </header>

      {candidates.length ? (
        <ol className={styles.candidateAccordion}>
          {orderedCandidates.map((candidate, index) => {
            const expanded =
              candidate.task_run_id === selectedTaskRunId;
            const detailId = `draft-candidate-${candidate.task_run_id}`;
            return (
              <li
                className={
                  expanded ? styles.candidateAccordionExpanded : undefined
                }
                data-state={
                  candidate.is_current
                    ? "current"
                    : candidate.can_adopt
                      ? "pending"
                      : candidate.is_current_brief
                        ? "history"
                        : "stale"
                }
                key={candidate.task_run_id}
              >
                <button
                  aria-controls={detailId}
                  aria-expanded={expanded}
                  className={styles.candidateSummary}
                  onClick={() =>
                    onSelect(expanded ? null : candidate.task_run_id)
                  }
                  type="button"
                >
                  <span className={styles.candidateOrdinal}>
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className={styles.candidateSummaryCopy}>
                    <strong>{candidate.title}</strong>
                    <small>
                      {candidate.model_id} ·{" "}
                      {completedTime(candidate.completed_at)}
                    </small>
                  </span>
                  <em data-state={candidateState(candidate)}>
                    {candidateState(candidate)}
                  </em>
                  <span
                    aria-hidden="true"
                    className={styles.candidateChevron}
                  >
                    ↓
                  </span>
                </button>

                <div
                  aria-hidden={!expanded}
                  className={styles.candidateDisclosure}
                  data-expanded={expanded}
                  id={detailId}
                  role="region"
                >
                  <div className={styles.candidateDisclosureInner}>
                    {expanded ? (
                      <article className={styles.candidatePreview}>
                      <div className={styles.candidateQuestion}>
                        <small>核心推理命题</small>
                        <p>
                          {candidate.reasoning_questions[0] ??
                            "该候选未声明核心推理命题。"}
                        </p>
                      </div>

                      <dl className={styles.candidateCounts}>
                        {countLabels.map(([key, label]) => (
                          <div key={key}>
                            <dt>{label}</dt>
                            <dd>{candidate.object_counts[key] ?? 0}</dd>
                          </div>
                        ))}
                      </dl>

                      <div className={styles.candidateConstraints}>
                        <small>创作约束摘录</small>
                        {candidate.constraint_statements.length ? (
                          <ul>
                            {candidate.constraint_statements
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
                          {candidate.provider} · 执行{" "}
                          {candidate.attempt_count} 次
                        </span>
                        {candidate.is_current ? (
                          <button onClick={onOpenWorkbench} type="button">
                            打开当前工作稿 →
                          </button>
                        ) : candidate.can_adopt ? (
                          <button
                            disabled={adopting}
                            onClick={() => onRequestAdopt(candidate)}
                            type="button"
                          >
                            {adopting
                              ? "正在采用…"
                              : "采用为当前工作稿 →"}
                          </button>
                        ) : (
                          <small>
                            {candidate.is_current_brief
                              ? "已进入采用历史"
                              : "简报已更新，不可采用"}
                          </small>
                        )}
                      </footer>
                      </article>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className={styles.candidateArchiveEmpty}>
          <b>尚无候选草稿</b>
          <p>冻结简报后可多次生成；每次结果都会独立保留在这里。</p>
        </div>
      )}
    </section>
  );
}
