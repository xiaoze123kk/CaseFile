"use client";

import "@xyflow/react/dist/style.css";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  DocumentHeader,
  StatusBadge,
} from "@/components/prototype-ui";
import {
  getPendingReasoningChanges,
  getReasoningOverviewMetrics,
  getReasoningSource,
  type ReasoningPath,
  type ReasoningProposalChange,
  type ReasoningSourceObject,
} from "@/lib/reasoning-prototype";
import type { DraftEvent } from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import { ReasoningCanvas } from "./reasoning-canvas";
import motionStyles from "./reasoning-motion.module.css";
import styles from "./reasoning-lab.module.css";
import sourceStyles from "./reasoning-source-preview.module.css";

const pathKindLabels: Record<ReasoningPath["kind"], string> = {
  primary: "主路径",
  alternative: "备选路径",
  excluded: "已排除",
};

const reasoningTypeLabels: Record<ReasoningPath["reasoningType"], string> = {
  abductive: "溯因",
  deductive: "演绎",
  inductive: "归纳",
  mixed: "混合",
};

const sourceTypeLabels: Record<ReasoningSourceObject["type"], string> = {
  brief: "Brief",
  event: "事件",
  information: "信息",
  phase: "叙事阶段",
  constraint: "约束",
};

const generationTracePath =
  "M30 170 L78 134 L138 159 L186 123 L207 151";
const generationTraceStart = 8;
const generationTraceEnd = 96;

const generationStages = [
  {
    threshold: generationTraceStart,
    label: "固定 Draft Revision 与对象清单",
    point: { x: 30, y: 170 },
  },
  {
    threshold: 32,
    label: "建立整卷对象与引用索引",
    point: { x: 78, y: 134 },
  },
  {
    threshold: 58,
    label: "识别核心问题与竞争路径",
    point: { x: 138, y: 159 },
  },
  {
    threshold: 82,
    label: "检查来源、反证与待求证缺口",
    point: { x: 186, y: 123 },
  },
  {
    threshold: generationTraceEnd,
    label: "投影节点坐标与来源包",
    point: { x: 207, y: 151 },
  },
];

function statusLabel(status: ReturnType<typeof usePrototype>["state"]["reasoning"]["status"]) {
  if (status === "running") return "生成中";
  if (status === "review") return "候选待审";
  if (status === "ready") return "已写入本地 Draft";
  if (status === "stale") return "基线已过期";
  if (status === "failed") return "生成失败";
  if (status === "cancelled") return "已取消";
  return "尚未生成";
}

function proposalBelongsToPath(
  proposal: ReasoningProposalChange,
  pathId: string,
  state: ReturnType<typeof usePrototype>["state"]["reasoning"],
) {
  const node = state.nodes.find((item) => item.id === proposal.targetId);
  const edge = state.edges.find((item) => item.id === proposal.targetId);
  return node?.pathId === pathId || edge?.pathId === pathId;
}

