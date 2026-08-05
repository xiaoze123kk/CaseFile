"use client";

import { useRouter } from "next/navigation";
import { type CSSProperties, useMemo, useState } from "react";

import { useDemoPrototype } from "@/features/demo-prototype/demo-prototype-provider";

import styles from "./prototype-late-stages.module.css";

const generationStages = [
  "解析冻结简报",
  "并行生成三份候选",
  "校验对象与引用",
] as const;

const statusLabels = {
  pending: "待采用",
  current: "当前工作稿",
  stale: "旧简报",
} as const;

export function DraftCandidatesStage() {
  const router = useRouter();
  const {
    state,
    generateCandidates,
    previewCandidate,
    adoptCandidate,
    beginBriefRevision,
    candidateStatus,
  } = useDemoPrototype();
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(
    null,
  );
  const [notice, setNotice] = useState(
    "候选由真实 Agent 生成，预览工作台为本地样例。",
  );

  const currentCandidates = useMemo(
    () =>
      state.draftCandidates.filter(
        (candidate) => candidate.briefVersion === state.frozenBriefVersion,
      ),
    [state.draftCandidates, state.frozenBriefVersion],
  );
  const olderCandidates = useMemo(
    () =>
      state.draftCandidates.filter(
        (candidate) => candidate.briefVersion !== state.frozenBriefVersion,
      ),
    [state.draftCandidates, state.frozenBriefVersion],
  );
  const generated = currentCandidates.length > 0;
  const generating = state.generation.status === "generating";
  const readyToGenerate = state.frozenBriefVersion !== null;

  function openWorkbench(candidateId: string) {
    previewCandidate(candidateId);
    router.push("/demo");
  }

  function adopt(candidateId: string) {
    if (!adoptCandidate(candidateId)) {
      setNotice("旧简报候选只能预览，不能替换当前工作稿。 ");
      return;
    }
    setNotice("候选已采用为当前工作稿；工作台默认打开这一版。 ");
  }

  function renderCandidate(candidate: (typeof state.draftCandidates)[number], index: number) {
    const status = candidateStatus(candidate);
    const expanded = expandedCandidateId === candidate.id;
    const detailId = `prototype-draft-${candidate.id}`;
    return (
      <article
        className={styles.candidateCard}
        data-focus={candidate.focus}
        data-status={status}
        key={candidate.id}
        style={{ "--candidate-order": index } as CSSProperties}
      >
        <button
          aria-controls={detailId}
          aria-expanded={expanded}
          className={styles.candidateSummary}
          onClick={() => setExpandedCandidateId(expanded ? null : candidate.id)}
          type="button"
        >
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div>
            <small>{candidate.focusLabel} · 简报 V{String(candidate.briefVersion).padStart(2, "0")}</small>
            <strong>{candidate.title}</strong>
            <p>{candidate.summary}</p>
          </div>
          <em>{statusLabels[status]}</em>
        </button>
        {expanded ? (
          <div className={styles.candidateDetail} id={detailId}>
            <section>
              <span>核心推理命题</span>
              <p>{candidate.reasoningQuestion}</p>
            </section>
            <dl>
              <div><dt>实体</dt><dd>{candidate.objectCounts.entities}</dd></div>
              <div><dt>事件</dt><dd>{candidate.objectCounts.events}</dd></div>
              <div><dt>信息</dt><dd>{candidate.objectCounts.information_units}</dd></div>
              <div><dt>推理链</dt><dd>{candidate.objectCounts.reasoning_paths}</dd></div>
            </dl>
            <div className={styles.candidateComparison}>
              <section>
                <span>这一版擅长</span>
                <ul>{candidate.strengths.map((item) => <li key={item}>{item}</li>)}</ul>
              </section>
              <section>
                <span>需要接受</span>
                <ul>{candidate.tradeoffs.map((item) => <li key={item}>{item}</li>)}</ul>
              </section>
            </div>
            <section className={styles.constraintExcerpt}>
              <span>创作约束摘录</span>
              {candidate.constraintStatements.length ? (
                <ul>{candidate.constraintStatements.slice(0, 3).map((item) => <li key={item}>{item}</li>)}</ul>
              ) : <p>当前简报没有额外约束。</p>}
            </section>
            <footer>
              <small>真实 Agent 生成 · 已完成结构与引用校验</small>
              <div>
                <button onClick={() => openWorkbench(candidate.id)} type="button">预览工作台</button>
                <button
                  data-primary="true"
                  disabled={status === "stale" || status === "current"}
                  onClick={() => adopt(candidate.id)}
                  type="button"
                >
                  {status === "stale"
                    ? "旧简报候选不可采用"
                    : status === "current"
                      ? "已是当前工作稿"
                      : "采用为当前工作稿 →"}
                </button>
              </div>
            </footer>
          </div>
        ) : null}
      </article>
    );
  }

  return (
    <section className={styles.candidatesStage} aria-labelledby="candidates-stage-title">
      <header className={styles.stageHeader}>
        <div>
          <span>创作简报 → 候选草稿 → 当前工作稿</span>
          <h1 id="candidates-stage-title">让三种创作策略同时摊开。</h1>
        </div>
        <dl>
          <div>
            <dt>冻结版本</dt>
            <dd>
              {readyToGenerate
                ? `V${String(state.frozenBriefVersion).padStart(2, "0")}`
                : `待冻结 · V${String(state.workingBriefVersion).padStart(2, "0")}`}
            </dd>
          </div>
          <div><dt>当前工作稿</dt><dd>{state.adoptedCandidateId ? "已采用" : "尚未采用"}</dd></div>
        </dl>
      </header>

      <section className={styles.generationDesk} aria-label="候选生成控制">
        <div className={styles.generationCopy}>
          <span>真实 Agent 生成</span>
          <strong>
            {generating
              ? "正在形成候选…"
              : generated
                ? "三份候选已归档"
                : readyToGenerate
                  ? "冻结简报已经就绪"
                  : "新版本等待重新审阅与冻结"}
          </strong>
          <p>三份结果由真实 Agent 任务生成，并完成结构与引用校验。</p>
        </div>
        <ol className={styles.generationProgress}>
          {generationStages.map((stage, index) => {
            const stageNo = index + 1;
            const complete = generated || state.generation.stage > stageNo;
            const active = generating && state.generation.stage === stageNo;
            return (
              <li data-active={active} data-complete={complete} key={stage}>
                <b>{complete ? "✓" : stageNo}</b>
                <span>{stage}</span>
              </li>
            );
          })}
        </ol>
        <button
          className={styles.generateButton}
          disabled={generated || generating || !readyToGenerate}
          onClick={() => {
            void generateCandidates()
              .then((ok) => {
                if (ok) {
                  setNotice("三份候选已生成并完成引用校验，可以预览或显式采用。");
                } else {
                  setNotice("当前简报尚未满足生成条件。 ");
                }
              })
              .catch((caught) => {
                setNotice(
                  caught instanceof Error
                    ? caught.message
                    : "候选生成未完成，请检查模型服务后重试。",
                );
              });
          }}
          type="button"
        >
          <span>{generating ? "生成中…" : generated ? "三份候选已生成" : "生成三份候选"}</span>
          <b>{generated ? "✓" : "→"}</b>
        </button>
      </section>

      {generated ? (
        <section className={styles.candidateArchive} aria-label="当前简报候选稿">
          <header>
            <div><span>候选决策卷</span><strong>当前简报的三联稿</strong></div>
            <b>{String(currentCandidates.length).padStart(2, "0")} 份</b>
          </header>
          <div className={styles.candidateFan}>
            {currentCandidates.map(renderCandidate)}
          </div>
        </section>
      ) : (
        <section className={styles.candidateEmpty}>
          <span>候选卷尚空</span>
          <h2>
            {readyToGenerate
              ? "点击生成后，三张工作稿会在这里同时展开。"
              : "先完成新版本审阅；旧候选仍留在下方卷宗。"}
          </h2>
          <p>
            {readyToGenerate
              ? "每一张都基于同一份冻结简报，由独立任务生成。"
              : "当前工作稿继续有效，直到你冻结并采用新版本候选。"}
          </p>
        </section>
      )}

      {olderCandidates.length ? (
        <details className={styles.oldCandidates}>
          <summary>旧简报候选 <b>{olderCandidates.length}</b></summary>
          <div>{olderCandidates.map((candidate, index) => renderCandidate(candidate, currentCandidates.length + index))}</div>
        </details>
      ) : null}

      <footer className={styles.candidatesFooter}>
        <p aria-live="polite">{notice}</p>
        <button disabled={generating} onClick={beginBriefRevision} type="button">建立简报修订</button>
      </footer>
    </section>
  );
}

