"use client";

import { useRouter } from "next/navigation";
import { type CSSProperties, useMemo, useRef, useState } from "react";

import {
  type CandidateTaskStage,
  type CandidateSlotStrategy,
  useCaseSession,
} from "@/features/case-session/case-session-provider";

import styles from "./intake-late-stages.module.css";

const generationStages = [
  "解析冻结简报",
  "生成三份策略候选",
  "校验对象与引用",
] as const;

const strategyLabels = {
  structure_first: "结构优先",
  atmosphere_first: "氛围优先",
  reasoning_first: "推理优先",
} as const;

const slotStageLabels: Record<CandidateTaskStage, string> = {
  queued: "已排队，等待 Agent 接手",
  planning: "正在规划对象结构",
  processing: "正在处理",
  generating: "正在生成候选内容",
  validating: "正在校验对象与引用",
  completed: "已完成结构与引用校验",
  failed: "生成失败，可重试",
};

const macroStageForTaskStage: Record<CandidateTaskStage, number> = {
  queued: 1,
  planning: 1,
  processing: 2,
  generating: 2,
  validating: 3,
  completed: 3,
  failed: 2,
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
    generateCandidates,
    adoptCandidate,
    beginBriefRevision,
    candidateStatus,
  } = useCaseSession();
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(
    null,
  );
  const [notice, setNotice] = useState(
    "候选由真实 Agent 生成；采用后将在分析师工作台打开真实工作稿。",
  );
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [adoptingCandidateId, setAdoptingCandidateId] = useState<string | null>(
    null,
  );
  const [adoptionErrors, setAdoptionErrors] = useState<Record<string, string>>(
    {},
  );
  const adoptionInFlightRef = useRef(false);

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
  const generatedStrategyCount = Object.values(state.generation.slots).filter(
    (slot) => slot.status === "succeeded",
  ).length;
  const generated = generatedStrategyCount === 3;
  const hasCandidates = currentCandidates.length > 0;
  const generating = state.generation.status === "generating";
  const readyToGenerate = state.frozenBriefVersion !== null;
  const activeGenerationStage = useMemo(() => {
    if (generated) return 3;
    if (!generating) return 0;
    return Math.max(
      1,
      ...Object.values(state.generation.slots)
        .filter((slot) => slot.status === "running")
        .map((slot) => macroStageForTaskStage[slot.stage]),
    );
  }, [generated, generating, state.generation.slots]);
  const currentGenerationAction = useMemo(() => {
    const runningSlots = Object.entries(state.generation.slots).filter(
      ([, slot]) => slot.status === "running",
    );
    if (runningSlots.length > 0) {
      const [strategy, slot] = runningSlots[0];
      const label = strategyLabels[strategy as CandidateSlotStrategy];
      return runningSlots.length === 1
        ? `${label}候选：${slotStageLabels[slot.stage]}`
        : `${runningSlots.length} 份候选并行处理中 · ${slotStageLabels[slot.stage]}`;
    }
    const failedCount = Object.values(state.generation.slots).filter(
      (slot) => slot.status === "failed",
    ).length;
    if (failedCount > 0) return `${failedCount} 份候选生成失败，可重试`;
    if (generated) return "三份候选已完成结构与引用校验";
    return readyToGenerate ? "等待开始生成" : "等待冻结简报";
  }, [generated, readyToGenerate, state.generation.slots]);

  function openWorkbench() {
    if (activeProjectId === null) {
      setNotice("当前会话尚未建案，无法打开分析师工作台。");
      return;
    }
    router.push(`/workbench?project=${activeProjectId}`);
  }

  async function adopt(candidateId: string) {
    if (adoptionInFlightRef.current) return;
    if (activeProjectId === null) {
      setAdoptionErrors((current) => ({
        ...current,
        [candidateId]: "当前会话尚未建案，无法采用候选。",
      }));
      return;
    }

    adoptionInFlightRef.current = true;
    setAdoptingCandidateId(candidateId);
    setAdoptionErrors((current) => {
      const next = { ...current };
      delete next[candidateId];
      return next;
    });
    try {
      const adopted = await adoptCandidate(candidateId);
      if (!adopted) {
        setAdoptionErrors((current) => ({
          ...current,
          [candidateId]: "这份候选已不属于当前冻结简报，请重新生成后再采用。",
        }));
        return;
      }
      setNotice("候选已采用为当前工作稿，正在打开分析师工作台。");
      router.push(`/workbench?project=${activeProjectId}`);
    } catch (caught) {
      const detail =
        caught instanceof Error ? caught.message : "服务端未能采用这份候选。";
      setAdoptionErrors((current) => ({
        ...current,
        [candidateId]: `${detail} 当前候选未被修改，可以重试。`,
      }));
    } finally {
      adoptionInFlightRef.current = false;
      setAdoptingCandidateId(null);
    }
  }

  function startCandidateGeneration() {
    setGenerationError(null);
    void generateCandidates()
      .then((ok) => {
        if (ok) {
          setNotice("三份策略候选已生成并完成引用校验，请明确采用其中一份。");
        } else {
          setNotice("部分策略候选已保留；点击生成按钮只会补齐失败或缺失槽位。");
        }
      })
      .catch((caught) => {
        setGenerationError(
          caught instanceof Error
            ? caught.message
            : "候选生成未完成，请检查模型服务后重试。",
        );
      });
  }

  function retryCandidate(strategy: keyof typeof strategyLabels) {
    setGenerationError(null);
    const attempt = state.generation.slots[strategy].attempt;
    void generateCandidates(strategy, attempt).catch((caught) => {
      setGenerationError(
        caught instanceof Error ? caught.message : "候选重试未完成，请稍后再试。",
      );
    });
  }

  function renderCandidate(candidate: (typeof state.draftCandidates)[number], index: number) {
    const status = candidateStatus(candidate);
    const expanded = expandedCandidateId === candidate.id;
    const detailId = `candidate-draft-${candidate.id}`;
    return (
      <article
        aria-busy={adoptingCandidateId === candidate.id}
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
                {status === "current" ? (
                  <button
                    data-primary="true"
                    disabled={adoptingCandidateId !== null}
                    onClick={openWorkbench}
                    type="button"
                  >
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
                    {status === "stale"
                      ? "旧简报候选不可采用"
                      : adoptingCandidateId === candidate.id
                        ? "正在采用…"
                        : "采用为当前工作稿 →"}
                  </button>
                )}
              </div>
              {adoptionErrors[candidate.id] ? (
                <p className={styles.candidateAdoptionError} role="alert">
                  <b>采用未完成</b>
                  <span>{adoptionErrors[candidate.id]}</span>
                </p>
              ) : null}
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
          <h1 id="candidates-stage-title">生成三份策略候选，展开比较。</h1>
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
                ? "三份策略候选已归档"
                : readyToGenerate
                  ? "冻结简报已经就绪"
                  : "新版本等待重新审阅与冻结"}
          </strong>
          <p>三份结果由真实 Agent 任务生成，并完成结构与引用校验。</p>
        </div>
        <ol className={styles.generationProgress}>
          {generationStages.map((stage, index) => {
            const stageNo = index + 1;
            const complete = generated || activeGenerationStage > stageNo;
            const active = generating && activeGenerationStage === stageNo;
            return (
              <li data-active={active} data-complete={complete} key={stage}>
                <b>{complete ? "✓" : stageNo}</b>
                <span>{stage}</span>
              </li>
            );
          })}
        </ol>
        <div aria-live="polite" className={styles.generationSlots}>
          <div className={styles.generationMeterHeader}>
            <strong>{generatedStrategyCount}/3</strong>
            <span>份候选已完成</span>
          </div>
          <div
            aria-label="候选生成进度"
            aria-valuemax={3}
            aria-valuemin={0}
            aria-valuenow={generatedStrategyCount}
            aria-valuetext={`${generatedStrategyCount}/3 份候选已完成`}
            className={styles.generationMeter}
            role="progressbar"
          >
            <span
              style={
                {
                  "--generation-progress": `${(generatedStrategyCount / 3) * 100}%`,
                } as CSSProperties
              }
            />
          </div>
          <p className={styles.generationCurrentAction}>{currentGenerationAction}</p>
          <ul className={styles.generationSlotList}>
            {Object.entries(state.generation.slots).map(([strategy, slot]) => {
              const strategyKey = strategy as CandidateSlotStrategy;
              const slotLabel =
                slot.status === "pending" ? "待生成" : slotStageLabels[slot.stage];
              return (
                <li data-slot-stage={slot.stage} data-slot-status={slot.status} key={strategy}>
                  <div className={styles.generationSlotHeading}>
                    <strong>{strategyLabels[strategyKey]}</strong>
                    <span>{slotLabel}</span>
                  </div>
                  {slot.attempt > 1 ? (
                    <small>第 {slot.attempt} 次尝试</small>
                  ) : null}
                  {slot.status === "failed" && slot.attempt < 2 ? (
                    <button
                      onClick={() => retryCandidate(strategyKey)}
                      type="button"
                    >
                      重试
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
        <button
          className={styles.generateButton}
          disabled={generated || generating || !readyToGenerate}
          onClick={startCandidateGeneration}
          type="button"
        >
          <span>
            {generating
              ? "正在补齐策略候选…"
              : generated
                ? "三份策略候选已生成"
                : hasCandidates
                  ? "补齐失败槽位"
                  : "生成三份策略候选"}
          </span>
          <b>{generated ? "✓" : "→"}</b>
        </button>
      </section>

      {generationError ? (
        <p className={styles.generationError} role="alert">
          <b>生成未完成</b>
          <span>{generationError}</span>
        </p>
      ) : null}

      {hasCandidates ? (
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
        <section aria-busy={generating} className={styles.candidateEmpty}>
          <span>候选卷尚空</span>
          <h2>
            {readyToGenerate
                ? "点击生成后，三张策略工作稿会在这里展开。"
              : "先完成新版本审阅；旧候选仍留在下方卷宗。"}
          </h2>
          <p>
            {readyToGenerate
                ? "每一张都基于同一份冻结简报，由独立任务生成；采用后会进入真实分析师工作台。"
              : "当前工作稿继续有效，直到你冻结并采用新版本候选。"}
          </p>
          {readyToGenerate ? (
            <button
              className={styles.emptyGenerateButton}
              disabled={generating}
              onClick={startCandidateGeneration}
              type="button"
            >
              <span>
                {generating ? "正在补齐策略候选…" : "生成三份策略候选"}
              </span>
              <b aria-hidden="true">→</b>
            </button>
          ) : null}
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