function SourceInspectorDetail({
  activePath,
  onClose,
  onOpenInWorkbench,
  returnLabel,
  source,
  sourceEvent,
}: {
  activePath: ReasoningPath;
  onClose: () => void;
  onOpenInWorkbench: (sourceId: string) => void;
  returnLabel: string;
  source: ReasoningSourceObject;
  sourceEvent?: DraftEvent;
}) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  const titleId = `reasoning-source-detail-${source.id}`;

  useEffect(() => {
    titleRef.current?.focus();
  }, [source.id]);

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  return (
    <section
      aria-labelledby={titleId}
      className={sourceStyles.inspectorPreview}
      id="reasoning-source-detail"
    >
      <header className={sourceStyles.previewHeader}>
        <div>
          <span>来源详情</span>
          <b>SOURCE QUICKLOOK</b>
        </div>
        <button onClick={onClose} type="button">
          ← {returnLabel}
        </button>
      </header>

      <div className={sourceStyles.previewBody}>
        <div className={sourceStyles.previewLead}>
          <div>
            <span>{sourceTypeLabels[source.type]}</span>
            <b>{source.id}</b>
          </div>
          <h3 id={titleId} ref={titleRef} tabIndex={-1}>
            {source.label}
          </h3>
          <p>{source.meta}</p>
        </div>

        <dl className={sourceStyles.previewFacts}>
          <div>
            <dt>当前路径</dt>
            <dd>{activePath.code}</dd>
          </div>
          <div>
            <dt>来源类型</dt>
            <dd>{sourceTypeLabels[source.type]}</dd>
          </div>
          <div>
            <dt>关联锚点</dt>
            <dd>{source.targetEventId ?? "无事件锚点"}</dd>
          </div>
        </dl>

        {sourceEvent ? (
          <section className={sourceStyles.eventAnchor}>
            <span>关联事件 · {sourceEvent.id}</span>
            <h4>
              {sourceEvent.time} · {sourceEvent.title}
            </h4>
            <p>{sourceEvent.description}</p>
            <dl>
              <div>
                <dt>地点</dt>
                <dd>{sourceEvent.location}</dd>
              </div>
              <div>
                <dt>阶段</dt>
                <dd>{sourceEvent.phase}</dd>
              </div>
              <div>
                <dt>参与者</dt>
                <dd>{sourceEvent.participants}</dd>
              </div>
              <div>
                <dt>可见范围</dt>
                <dd>{sourceEvent.visibility}</dd>
              </div>
            </dl>
          </section>
        ) : source.targetEventId ? (
          <div className={sourceStyles.standaloneNote}>
            关联事件 {source.targetEventId} 未找到，暂时无法在工作台定位。
          </div>
        ) : (
          <div className={sourceStyles.standaloneNote}>
            这是独立的 {sourceTypeLabels[source.type]}
            来源，可直接在推理实验室核对，不需要切换页面。
          </div>
        )}
      </div>

      <footer className={sourceStyles.previewFooter}>
        <span>查看不会修改 Draft；需要编辑正文时再前往工作台。</span>
        {source.targetEventId && sourceEvent ? (
          <button
            onClick={() => onOpenInWorkbench(source.id)}
            type="button"
          >
            在工作台定位 ↗
          </button>
        ) : (
          <b>{source.targetEventId ? "锚点不可用" : "无需跳转"}</b>
        )}
      </footer>
    </section>
  );
}

