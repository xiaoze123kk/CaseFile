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
    resumeGeneration,
    cancelGeneration,
    previewCandidate,
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
  const selectedSlot = state.selectedStrategy
    ? state.generation.slots[state.selectedStrategy]
    : null;
  const componentSteps = selectedSlot?.latestTask?.component_steps ?? [];
  const latestComponentSteps = new Map(
    [...componentSteps]
      .sort((left, right) => left.step_run_id - right.step_run_id)
      .map((step) => [step.component_id, step]),
  );
  const failedComponent = [...latestComponentSteps.values()].find(
    (step) => step.status === "failed",
  );
  const adoptedCandidate = state.draftCandidates.find(
    (candidate) => candidate.id === state.adoptedCandidateId,
  ) ?? null;
  const cancelling = selectedSlot?.latestTask?.status === "cancelling";

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
      const outcome = await generateCandidates(state.selectedStrategy);
      if (outcome === "succeeded") {
        setNotice(`${strategyLabels[state.selectedStrategy]}完整深稿已通过结构与引用校验。`);
      } else if (outcome === "cancelled") {
        setNotice("本次生成已安全停止，Current Draft 未被修改。");
      } else {
        setNotice("生成任务未启动，请检查当前槽位后重试。");
      }
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
        setGenerationError("这份深稿不属于当前冻结的创作简报，请重新生成。");
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

  function openPreview(candidate: (typeof state.draftCandidates)[number]) {
    if (activeProjectId === null) return;
    const taskRunId = candidate.candidateState?.taskRunId
      ?? Number(candidate.id.replace(/^draft-/, ""));
    if (!Number.isSafeInteger(taskRunId) || taskRunId < 1) {
      setGenerationError("这份候选缺少可恢复的任务标识，请刷新后重试。");
      return;
    }
    previewCandidate(candidate.id);
    router.push(`/workbench?project=${activeProjectId}&preview=${taskRunId}`);
  }

  async function resumeFailedDraft() {
    if (!state.selectedStrategy) return;
    setGenerationError(null);
    try {
      await resumeGeneration(state.selectedStrategy);
      setNotice("已从失败阶段恢复，输入与上游哈希一致的成功部件已复用。");
    } catch (error) {
      setGenerationError(error instanceof Error ? error.message : "恢复失败");
    }
  }

  async function cancelSelectedDraft() {
    if (!state.selectedStrategy) return;
    setGenerationError(null);
    try {
      const task = await cancelGeneration(state.selectedStrategy);
      if (!task) return;
      if (task.status === "cancelling") {
        setNotice("已请求安全停止；Worker 会结束当前步骤，Current Draft 不会改变。");
      } else if (task.status === "cancelled") {
        setNotice("本次生成已安全停止，Current Draft 未被修改。");
      } else if (task.status === "succeeded") {
        setNotice("任务已在停止请求到达前完成，候选列表已刷新。");
      } else if (task.status === "failed") {
        setGenerationError(
          `任务已在停止请求到达前失败：${task.failure?.message ?? "请查看失败详情后重试。"}`,
        );
      } else {
        setGenerationError(`停止请求返回任务状态“${task.status}”，请刷新后确认。`);
      }
    } catch (error) {
      setGenerationError(error instanceof Error ? error.message : "停止任务失败");
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
              <small
                data-testid={`candidate-completed-at-${candidate.candidateState?.taskRunId ?? candidate.id}`}
              >
                真实 Agent 生成 · 已完成完整 Contract 校验 ·{" "}
                {formatCandidateCompletedAt(candidate.candidateState?.completedAt)}
              </small>
              <div>
                {status === "current" ? (
                  <button onClick={() => router.push(`/workbench?project=${activeProjectId}`)} type="button">
                    打开分析师工作台 →
                  </button>
                ) : (
                  <>
                    <button onClick={() => openPreview(candidate)} type="button">
                      预览工作台
                    </button>
                    <button
                      aria-busy={adoptingCandidateId === candidate.id}
                      data-primary="true"
                      disabled={status === "stale" || adoptingCandidateId !== null}
                      onClick={() => void adopt(candidate.id)}
                      type="button"
                    >
                      {adoptingCandidateId === candidate.id
                        ? "正在采用…"
                        : status === "stale"
                          ? "历史候选不可采用"
                          : "采用为当前工作稿 →"}
                    </button>
                  </>
                )}
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
          <span>创作简报 → 策略选择 → 完整工作稿</span>
          <h1 id="candidates-stage-title">先选定创作策略，再生成一份完整深稿。</h1>
        </div>
        <dl>
          <div><dt>冻结版本</dt><dd>V{String(state.frozenBriefVersion ?? state.workingBriefVersion).padStart(2, "0")}</dd></div>
          <div><dt>当前工作稿</dt><dd>{state.adoptedCandidateId ? "已采用" : "尚未采用"}</dd></div>
        </dl>
      </header>

      {adoptedCandidate ? (
        <section className={styles.handoffStrip} aria-label="当前工作稿交接">
          <div>
            <span>Current Draft · CF-{activeProjectId ?? "—"} / TR-{adoptedCandidate.candidateState?.taskRunId ?? "—"}</span>
            <strong>{adoptedCandidate.title}</strong>
            <p>冻结 Brief V{String(adoptedCandidate.briefVersion).padStart(2, "0")} · 已由作者明确采用 · 可继续验证、溯源与编辑</p>
          </div>
          <b>ADOPTED</b>
          <button onClick={() => router.push(`/workbench?project=${activeProjectId}`)} type="button">
            进入分析师工作台 →
          </button>
        </section>
      ) : null}

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
          <div className={styles.strategyFan} aria-label="三种策略并列比较">
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
            <span>{analysis.status === "failed" ? "策略分析未完成" : "正在读取冻结的创作简报"}</span>
            <p>{analysis.error ?? "Agent 将给出三个针对本案的方向，不会替你作出选择。"}</p>
          </div>
        )}

        {analysis.recommendationReason ? (
          <p className={styles.generationCurrentAction}>
            Agent 建议：{strategyLabels[analysis.recommendedStrategy!]}。{analysis.recommendationReason}
          </p>
        ) : null}

        <div className={styles.strategyActions}>
          <button
            className={styles.generateButton}
            disabled={!state.selectedStrategy || generating || Boolean(selectedCandidate)}
            onClick={() => void generateSelectedDraft()}
            type="button"
          >
            <span>
              {generating
                ? cancelling
                  ? "正在安全停止生成…"
                  : `正在生成${strategyLabels[state.selectedStrategy!]}完整深稿…`
                : selectedCandidate
                  ? "完整深稿已生成"
                  : state.selectedStrategy
                    ? `生成${strategyLabels[state.selectedStrategy]}完整深稿`
                    : "请先选择一个策略"}
            </span>
            <b>{selectedCandidate ? "✓" : "→"}</b>
          </button>
          <button
            className={styles.strategyRefresh}
            disabled={analysis.status === "analyzing" || generating}
            onClick={() => void regenerateStrategyAnalysis()}
            type="button"
          >
            重新分析三种策略
          </button>
          {generating ? (
            <button
              className={styles.strategyRefresh}
              data-danger="true"
              disabled={!selectedSlot?.taskRunId || cancelling}
              onClick={() => void cancelSelectedDraft()}
              type="button"
            >
              {cancelling ? "正在停止…" : "停止本次生成"}
            </button>
          ) : null}
        </div>
      </section>

      {generationError ? <p className={styles.generationError} role="alert">{generationError}</p> : null}

      {selectedSlot?.latestTask && componentSteps.length ? (
        <section className={styles.agentPipeline} aria-label="深稿生成部件进度">
          <header>
            <div><span>{selectedSlot.latestTask.prompt_version ?? "brief_to_draft"}</span><strong>六步生成流水线</strong></div>
            <b>Attempt {selectedSlot.latestTask.attempt_count}</b>
          </header>
          <ol>
            {pipelineRows(latestComponentSteps).map((row, index) => (
              <li data-status={row.status} key={row.id}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <strong>{row.label}</strong>
                  <small>{stepStatusLabel(row.status)}</small>
                  {row.children ? (
                    <ul>{row.children.map((child) => (
                      <li data-status={child.status} key={child.id}>
                        {child.label} · {stepStatusLabel(child.status)}
                      </li>
                    ))}</ul>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
          {failedComponent ? (
            <div className={styles.componentFailure} role="alert">
              <strong>{componentLabel(failedComponent.component_id)}执行失败</strong>
              <p>层：{failedComponent.failure_layer ?? "未知"} · Schema：{failedComponent.schema_id}</p>
              {failedComponent.issues.map((issue) => (
                <code key={`${issue.code}-${issue.path}`}>{issue.path || "/"} · {issue.message}</code>
              ))}
              <button
                disabled={!failedComponent.recoverable || generating}
                onClick={() => void resumeFailedDraft()}
                type="button"
              >
                {failedComponent.recoverable ? "从失败阶段恢复" : "当前失败不可恢复"}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

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

export function formatCandidateCompletedAt(value: string | null | undefined) {
  if (!value) return "完成时间待同步";
  const completedAt = new Date(value);
  if (Number.isNaN(completedAt.getTime())) return "完成时间待同步";
  return `完成于 ${new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(completedAt)}`;
}

type PipelineStatus = "pending" | "running" | "succeeded" | "failed" | "reused" | "skipped";

function pipelineRows(steps: Map<string, { status: PipelineStatus }>) {
  const direct = (id: string) => steps.get(id)?.status ?? "pending";
  const domains = [
    { id: "story_world", label: "故事世界", status: direct("story_world") },
    { id: "evidence_logic", label: "证据推理", status: direct("evidence_logic") },
    { id: "resolution_governance", label: "解答治理", status: direct("resolution_governance") },
  ];
  const domainStatus: PipelineStatus = domains.some((item) => item.status === "failed")
    ? "failed"
    : domains.every((item) => item.status === "succeeded" || item.status === "reused")
      ? domains.some((item) => item.status === "reused") ? "reused" : "succeeded"
      : domains.some((item) => item.status === "running") ? "running" : "pending";
  return [
    { id: "context_pack_builder", label: "上下文包构建", status: direct("context_pack_builder") },
    { id: "case_blueprint_planner", label: "案件蓝图规划", status: direct("case_blueprint_planner") },
    { id: "domain_drafters", label: "三域创作", status: domainStatus, children: domains },
    { id: "reference_linker", label: "引用链接", status: direct("reference_linker") },
    { id: "casefile_compiler", label: "CaseFile 编译", status: direct("casefile_compiler") },
    { id: "quality_repair_gate", label: "质量与修复门禁", status: direct("quality_repair_gate") },
  ];
}

function stepStatusLabel(status: PipelineStatus) {
  return { pending: "等待", running: "执行中", succeeded: "已完成", failed: "失败", reused: "已复用", skipped: "已跳过" }[status];
}

function componentLabel(componentId: string) {
  const domains: Record<string, string> = {
    story_world: "故事世界",
    evidence_logic: "证据推理",
    resolution_governance: "解答治理",
  };
  return pipelineRows(new Map()).find((row) => row.id === componentId)?.label
    ?? domains[componentId]
    ?? componentId;
}
