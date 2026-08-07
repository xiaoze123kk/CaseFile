"use client";

import { useRouter } from "next/navigation";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";

import {
  type CandidateSlotStrategy,
  useCaseSession,
} from "@/features/case-session/case-session-provider";

import styles from "./intake-late-stages.module.css";

const strategyLabels: Record<CandidateSlotStrategy, string> = {
  structure_first: "结构优先",
  atmosphere_first: "氛围优先",
  reasoning_first: "推理优先",
};

const statusLabels = {
  pending: "待采用",
  current: "当前工作稿",
  stale: "旧简报",
} as const;

export function DraftCandidatesStage() {
  const router = useRouter();
  const {
    state,
    activeProjectId,
    analyzeStrategies,
    selectStrategy,
    generateCandidates,
    adoptCandidate,
    beginBriefRevision,
    candidateStatus,
  } = useCaseSession();
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(null);
  const [notice, setNotice] = useState("先选择策略，再生成一份完整深稿。");
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [adoptingCandidateId, setAdoptingCandidateId] = useState<string | null>(null);
  const analysisStartedRef = useRef(false);
  const adoptionInFlightRef = useRef(false);

  const currentCandidates = useMemo(
    () => state.draftCandidates.filter(
      (candidate) => candidate.briefVersion === state.frozenBriefVersion,
    ),
    [state.draftCandidates, state.frozenBriefVersion],
  );
  const olderCandidates = useMemo(
    () => state.draftCandidates.filter(
      (candidate) => candidate.briefVersion !== state.frozenBriefVersion,
    ),
    [state.draftCandidates, state.frozenBriefVersion],
  );
  const selectedCandidate = state.selectedStrategy
    ? currentCandidates.find(
        (candidate) => candidate.focus === strategyFocus(state.selectedStrategy!),
      ) ?? null
    : null;
  const generating = state.generation.status === "generating";
  const analysis = state.strategyAnalysis;
  const ready = state.frozenBriefVersion !== null;

  useEffect(() => {
    if (!ready || analysis.status !== "idle" || analysisStartedRef.current) return;
    analysisStartedRef.current = true;
    void analyzeStrategies().catch(() => {
      analysisStartedRef.current = false;
    });
  }, [analysis.status, analyzeStrategies, ready]);

  async function regenerateStrategyAnalysis() {
    setGenerationError(null);
    try {
      await analyzeStrategies(true);
      setNotice("策略已经依据当前冻结 Brief 重新分析。");
    } catch (error) {
      setGenerationError(error instanceof Error ? error.message : "策略分析失败");
    }
  }

  async function generateSelectedDraft() {
    if (!state.selectedStrategy) return;
    setGenerationError(null);
    try {
      const completed = await generateCandidates(state.selectedStrategy);
      setNotice(
        completed
          ? `${strategyLabels[state.selectedStrategy]}完整深稿已通过结构与引用校验。`
          : "生成任务未完成，请查看当前槽位并重试。",
      );
    } catch (error) {
      setGenerationError(error instanceof Error ? error.message : "完整深稿生成失败");
    }
  }

  async function adopt(candidateId: string) {
    if (adoptionInFlightRef.current || activeProjectId === null) return;
    adoptionInFlightRef.current = true;
    setAdoptingCandidateId(candidateId);
    try {
      if (!(await adoptCandidate(candidateId))) {
        setGenerationError("这份深稿不属于当前冻结 Brief，请重新生成。");
        return;
      }
      router.push(`/workbench?project=${activeProjectId}`);
    } catch (error) {
      setGenerationError(error instanceof Error ? error.message : "采用深稿失败");
    } finally {
      adoptionInFlightRef.current = false;
      setAdoptingCandidateId(null);
    }
  }

  function renderCandidate(
    candidate: (typeof state.draftCandidates)[number],
    index: number,
  ) {
    const status = candidateStatus(candidate);
    const expanded = expandedCandidateId === candidate.id;
    return (
      <article
        className={styles.candidateCard}
        data-focus={candidate.focus}
        data-status={status}
        key={candidate.id}
        style={{ "--candidate-order": index } as CSSProperties}
      >
        <button
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
          <div className={styles.candidateDetail}>
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
            <footer>
              <small>真实 Agent 生成 · 已完成完整 Contract 校验</small>
              {status === "current" ? (
                <button onClick={() => router.push(`/workbench?project=${activeProjectId}`)} type="button">
                  打开分析师工作台 →
                </button>
              ) : (
                <button
                  aria-busy={adoptingCandidateId === candidate.id}
                  data-primary="true"
                  disabled={status === "stale" || adoptingCandidateId !== null}
                  onClick={() => void adopt(candidate.id)}
                  type="button"
                >
                  {adoptingCandidateId === candidate.id ? "正在采用…" : "采用为当前工作稿 →"}
                </button>
              )}
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
          <span>创作简报 → 策略选择 → 完整工作稿</span>
          <h1 id="candidates-stage-title">先选定创作策略，再生成一份完整深稿。</h1>
        </div>
        <dl>
          <div><dt>冻结版本</dt><dd>V{String(state.frozenBriefVersion ?? state.workingBriefVersion).padStart(2, "0")}</dd></div>
          <div><dt>当前工作稿</dt><dd>{state.adoptedCandidateId ? "已采用" : "尚未采用"}</dd></div>
        </dl>
      </header>

      <section className={styles.generationDesk} aria-label="创作策略选择">
        <div className={styles.generationCopy}>
          <span>Brief 定制分析</span>
          <strong>
            {analysis.status === "analyzing"
              ? "正在分析三种创作方向…"
              : analysis.status === "ready"
                ? "三种方向已就绪，请由你选择。"
                : "等待策略分析"}
          </strong>
          <p>这里只比较方向、收益与代价，不提前生成三份昂贵的完整稿。</p>
        </div>

        {analysis.status === "ready" ? (
          <div className={styles.candidateFan}>
            {analysis.options.map((option, index) => {
              const selected = state.selectedStrategy === option.strategy;
              const recommended = analysis.recommendedStrategy === option.strategy;
              return (
                <article
                  className={styles.candidateCard}
                  data-focus={strategyFocus(option.strategy)}
                  data-status={selected ? "current" : "pending"}
                  key={option.strategy}
                  style={{ "--candidate-order": index } as CSSProperties}
                >
                  <button
                    aria-pressed={selected}
                    className={styles.candidateSummary}
                    onClick={() => selectStrategy(option.strategy)}
                    type="button"
                  >
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <small>{recommended ? "Agent 建议 · " : ""}{strategyLabels[option.strategy]}</small>
                      <strong>{option.focus}</strong>
                      <p>{option.direction}</p>
                    </div>
                    <em>{selected ? "已选择" : "选择"}</em>
                  </button>
                  <div className={styles.candidateDetail}>
                    <section><span>适配依据</span><p>{option.brief_fit}</p></section>
                    <div className={styles.candidateComparison}>
                      <section><span>主要收益</span><ul>{option.strengths.map((item) => <li key={item}>{item}</li>)}</ul></section>
                      <section><span>需要接受</span><ul>{option.tradeoffs.map((item) => <li key={item}>{item}</li>)}</ul></section>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className={styles.candidateEmpty} aria-busy={analysis.status === "analyzing"}>
            <span>{analysis.status === "failed" ? "策略分析未完成" : "正在读取冻结 Brief"}</span>
            <p>{analysis.error ?? "Agent 将给出三个针对本案的方向，不会替你作出选择。"}</p>
          </div>
        )}

        {analysis.recommendationReason ? (
          <p className={styles.generationCurrentAction}>
            Agent 建议：{strategyLabels[analysis.recommendedStrategy!]}。{analysis.recommendationReason}
          </p>
        ) : null}

        <button
          className={styles.generateButton}
          disabled={!state.selectedStrategy || generating || Boolean(selectedCandidate)}
          onClick={() => void generateSelectedDraft()}
          type="button"
        >
          <span>
            {generating
              ? `正在生成${strategyLabels[state.selectedStrategy!]}完整深稿…`
              : selectedCandidate
                ? "这一策略的完整深稿已生成"
                : state.selectedStrategy
                  ? `生成${strategyLabels[state.selectedStrategy]}完整深稿`
                  : "请先选择一个策略"}
          </span>
          <b>{selectedCandidate ? "✓" : "→"}</b>
        </button>
        <button disabled={analysis.status === "analyzing" || generating} onClick={() => void regenerateStrategyAnalysis()} type="button">
          重新分析三种策略
        </button>
      </section>

      {generationError ? <p className={styles.generationError} role="alert">{generationError}</p> : null}

      {currentCandidates.length ? (
        <section className={styles.candidateArchive} aria-label="当前简报完整深稿">
          <header><div><span>完整深稿</span><strong>已生成的策略方向</strong></div><b>{currentCandidates.length}</b></header>
          <div className={styles.candidateFan}>{currentCandidates.map(renderCandidate)}</div>
        </section>
      ) : null}

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

function strategyFocus(strategy: CandidateSlotStrategy) {
  if (strategy === "structure_first") return "structure";
  if (strategy === "atmosphere_first") return "atmosphere";
  return "reasoning";
}