export function ReasoningLab() {
  const router = useRouter();
  const { state, dispatch, ready } = usePrototype();
  const [previewedSourceId, setPreviewedSourceId] = useState<string | null>(
    null,
  );
  const sourcePreviewTriggerRef = useRef<HTMLButtonElement | null>(null);
  const sourcePreviewTriggerKeyRef = useRef<string | null>(null);
  const reasoning = state.reasoning;
  const metrics = useMemo(
    () => getReasoningOverviewMetrics(reasoning),
    [reasoning],
  );
  const pendingChanges = getPendingReasoningChanges(reasoning);
  const activePath = reasoning.paths.find(
    (path) => path.id === reasoning.activePathId,
  );
  const selectedNode = reasoning.nodes.find(
    (node) => node.id === reasoning.selectedNodeId,
  );
  const selectedProposal =
    reasoning.proposals.find(
      (proposal) => proposal.id === reasoning.selectedProposalId,
    ) ?? pendingChanges[0];
  const previewedSource = previewedSourceId
    ? getReasoningSource(previewedSourceId)
    : undefined;

  useEffect(() => {
    if (reasoning.status !== "running") return;

    const next =
      reasoning.progress < 32
        ? {
            progress: 32,
            stage: "建立整卷对象与引用索引",
            delay: 620,
          }
        : reasoning.progress < 58
          ? {
              progress: 58,
              stage: "识别核心问题与竞争路径",
              delay: 760,
            }
          : reasoning.progress < 82
            ? {
                progress: 82,
                stage: "检查来源、反证与待求证缺口",
                delay: 840,
              }
            : reasoning.progress < 96
              ? {
                  progress: 96,
                  stage: "投影节点坐标与来源包",
                  delay: 720,
                }
              : null;

    const timer = window.setTimeout(() => {
      if (next) {
        dispatch({
          type: "update-reasoning-run",
          progress: next.progress,
          stage: next.stage,
        });
      } else {
        dispatch({ type: "complete-reasoning-run" });
      }
    }, next?.delay ?? 620);

    return () => window.clearTimeout(timer);
  }, [dispatch, reasoning.progress, reasoning.status]);

  const previewSource = useCallback((
    sourceId: string,
    trigger?: HTMLButtonElement,
  ) => {
    if (!getReasoningSource(sourceId)) return;
    const sourceTrigger =
      trigger ??
      (document.activeElement instanceof HTMLButtonElement
        ? document.activeElement
        : undefined);
    sourcePreviewTriggerRef.current = sourceTrigger ?? null;
    sourcePreviewTriggerKeyRef.current =
      sourceTrigger?.dataset.sourceTriggerKey ?? null;
    setPreviewedSourceId(sourceId);
  }, []);

  const closeSourcePreview = useCallback(() => {
    const previousTrigger = sourcePreviewTriggerRef.current;
    const previousTriggerKey = sourcePreviewTriggerKeyRef.current;
    setPreviewedSourceId(null);
    window.requestAnimationFrame(() => {
      if (previousTrigger?.isConnected) {
        previousTrigger.focus();
        return;
      }

      if (!previousTriggerKey) return;
      document
        .querySelector<HTMLButtonElement>(
          `[data-source-trigger-key="${CSS.escape(previousTriggerKey)}"]`,
        )
        ?.focus();
    });
  }, []);

  const openSourceInWorkbench = useCallback((sourceId: string) => {
    const source = getReasoningSource(sourceId);
    if (
      !source?.targetEventId ||
      !state.draft.events.some((event) => event.id === source.targetEventId)
    ) {
      return;
    }
    dispatch({ type: "select-event", id: source.targetEventId });
    router.push(`/workbench#event=${encodeURIComponent(source.targetEventId)}`);
  }, [dispatch, router, state.draft.events]);

  function openPath(pathId: string) {
    setPreviewedSourceId(null);
    dispatch({ type: "select-reasoning-path", id: pathId });
  }

  if (!ready) {
    return (
      <main className={`document ${styles.loading}`} aria-live="polite">
        <span>CASEFILE / REASONING LAB</span>
        <strong>正在展开推理索引…</strong>
      </main>
    );
  }

  const headerAction =
    reasoning.status === "running" ? (
      <button
        className={styles.headerButton}
        onClick={() => dispatch({ type: "cancel-reasoning-run" })}
        type="button"
      >
        取消任务
      </button>
    ) : reasoning.status === "idle" ? null : (
      <button
        className={styles.headerButton}
        onClick={() => {
          setPreviewedSourceId(null);
          dispatch({ type: "start-reasoning-run" });
        }}
        type="button"
      >
        重新生成整卷图
      </button>
    );

  return (
    <main className={`document ${styles.reasoningDocument}`}>
      <DocumentHeader
        action={headerAction}
        eyebrow="AUTHOR VIEW / REASONING LAB"
        meta={[
          { label: "DRAFT", value: `REV.${state.draft.revision}` },
          { label: "GRAPH", value: statusLabel(reasoning.status) },
          {
            label: "BASE",
            value:
              reasoning.status === "idle"
                ? "NOT SET"
                : `REV.${reasoning.baseRevision}`,
            tone: reasoning.status === "stale" ? "critical" : "default",
          },
        ]}
        title="推理实验室"
      />

      {reasoning.status === "idle" ||
      reasoning.status === "cancelled" ||
      reasoning.status === "failed" ? (
        <ReasoningEmptyState />
      ) : reasoning.status === "running" ? (
        <ReasoningRunningState />
      ) : reasoning.view === "overview" ? (
        <ReasoningOverview
          metrics={metrics}
          onOpenPath={openPath}
          pendingCount={pendingChanges.length}
        />
      ) : (
        <section className={`${styles.pathWorkspace} ${motionStyles.pathWorkspace}`}>
          <PathNavigator
            activePathId={activePath?.id ?? ""}
            onOpenOverview={() => {
              setPreviewedSourceId(null);
              dispatch({ type: "open-reasoning-overview" });
            }}
            onOpenPath={openPath}
            paths={reasoning.paths}
          />
          <ReasoningCanvas
            key={activePath?.id}
            onClearSourcePreview={() => setPreviewedSourceId(null)}
            onPreviewSource={previewSource}
            previewedSourceId={previewedSource?.id}
          />
          <ReasoningInspector
            activePath={activePath}
            onClearSourcePreview={() => setPreviewedSourceId(null)}
            onCloseSourcePreview={closeSourcePreview}
            onOpenSourceInWorkbench={openSourceInWorkbench}
            onPreviewSource={previewSource}
            previewedSource={previewedSource}
            selectedNode={selectedNode}
            selectedProposal={selectedProposal}
          />
        </section>
      )}
    </main>
  );
}

function ReasoningEmptyState() {
  const { state, dispatch } = usePrototype();
  const reasoning = state.reasoning;
  const isFailure = reasoning.status === "failed";
  const isCancelled = reasoning.status === "cancelled";

  return (
    <section className={styles.emptyStage}>
      <div className={styles.emptyHero}>
        <div
          className={`${styles.orbitMark} ${motionStyles.idleMark}`}
          aria-hidden="true"
        >
          <i />
          <i />
          <i />
          <b>RL</b>
        </div>
        <span className={styles.eyebrow}>MANUAL GENERATION ONLY</span>
        <h2>把整个 CaseFile 展开成一张可审计的推理星图</h2>
        <p>
          Agent 只在你明确触发后读取当前 Draft。它会同时给出主路径、备选路径、
          已排除解释和待求证缺口；所有结果先进入候选态。
        </p>

        {isFailure || isCancelled ? (
          <div
            className={`${styles.emptyNotice} ${
              isFailure ? styles.emptyNoticeCritical : ""
            }`}
            role="status"
          >
            <b>{isFailure ? "RUN FAILED" : "RUN CANCELLED"}</b>
            <span>
              {isFailure
                ? reasoning.failureMessage
                : "任务由用户取消，Draft 与候选图均未发生变化。"}
            </span>
          </div>
        ) : null}

        <div className={styles.modePicker} role="radiogroup" aria-label="生成模式">
          <button
            aria-checked={reasoning.mode === "organize"}
            className={reasoning.mode === "organize" ? styles.modeActive : ""}
            onClick={() =>
              dispatch({ type: "set-reasoning-mode", mode: "organize" })
            }
            role="radio"
            type="button"
          >
            <span>01</span>
            <strong>整理现有结构</strong>
            <small>只组织已有对象和关系，不提出新的假设正文。</small>
          </button>
          <button
            aria-checked={reasoning.mode === "explore"}
            className={reasoning.mode === "explore" ? styles.modeActive : ""}
            onClick={() =>
              dispatch({ type: "set-reasoning-mode", mode: "explore" })
            }
            role="radio"
            type="button"
          >
            <span>02</span>
            <strong>探索推理</strong>
            <small>允许提出 AI 候选、反证关系和待求证缺口。</small>
          </button>
        </div>

        <button
          className={`${styles.generateButton} ${motionStyles.generateButton}`}
          onClick={() => dispatch({ type: "start-reasoning-run" })}
          type="button"
        >
          <span>
            <small>FULL DRAFT / REV.{state.draft.revision}</small>
            生成整卷推理图
          </span>
          <b>↗</b>
        </button>
      </div>

      <aside className={styles.manifestPanel}>
        <header>
          <span>本次预计读取</span>
          <b>INPUT MANIFEST</b>
        </header>
        {[
          ["Brief 与约束", "05"],
          ["人物与地点", "08"],
          ["事件与阶段", String(state.draft.events.length + 6).padStart(2, "0")],
          ["信息与证据", "17"],
          ["主张与假设", "08"],
        ].map(([label, value]) => (
          <div className={styles.manifestRow} key={label}>
            <span>{label}</span>
            <b>{value}</b>
          </div>
        ))}
        <footer>
          <b>明确边界</b>
          <p>本地 Fixture 模拟 Agent；不会请求 API，也不会直接修改 Draft。</p>
        </footer>
      </aside>
    </section>
  );
}

function ReasoningRunningState() {
  const { state, dispatch } = usePrototype();
  const reasoning = state.reasoning;
  const traceProgress = Math.min(
    100,
    Math.max(
      0,
      ((reasoning.progress - generationTraceStart) /
        (generationTraceEnd - generationTraceStart)) *
        100,
    ),
  );

  return (
    <section className={styles.runningStage} aria-live="polite">
      <div
        aria-label="推理图生成进度"
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={reasoning.progress}
        aria-valuetext={`${reasoning.progress}% · ${reasoning.stage}`}
        className={`${styles.runningPulse} ${motionStyles.runningPulse}`}
        role="progressbar"
      >
        <i />
        <i />
        <svg
          aria-hidden="true"
          className={motionStyles.networkTrace}
          focusable="false"
          viewBox="0 0 240 220"
        >
          <path
            className={motionStyles.networkGuide}
            d={generationTracePath}
            pathLength="100"
          />
          <path
            className={motionStyles.networkProgress}
            d={generationTracePath}
            pathLength="100"
            style={{ strokeDashoffset: 100 - traceProgress }}
          />
          {generationStages.map((stage) => (
            <circle
              className={`${motionStyles.tracePoint} ${
                reasoning.progress >= stage.threshold
                  ? motionStyles.tracePointActive
                  : ""
              }`}
              cx={stage.point.x}
              cy={stage.point.y}
              key={stage.threshold}
              r="5"
            />
          ))}
        </svg>
        <b>{reasoning.progress}%</b>
      </div>
      <div className={styles.runningContent}>
        <span className={styles.eyebrow}>
          RLG-{String(reasoning.runSequence).padStart(4, "0")} / BACKGROUND TASK
        </span>
        <h2>Agent 正在组织整卷推理结构</h2>
        <p>{reasoning.stage}</p>

        <div className={styles.progressTrack}>
          <i style={{ width: `${reasoning.progress}%` }} />
        </div>

        <ol className={styles.stageList}>
          {generationStages.map((stage) => {
            const complete = reasoning.progress > stage.threshold;
            const active =
              reasoning.progress >= stage.threshold &&
              reasoning.progress <
                (generationStages[
                  generationStages.indexOf(stage) + 1
                ]?.threshold ?? 101);
            return (
              <li
                className={
                  complete
                    ? styles.stageComplete
                    : active
                      ? styles.stageActive
                      : ""
                }
                key={stage.threshold}
              >
                <b>{complete ? "✓" : stage.threshold}</b>
                <span>{stage.label}</span>
              </li>
            );
          })}
        </ol>

        <div className={styles.runningMeta}>
          <span>BASE REV.{reasoning.baseRevision}</span>
          <span>
            MODE · {reasoning.mode === "explore" ? "探索推理" : "整理现有"}
          </span>
          <span>LOCAL FIXTURE</span>
        </div>

        <button
          className={styles.cancelButton}
          onClick={() => dispatch({ type: "cancel-reasoning-run" })}
          type="button"
        >
          取消本次生成
        </button>
      </div>
    </section>
  );
}

function ReasoningOverview({
  metrics,
  pendingCount,
  onOpenPath,
}: {
  metrics: ReturnType<typeof getReasoningOverviewMetrics>;
  pendingCount: number;
  onOpenPath: (pathId: string) => void;
}) {
  const { state } = usePrototype();
  const reasoning = state.reasoning;

  return (
    <section className={`${styles.overviewStage} ${motionStyles.overviewStage}`}>
      {reasoning.status === "stale" ? (
        <div className={styles.staleBanner} role="status">
          <b>REVISION GUARD</b>
          <span>{reasoning.stage}</span>
          <small>当前图仍可查看，但需要重新生成后才能批准候选。</small>
        </div>
      ) : (
        <div className={styles.overviewLead}>
          <div>
            <span className={styles.eyebrow}>
              FULL DOSSIER / {reasoning.mode.toUpperCase()}
            </span>
            <h2>整卷推理总览</h2>
            <p>
              Agent 将 CaseFile 归为 {metrics.paths} 条竞争路径。
              主路径保持最高来源覆盖，备选路径保留缺口，排除路径展示反证原因。
            </p>
          </div>
          <div className={styles.reviewStamp}>
            <span>{reasoning.status === "review" ? "REVIEW" : "READY"}</span>
            <b>{pendingCount}</b>
            <small>待审候选</small>
          </div>
        </div>
      )}

      <div className={styles.metricStrip}>
        {[
          ["核心问题", metrics.questions, "QUESTIONS"],
          ["推理路径", metrics.paths, "PATHS"],
          ["待求证", metrics.gaps, "OPEN GAPS"],
          ["约束冲突", metrics.conflicts, "CONFLICTS"],
          ["来源覆盖", `${metrics.sourceCoverage}%`, "COVERAGE"],
        ].map(([label, value, code]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
            <small>{code}</small>
          </div>
        ))}
      </div>

      <div className={styles.overviewGrid}>
        <div className={styles.pathCards}>
          {reasoning.paths.map((path, index) => (
            <button
              className={`${styles.pathCard} ${
                styles[`pathCard_${path.kind}`]
              } ${motionStyles.pathCard}`}
              key={path.id}
              onClick={() => onOpenPath(path.id)}
              style={{ animationDelay: `${90 + index * 80}ms` }}
              type="button"
            >
              <header>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <b>{pathKindLabels[path.kind]}</b>
                  <small>{path.code}</small>
                </div>
                <strong>{Math.round(path.confidence * 100)}%</strong>
              </header>
              <h3>{path.title}</h3>
              <p>{path.question}</p>
              <footer>
                <span>
                  {reasoningTypeLabels[path.reasoningType]} ·{" "}
                  {path.nodeIds.length} 节点
                </span>
                <b>{path.gapCount ? `${path.gapCount} 缺口` : "来源完整"} ↗</b>
              </footer>
            </button>
          ))}
        </div>

        <aside className={styles.crossPathPanel}>
          <header>
            <span>跨路径发现</span>
            <b>CROSS-PATH INDEX</b>
          </header>
          <div className={styles.crossPathItem}>
            <b>INFO-2107</b>
            <strong>第五人权限记录被 2 条路径共用</strong>
            <small>身份归属 · 信息泄露</small>
          </div>
          <div className={`${styles.crossPathItem} ${styles.crossPathCritical}`}>
            <b>CONFLICT</b>
            <strong>阶段 03 的获得事件仍然缺失</strong>
            <small>硬约束影响谜底公平性</small>
          </div>
          <div className={styles.crossPathItem}>
            <b>UNROUTED · 02</b>
            <strong>两个来源对象尚未进入主路径</strong>
            <small>可在单路径画布中人工关联</small>
          </div>
          <footer>
            <StatusBadge tone={reasoning.status === "review" ? "warning" : "dark"}>
              {reasoning.status === "review"
                ? "候选等待人工决策"
                : `已写入 REV.${reasoning.outcomeRevision ?? state.draft.revision}`}
            </StatusBadge>
          </footer>
        </aside>
      </div>
    </section>
  );
}

function PathNavigator({
  paths,
  activePathId,
  onOpenOverview,
  onOpenPath,
}: {
  paths: ReasoningPath[];
  activePathId: string;
  onOpenOverview: () => void;
  onOpenPath: (pathId: string) => void;
}) {
  return (
    <aside className={styles.pathNavigator}>
      <header>
        <button
          onClick={onOpenOverview}
          type="button"
        >
          ← 总览
        </button>
        <span>PATH NAVIGATOR</span>
      </header>
      <nav aria-label="推理路径">
        {paths.map((path) => (
          <button
            aria-current={path.id === activePathId ? "page" : undefined}
            className={path.id === activePathId ? styles.pathNavActive : ""}
            key={path.id}
            onClick={() => onOpenPath(path.id)}
            type="button"
          >
            <span>{path.code}</span>
            <strong>{path.title}</strong>
            <small>
              {pathKindLabels[path.kind]} · {Math.round(path.confidence * 100)}%
            </small>
          </button>
        ))}
      </nav>
      <footer>
        <b>图例</b>
        <span><i className={styles.legendConfirmed} /> 已确认</span>
        <span><i className={styles.legendCandidate} /> AI 候选</span>
        <span><i className={styles.legendExcluded} /> 已排除</span>
        <span><i className={styles.legendGap} /> 待求证</span>
      </footer>
    </aside>
  );
}

function ReasoningInspector({
  activePath,
  onClearSourcePreview,
  onCloseSourcePreview,
  onOpenSourceInWorkbench,
  onPreviewSource,
  previewedSource,
  selectedNode,
  selectedProposal,
}: {
  activePath?: ReasoningPath;
  onClearSourcePreview: () => void;
  onCloseSourcePreview: () => void;
  onOpenSourceInWorkbench: (sourceId: string) => void;
  onPreviewSource: (
    sourceId: string,
    trigger?: HTMLButtonElement,
  ) => void;
  previewedSource?: ReasoningSourceObject;
  selectedNode: ReturnType<typeof usePrototype>["state"]["reasoning"]["nodes"][number] | undefined;
  selectedProposal?: ReasoningProposalChange;
}) {
  const { state, dispatch } = usePrototype();
  const reasoning = state.reasoning;
  const pathProposals = activePath
    ? reasoning.proposals.filter((proposal) =>
        proposalBelongsToPath(proposal, activePath.id, reasoning),
      )
    : [];
  const pending = pathProposals.filter(
    (proposal) => proposal.status === "pending",
  );
  const contextualProposal =
    pathProposals.find((proposal) => proposal.id === selectedProposal?.id) ??
    pathProposals[0];
  const previewedSourceEvent = previewedSource?.targetEventId
    ? state.draft.events.find(
        (event) => event.id === previewedSource.targetEventId,
      )
    : undefined;
  const selectedCount = reasoning.proposals.filter(
    (proposal) => proposal.status === "pending" && proposal.selected,
  ).length;

  if (reasoning.status === "review") {
    return (
      <aside className={styles.inspectorPanel}>
        <header className={styles.inspectorHeader}>
          <div>
            <span>候选审阅</span>
            <b>{pending.length} ITEMS IN PATH</b>
          </div>
          <button
            onClick={() =>
              dispatch({
                type: "select-all-reasoning-proposals",
                selected: selectedCount !== getPendingReasoningChanges(reasoning).length,
              })
            }
            type="button"
          >
            {selectedCount ? "切换全选" : "全部选择"}
          </button>
        </header>

        <div className={styles.reviewInspectorBody}>
          <div className={styles.proposalList}>
            {pathProposals.map((proposal) => (
              <button
                className={
                  proposal.id === contextualProposal?.id
                    ? styles.proposalActive
                    : ""
                }
                key={proposal.id}
                onClick={() => {
                  onClearSourcePreview();
                  dispatch({
                    type: "select-reasoning-proposal",
                    id: proposal.id,
                  });
                  if (proposal.targetType === "node") {
                    dispatch({
                      type: "select-reasoning-node",
                      id: proposal.targetId,
                    });
                  }
                }}
                type="button"
              >
                <i
                  aria-label={proposal.selected ? "已选择" : "未选择"}
                  aria-checked={proposal.selected}
                  className={proposal.selected ? styles.proposalChecked : ""}
                  onClick={(event) => {
                    event.stopPropagation();
                    dispatch({
                      type: "toggle-reasoning-proposal",
                      id: proposal.id,
                    });
                  }}
                  role="checkbox"
                />
                <span>
                  <b>{proposal.label}</b>
                  <small>{proposal.description}</small>
                </span>
                <strong>
                  {proposal.confidence
                    ? `${Math.round(proposal.confidence * 100)}%`
                    : "—"}
                </strong>
              </button>
            ))}
            {pathProposals.length === 0 ? (
              <p className={styles.noProposal}>本路径没有待审候选。</p>
            ) : null}
          </div>

          {previewedSource && activePath ? (
            <SourceInspectorDetail
              activePath={activePath}
              key={previewedSource.id}
              onClose={onCloseSourcePreview}
              onOpenInWorkbench={onOpenSourceInWorkbench}
              returnLabel={
                pathProposals.length > 0 ? "返回候选" : "返回检查器"
              }
              source={previewedSource}
              sourceEvent={previewedSourceEvent}
            />
          ) : contextualProposal ? (
            <div className={styles.proposalDetail}>
              <span className={styles.eyebrow}>STRUCTURED RATIONALE</span>
              <h3>{contextualProposal.label}</h3>
              <p>{contextualProposal.rationale}</p>
              <div className={styles.sourceChips}>
                {contextualProposal.sourceIds.map((sourceId) => {
                  const source = getReasoningSource(sourceId);
                  return (
                    <button
                      aria-controls={source ? "reasoning-source-detail" : undefined}
                      data-source-trigger-key={`proposal:${contextualProposal.id}:${sourceId}`}
                      disabled={!source}
                      key={sourceId}
                      onClick={(event) =>
                        onPreviewSource(sourceId, event.currentTarget)
                      }
                      type="button"
                    >
                      {sourceId}
                      {source ? " →" : ""}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className={styles.noSelection}>
              <b>NO PATH CANDIDATE</b>
              <p>此路径没有候选变更，仍可从画布查看来源。</p>
            </div>
          )}

          {contextualProposal ? (
            <div className={styles.proposalActions}>
              <button
                disabled={contextualProposal.status !== "pending"}
                onClick={() =>
                  dispatch({
                    type: "reject-reasoning-proposal",
                    id: contextualProposal.id,
                  })
                }
                type="button"
              >
                拒绝当前
              </button>
              <button
                disabled={contextualProposal.status !== "pending"}
                onClick={() =>
                  dispatch({
                    type: "toggle-reasoning-proposal",
                    id: contextualProposal.id,
                  })
                }
                type="button"
              >
                {contextualProposal.selected ? "取消选择" : "加入批准"}
              </button>
            </div>
          ) : null}
        </div>

        <footer className={styles.approvalFooter}>
          <div>
            <span>本次批准</span>
            <strong>{selectedCount} 项</strong>
          </div>
          <button
            disabled={selectedCount === 0}
            onClick={() => dispatch({ type: "apply-reasoning-proposals" })}
            type="button"
          >
            批准所选并写入 REV.{state.draft.revision + 1}
          </button>
        </footer>
      </aside>
    );
  }

  return (
    <aside className={styles.inspectorPanel}>
      <header className={styles.inspectorHeader}>
        <div>
          <span>{previewedSource ? "来源检查器" : "节点检查器"}</span>
          <b>{previewedSource ? "SOURCE INSPECTOR" : "OBJECT INSPECTOR"}</b>
        </div>
        <StatusBadge tone={reasoning.status === "stale" ? "red" : "dark"}>
          {statusLabel(reasoning.status)}
        </StatusBadge>
      </header>

      <div className={styles.nodeInspectorBody}>
        {reasoning.status === "stale" ? (
          <div className={styles.inspectorStale}>
            <b>REVISION MISMATCH</b>
            <p>{reasoning.stage}</p>
            <button
              onClick={() => dispatch({ type: "start-reasoning-run" })}
              type="button"
            >
              基于当前 Draft 重新生成
            </button>
          </div>
        ) : null}

        {previewedSource && activePath ? (
          <SourceInspectorDetail
            activePath={activePath}
            key={previewedSource.id}
            onClose={onCloseSourcePreview}
            onOpenInWorkbench={onOpenSourceInWorkbench}
            returnLabel="返回节点"
            source={previewedSource}
            sourceEvent={previewedSourceEvent}
          />
        ) : selectedNode ? (
          <div className={styles.nodeInspector}>
            <span className={styles.eyebrow}>{selectedNode.kind}</span>
            <label>
              <span>节点标题</span>
              <input
                defaultValue={selectedNode.label}
                disabled={
                  reasoning.status !== "ready" || !selectedNode.userEditable
                }
                key={selectedNode.id}
                onBlur={(event) =>
                  dispatch({
                    type: "rename-reasoning-node",
                    id: selectedNode.id,
                    label: event.target.value,
                  })
                }
              />
            </label>
            <p>{selectedNode.statement}</p>
            <dl>
              <div>
                <dt>状态</dt>
                <dd>{selectedNode.status}</dd>
              </div>
              <div>
                <dt>置信度</dt>
                <dd>
                  {selectedNode.confidence === undefined
                    ? "—"
                    : `${Math.round(selectedNode.confidence * 100)}%`}
                </dd>
              </div>
              <div>
                <dt>来源</dt>
                <dd>{selectedNode.sourceIds.length}</dd>
              </div>
            </dl>
            <div className={styles.inspectorSources}>
              <b>来源清单</b>
              {selectedNode.sourceIds.map((sourceId) => {
                const source = getReasoningSource(sourceId);
                return (
                  <button
                    aria-controls={source ? "reasoning-source-detail" : undefined}
                    data-source-trigger-key={`node:${selectedNode.id}:${sourceId}`}
                    disabled={!source}
                    key={sourceId}
                    onClick={(event) =>
                      onPreviewSource(sourceId, event.currentTarget)
                    }
                    type="button"
                  >
                    <span>
                      <b>{sourceId}</b>
                      <small>{source?.label ?? "未知来源"}</small>
                    </span>
                    <i>{source ? "快速查看 →" : "未解析"}</i>
                  </button>
                );
              })}
            </div>
          </div>
        ) : (
          <div className={styles.noSelection}>
            <b>SELECT A NODE</b>
            <p>选择画布节点以查看来源、置信度和可编辑字段。</p>
          </div>
        )}
      </div>

      <footer className={styles.inspectorFootnote}>
        证据与事件正文仍由 CaseFile 工作台维护；此处只编辑推理语义。
      </footer>
    </aside>
  );
}
