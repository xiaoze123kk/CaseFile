import { useMemo, useState } from "react";

import {
  type ReasoningOutcome,
  type ReasoningPath,
  type WorkbenchReasoningAssessment,
  type WorkbenchReasoningGroup,
  type WorkbenchSeed,
} from "./analyst-fixture";
import styles from "./analyst-workbench.module.css";
import {
  WorkbenchCanvasKernel,
  type WorkbenchCanvasLegendItem,
  type WorkbenchCanvasSceneEdge,
  type WorkbenchCanvasSceneNode,
} from "./workbench-canvas-kernel";
import type { WorkbenchCanvasLayoutIdentity } from "./workbench-canvas-layout";
import { reasoningOutcomeLabels, reliabilityLabel } from "./workbench-presenters";
import type { WorkbenchConclusion } from "./workbench-real-data-types";

type ReasoningNodeKind = "evidence" | "reason" | "hypothesis" | "conclusion";

const reasoningNodeMeta: Record<
  ReasoningNodeKind,
  { accent: string; legend: string }
> = {
  evidence: { accent: "#c17c12", legend: "证据" },
  reason: { accent: "#277a83", legend: "推理步骤" },
  hypothesis: { accent: "#7f4a92", legend: "假设" },
  conclusion: { accent: "#a84b32", legend: "最终结论" },
};

const reasoningNodeLegend: WorkbenchCanvasLegendItem[] = (
  Object.entries(reasoningNodeMeta) as Array<
    [ReasoningNodeKind, (typeof reasoningNodeMeta)[ReasoningNodeKind]]
  >
).map(([id, meta]) => ({ id, label: meta.legend, accent: meta.accent }));

interface ReasoningCanvasNode {
  id: string;
  kind: ReasoningNodeKind;
  caption: string;
  label: string;
  outcome?: ReasoningOutcome | WorkbenchConclusion["reviewStatus"] | "missing";
  objectId?: string;
}

interface ReasoningCanvasEdge {
  id: string;
  source: string;
  target: string;
  kind: "evidence" | "chain" | "hypothesis" | "resolution" | ReasoningOutcome;
}

interface ReasoningCanvasScene {
  nodes: ReasoningCanvasNode[];
  edges: ReasoningCanvasEdge[];
}

type ReasoningMode = "graph" | "matrix";

interface MatrixSelection {
  group: WorkbenchReasoningGroup;
  hypothesis: WorkbenchReasoningGroup["hypotheses"][number];
  information: WorkbenchReasoningGroup["information"][number];
  assessment: WorkbenchReasoningAssessment | null;
}

const assessmentEffectLabels = {
  supports: "支持",
  contradicts: "冲突",
  neutral: "不区分",
  unassessed: "未评估",
} as const;

const assessmentStrengthLabels = {
  weak: "弱",
  moderate: "中",
  strong: "强",
} as const;

function conclusionStatusLabel(conclusion: WorkbenchConclusion | undefined) {
  if (!conclusion) return "尚未形成结论";
  return conclusion.reviewStatus === "confirmed" ? "作者已确认" : "待作者确认";
}

function conclusionOutcomeLabel(conclusion: WorkbenchConclusion | undefined) {
  if (!conclusion) return "无结论";
  return conclusion.outcome === "undetermined" ? "未定论" : "答案";
}

function conclusionValue(value: WorkbenchConclusion["values"][number]["value"]) {
  if (typeof value !== "object" || value === null) return String(value);
  const objectId = value.object_id;
  return typeof objectId === "string" ? objectId : JSON.stringify(value);
}

function hypothesisConclusionRole(
  conclusion: WorkbenchConclusion | undefined,
  hypothesisId: string,
) {
  if (!conclusion?.selectedHypothesisIds.includes(hypothesisId)) return null;
  return conclusion.outcome === "undetermined" ? "并存解释" : "进入当前结论";
}

function ConclusionBand({
  conclusion,
  question,
  onSelectResolution,
  onTransitionConclusion,
  busy = false,
}: {
  conclusion: WorkbenchConclusion | undefined;
  question: string;
  onSelectResolution: () => void;
  onTransitionConclusion?: (action: "confirm" | "withdraw") => void;
  busy?: boolean;
}) {
  return (
    <article
      className={styles.conclusionBand}
      data-outcome={conclusion?.outcome ?? "missing"}
      data-status={conclusion?.reviewStatus ?? "missing"}
    >
      <button
        aria-label={`查看核心问题：${question}`}
        className={styles.conclusionQuestion}
        onClick={onSelectResolution}
        type="button"
      >
        <span>核心问题</span>
        <strong>{question}</strong>
      </button>
      <div className={styles.conclusionDecision}>
        <div className={styles.conclusionStatusRow}>
          <span>最终结论</span>
          <b>{conclusionStatusLabel(conclusion)}</b>
          <i>{conclusionOutcomeLabel(conclusion)}</i>
        </div>
        <h3>{conclusion?.summary ?? "该核心问题尚未形成结论"}</h3>
        {conclusion ? (
          <>
            {conclusion.values.length ? (
              <dl className={styles.conclusionValues}>
                {conclusion.values.map((item) => (
                  <div key={item.slotId}>
                    <dt>{item.slotId}</dt>
                    <dd>{conclusionValue(item.value)}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            <p>{conclusion.rationale}</p>
            {conclusion.unresolvedGaps.length ? (
              <div className={styles.conclusionGaps}>
                <span>仍缺少</span>
                <ul>
                  {conclusion.unresolvedGaps.map((gap) => <li key={gap}>{gap}</li>)}
                </ul>
              </div>
            ) : null}
            {onTransitionConclusion ? (
              <button
                className={styles.conclusionAction}
                disabled={busy}
                onClick={() => onTransitionConclusion(
                  conclusion.reviewStatus === "confirmed" ? "withdraw" : "confirm",
                )}
                type="button"
              >
                {conclusion.reviewStatus === "confirmed" ? "撤回确认" : "确认最终结论"}
              </button>
            ) : null}
          </>
        ) : (
          <p>先完成推理路径和假设评估，再由作者确认答案或未定论。</p>
        )}
      </div>
    </article>
  );
}

function matrixSelectionFor(
  group: WorkbenchReasoningGroup,
  hypothesis: WorkbenchReasoningGroup["hypotheses"][number],
  information: WorkbenchReasoningGroup["information"][number],
): MatrixSelection {
  return {
    group,
    hypothesis,
    information,
    assessment:
      group.assessments.find(
        (assessment) =>
          assessment.hypothesisId === hypothesis.id &&
          assessment.informationId === information.id,
      ) ?? null,
  };
}

function ReasoningMatrix({
  groups,
  onSelectObject,
  selectedObjectId,
  onTransitionConclusion,
  transitionBusy,
}: {
  groups: WorkbenchReasoningGroup[];
  onSelectObject: (objectId: string) => void;
  selectedObjectId: string | null;
  onTransitionConclusion?: (resolutionId: string, action: "confirm" | "withdraw") => void;
  transitionBusy?: boolean;
}) {
  const [activeResolutionId, setActiveResolutionId] = useState<string | null>(null);
  const [selection, setSelection] = useState<MatrixSelection | null>(null);
  const selectedObjectGroup = groups.find(
    (group) =>
      group.resolutionSpecId === selectedObjectId ||
      group.hypotheses.some((item) => item.id === selectedObjectId) ||
      group.information.some((item) => item.id === selectedObjectId),
  );
  const selectedGroup =
    selectedObjectGroup ??
    groups.find((group) => group.resolutionSpecId === activeResolutionId) ??
    groups[0];

  if (!selectedGroup) {
    return (
      <section className={styles.reasoningMatrixEmpty}>
        <strong>当前工作稿还没有可比较的假设。</strong>
        <p>推理过程图仍会保留已有路径；竞争矩阵只读取当前工作稿的显式假设。</p>
      </section>
    );
  }

  const selectedConclusion = selectedGroup.conclusion;

  if (selectedGroup.hypotheses.length < 2) {
    return (
      <section className={styles.reasoningMatrix} aria-label="竞争矩阵">
        <ConclusionBand
          conclusion={selectedConclusion}
          onSelectResolution={() => onSelectObject(selectedGroup.resolutionSpecId)}
          onTransitionConclusion={onTransitionConclusion
            ? (action) => onTransitionConclusion(selectedGroup.resolutionSpecId, action)
            : undefined}
          question={selectedGroup.question}
          busy={transitionBusy}
        />
        <div className={styles.reasoningMatrixEmpty}>
          <strong>当前问题只有一个假设，至少需要两个解释才能比较。</strong>
          <p>{selectedGroup.question}</p>
        </div>
      </section>
    );
  }

  const selectCell = (
    hypothesis: WorkbenchReasoningGroup["hypotheses"][number],
    information: WorkbenchReasoningGroup["information"][number],
  ) => {
    const nextSelection = matrixSelectionFor(selectedGroup, hypothesis, information);
    setSelection(nextSelection);
    onSelectObject(information.id);
  };

  if (!selectedGroup.information.length) {
    return (
      <section className={styles.reasoningMatrix} aria-label="竞争矩阵">
        <ConclusionBand
          conclusion={selectedConclusion}
          onSelectResolution={() => onSelectObject(selectedGroup.resolutionSpecId)}
          onTransitionConclusion={onTransitionConclusion
            ? (action) => onTransitionConclusion(selectedGroup.resolutionSpecId, action)
            : undefined}
          question={selectedGroup.question}
          busy={transitionBusy}
        />
        <div className={styles.reasoningMatrixEmpty}>
          <strong>已有竞争解释，但尚未生成显式证据评估。</strong>
          <p>{selectedGroup.question}</p>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.reasoningMatrix} aria-labelledby="reasoning-matrix-heading">
      <ConclusionBand
        conclusion={selectedConclusion}
        onSelectResolution={() => onSelectObject(selectedGroup.resolutionSpecId)}
        onTransitionConclusion={onTransitionConclusion
          ? (action) => onTransitionConclusion(selectedGroup.resolutionSpecId, action)
          : undefined}
        question={selectedGroup.question}
        busy={transitionBusy}
      />
      <div className={styles.reasoningMatrixHeader}>
        <div>
          <span>竞争解释矩阵</span>
          <h3 id="reasoning-matrix-heading">{selectedGroup.question}</h3>
        </div>
        {groups.length > 1 ? (
          <label className={styles.reasoningGroupPicker}>
            <span>待解问题</span>
            <select
              aria-label="选择待解问题"
              onChange={(event) => {
                setActiveResolutionId(event.target.value);
                setSelection(null);
                onSelectObject(event.target.value);
              }}
              value={selectedGroup.resolutionSpecId}
            >
              {groups.map((group) => (
                <option key={group.resolutionSpecId} value={group.resolutionSpecId}>
                  {group.question}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      <div className={styles.reasoningMatrixDesktop}>
        <table>
          <thead>
            <tr>
              <th scope="col">信息</th>
              {selectedGroup.hypotheses.map((hypothesis) => {
                const conclusionRole = hypothesisConclusionRole(
                  selectedConclusion,
                  hypothesis.id,
                );
                return (
                  <th key={hypothesis.id} scope="col">
                    <button
                      aria-label={`定位假设：${hypothesis.title}${conclusionRole ? `，${conclusionRole}` : ""}`}
                      onClick={() => onSelectObject(hypothesis.id)}
                      type="button"
                    >
                      <span>{hypothesis.title}</span>
                      <small>{reasoningOutcomeLabels[hypothesis.outcome]}</small>
                      {conclusionRole ? (
                        <em className={styles.hypothesisConclusionRole}>{conclusionRole}</em>
                      ) : null}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {selectedGroup.information.map((information) => (
              <tr key={information.id}>
                <th scope="row">
                  <button
                    aria-label={`定位信息：${information.title}`}
                    onClick={() => onSelectObject(information.id)}
                    type="button"
                  >
                    <span>{information.title}</span>
                    <small>可靠性 · {reliabilityLabel(information.reliability)}</small>
                  </button>
                </th>
                {selectedGroup.hypotheses.map((hypothesis) => {
                  const assessment = selectedGroup.assessments.find(
                    (item) =>
                      item.hypothesisId === hypothesis.id &&
                      item.informationId === information.id,
                  );
                  const effect = assessment?.effect ?? "unassessed";
                  return (
                    <td key={hypothesis.id}>
                      <button
                        aria-label={`${information.title} 对 ${hypothesis.title}：${assessmentEffectLabels[effect]}`}
                        data-effect={effect}
                        onClick={() => selectCell(hypothesis, information)}
                        type="button"
                      >
                        <span>{assessmentEffectLabels[effect]}</span>
                        {assessment ? (
                          <small>{assessmentStrengthLabels[assessment.strength]}</small>
                        ) : null}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.reasoningMatrixMobile}>
        {selectedGroup.information.map((information) => (
          <article key={information.id}>
            <button
              aria-label={`定位信息：${information.title}`}
              className={styles.reasoningMatrixInformationButton}
              onClick={() => onSelectObject(information.id)}
              type="button"
            >
              <span>{information.title}</span>
              <small>可靠性 · {reliabilityLabel(information.reliability)}</small>
            </button>
            <ul>
              {selectedGroup.hypotheses.map((hypothesis) => {
                const assessment = selectedGroup.assessments.find(
                  (item) =>
                    item.hypothesisId === hypothesis.id &&
                    item.informationId === information.id,
                );
                const effect = assessment?.effect ?? "unassessed";
                const conclusionRole = hypothesisConclusionRole(
                  selectedConclusion,
                  hypothesis.id,
                );
                return (
                  <li key={hypothesis.id}>
                    <button
                      aria-label={`${information.title} 对 ${hypothesis.title}：${assessmentEffectLabels[effect]}`}
                      data-effect={effect}
                      onClick={() => selectCell(hypothesis, information)}
                      type="button"
                    >
                      <span>{hypothesis.title}</span>
                      <b>{assessmentEffectLabels[effect]}</b>
                      {assessment ? <small>{assessmentStrengthLabels[assessment.strength]}</small> : null}
                      {conclusionRole ? (
                        <em className={styles.hypothesisConclusionRole}>{conclusionRole}</em>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          </article>
        ))}
      </div>
      <aside className={styles.reasoningAssessmentDetail} aria-live="polite">
        {selection ? (
          <>
            <span>判定依据 · {selection.hypothesis.title}</span>
            <strong>{selection.information.title}</strong>
            {selection.assessment ? (
              <p>
                {assessmentEffectLabels[selection.assessment.effect]} ·
                {assessmentStrengthLabels[selection.assessment.strength]} ·
                {selection.assessment.rationale}
              </p>
            ) : (
              <p>该信息与此假设尚未评估；系统不会根据其他引用推断结论。</p>
            )}
          </>
        ) : (
          <p>选择一个单元格，查看判定依据并同步定位信息对象。</p>
        )}
      </aside>
    </section>
  );
}

export function buildReasoningCanvas(
  paths: Array<ReasoningPath & Partial<{ targetLabel: string }>>,
  conclusions: WorkbenchConclusion[] = [],
  reasoningGroups: WorkbenchReasoningGroup[] = [],
): ReasoningCanvasScene {
  const nodes = new Map<string, ReasoningCanvasNode>();
  const edges = new Map<string, ReasoningCanvasEdge>();
  const evidenceIds = [...new Set(paths.flatMap((path) => path.evidenceIds))];
  const conclusionsByResolution = new Map(
    conclusions.map((item) => [item.resolutionSpecId, item]),
  );

  const addNode = (node: ReasoningCanvasNode) => {
    if (!nodes.has(node.id)) nodes.set(node.id, node);
  };
  const addEdge = (edge: ReasoningCanvasEdge) => {
    if (!edges.has(edge.id)) edges.set(edge.id, edge);
  };

  paths.forEach((path) => {
    const resolutionSpecId =
      (path as ReasoningPath & Partial<{ resolutionSpecId: string | null }>).resolutionSpecId ??
      conclusions.find((item) => item.supportingReasoningPathIds.includes(path.id))
        ?.resolutionSpecId ?? null;
    const conclusion = resolutionSpecId
      ? conclusionsByResolution.get(resolutionSpecId)
      : undefined;
    const hypothesisId = `hypothesis-${path.hypothesisId}`;
    const conclusionId = resolutionSpecId
      ? `resolution-conclusion:${resolutionSpecId}`
      : null;
    if (conclusionId) {
      addNode({
        id: conclusionId,
        kind: "conclusion",
        caption: conclusion
          ? `${conclusionStatusLabel(conclusion)} · ${conclusionOutcomeLabel(conclusion)}`
          : "尚未形成结论",
        label: conclusion?.summary ?? "尚未形成结论",
        objectId: resolutionSpecId ?? undefined,
        outcome: conclusion?.reviewStatus ?? "missing",
      });
    }
    const conclusionRole = hypothesisConclusionRole(conclusion, path.hypothesisId);
    addNode({
      id: hypothesisId,
      kind: "hypothesis",
      caption: conclusionRole ?? "候选假设",
      label: path.targetLabel || path.hypothesisId,
      objectId: path.hypothesisId,
      outcome: path.outcome,
    });
    path.steps.forEach((step, stepIndex) => {
      const stepId = `step-${path.id}-${step.id}`;
      addNode({
        id: stepId,
        kind: "reason",
        caption: step.verb,
        label: step.claim,
      });
      for (const evidenceId of step.evidenceIds) {
        if (!evidenceIds.includes(evidenceId)) continue;
        addEdge({
          id: `${path.id}-${evidenceId}-${step.id}`,
          source: evidenceId,
          target: stepId,
          kind: "evidence",
        });
      }
      if (stepIndex > 0) {
        const previous = path.steps[stepIndex - 1];
        addEdge({
          id: `${path.id}-${previous.id}-${step.id}`,
          source: `step-${path.id}-${previous.id}`,
          target: stepId,
          kind: "chain",
        });
      }
    });
    const lastStep = path.steps[path.steps.length - 1];
    if (lastStep) {
      addEdge({
        id: `${path.id}-${lastStep.id}-hypothesis`,
        source: `step-${path.id}-${lastStep.id}`,
        target: hypothesisId,
        kind: path.outcome,
      });
    }
    if (conclusionId && (conclusionRole || !conclusion)) {
      addEdge({
        id: `${hypothesisId}-${conclusionId}`,
        source: hypothesisId,
        target: conclusionId,
        kind: "resolution",
      });
    }
  });

  reasoningGroups.forEach((group) => {
    const conclusion = conclusionsByResolution.get(group.resolutionSpecId) ?? group.conclusion;
    const conclusionId = `resolution-conclusion:${group.resolutionSpecId}`;
    addNode({
      id: conclusionId,
      kind: "conclusion",
      caption: conclusion
        ? `${conclusionStatusLabel(conclusion)} · ${conclusionOutcomeLabel(conclusion)}`
        : "尚未形成结论",
      label: conclusion?.summary ?? "尚未形成结论",
      objectId: group.resolutionSpecId,
      outcome: conclusion?.reviewStatus ?? "missing",
    });
  });

  evidenceIds.forEach((id) => {
    addNode({
      id,
      kind: "evidence",
      caption: "证据",
      label: id,
      objectId: id,
    });
  });

  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

export function ReasoningGraphView({
  seed,
  selectedObjectId,
  onSelectObject,
  layoutScope,
  onTransitionConclusion,
  transitionBusy = false,
}: {
  seed: WorkbenchSeed;
  selectedObjectId: string | null;
  onSelectObject: (objectId: string) => void;
  layoutScope: string;
  onTransitionConclusion?: (resolutionId: string, action: "confirm" | "withdraw") => void;
  transitionBusy?: boolean;
}) {
  const [mode, setMode] = useState<ReasoningMode>("graph");
  const activeReasoningGroup =
    seed.reasoningGroups?.find((group) =>
      group.resolutionSpecId === selectedObjectId ||
      group.hypotheses.some((item) => item.id === selectedObjectId) ||
      group.information.some((item) => item.id === selectedObjectId) ||
      seed.reasoningPaths.some(
        (path) =>
          (path as ReasoningPath & Partial<{ resolutionSpecId: string | null }>).resolutionSpecId === group.resolutionSpecId &&
          (path.id === selectedObjectId || path.evidenceIds.includes(selectedObjectId ?? "")),
      ),
    ) ?? seed.reasoningGroups?.[0];
  const scene = useMemo(
    () => buildReasoningCanvas(
      seed.reasoningPaths,
      seed.conclusions,
      seed.reasoningGroups ?? [],
    ),
    [seed.conclusions, seed.reasoningGroups, seed.reasoningPaths],
  );
  const evidenceById = useMemo(
    () => new Map(seed.caseObjects.map((object) => [object.id, object])),
    [seed.caseObjects],
  );
  const sceneNodes = useMemo<WorkbenchCanvasSceneNode[]>(
    () =>
      scene.nodes.map((node) => {
        const label =
          node.kind === "evidence"
            ? (evidenceById.get(node.id)?.label ?? node.label)
            : node.label;
        const prefix =
          node.kind === "conclusion"
            ? "最终结论"
            : node.kind === "hypothesis"
              ? "假设"
            : node.kind === "evidence"
              ? "证据"
              : "推理";
        return {
          id: node.id,
          variant: "reasoning",
          kind: node.kind,
          caption: node.caption,
          label,
          ariaLabel: `${prefix}：${label}`,
          accent: reasoningNodeMeta[node.kind].accent,
          selectableId: node.objectId,
          outcome: node.outcome,
          width: node.kind === "evidence" ? 176 : 216,
          height: node.kind === "reason" ? 72 : node.kind === "conclusion" ? 78 : 64,
        };
      }),
    [evidenceById, scene.nodes],
  );
  const sceneEdges = useMemo<WorkbenchCanvasSceneEdge[]>(
    () =>
      scene.edges.map((edge) => ({
        ...edge,
        label:
          edge.kind === "evidence"
            ? "证据引用"
            : edge.kind === "chain"
              ? "推理推进"
            : edge.kind === "hypothesis"
              ? "收束到假设"
              : edge.kind === "resolution"
                ? "进入最终结论"
                : reasoningOutcomeLabels[edge.kind],
      })),
    [scene.edges],
  );
  const externalSelectedNodeIds = useMemo(
    () =>
      scene.nodes.flatMap((node) =>
        node.objectId === selectedObjectId ? [node.id] : [],
      ),
    [scene.nodes, selectedObjectId],
  );
  const identity = useMemo<WorkbenchCanvasLayoutIdentity>(
    () => ({
      scope: layoutScope,
      revision: seed.caseMeta.revision,
      view: "reasoning",
    }),
    [layoutScope, seed.caseMeta.revision],
  );

  return (
    <section
      className={styles.reasoningView}
      aria-labelledby="reasoning-heading"
    >
      <header className={styles.sectionHeader}>
        <div>
          <span>推理过程图</span>
          <h2 id="reasoning-heading">信息 → 推理步骤 → 假设 → 最终结论</h2>
          <span className={styles.srOnly}>证据如何收束到假设</span>
        </div>
        <div className={styles.sectionTrailing}>
          <div aria-label="推理分析模式" className={styles.reasoningModeSwitch} role="tablist">
            <button
              aria-selected={mode === "graph"}
              onClick={() => setMode("graph")}
              role="tab"
              type="button"
            >
              过程图
            </button>
            <button
              aria-selected={mode === "matrix"}
              onClick={() => setMode("matrix")}
              role="tab"
              type="button"
            >
              竞争矩阵
            </button>
          </div>
          <small>{seed.reasoningPaths.length} 条路径</small>
        </div>
      </header>
      {mode === "graph" && activeReasoningGroup ? (
        <ConclusionBand
          conclusion={activeReasoningGroup.conclusion}
          onSelectResolution={() => onSelectObject(activeReasoningGroup.resolutionSpecId)}
          onTransitionConclusion={onTransitionConclusion
            ? (action) => onTransitionConclusion(activeReasoningGroup.resolutionSpecId, action)
            : undefined}
          question={activeReasoningGroup.question}
          busy={transitionBusy}
        />
      ) : null}
      {mode === "graph" && sceneNodes.length ? (
        <WorkbenchCanvasKernel
          ariaLabel="推理画布"
          direction="BT"
          edges={sceneEdges}
          externalSelectedNodeIds={externalSelectedNodeIds}
          identity={identity}
          key={`${identity.scope}:${identity.revision}:${identity.view}`}
          legend={
            <>
              <span data-kind="evidence">证据引用</span>
              <span data-kind="chain">推理推进</span>
              <span data-kind="hypothesis">收束到假设</span>
              <span data-kind="resolution">进入最终结论</span>
              <span data-kind="supported">支持</span>
              <span data-kind="contested">解释竞争</span>
              <span data-kind="eliminated">排除</span>
              <span className={styles.srOnly}>已排除</span>
            </>
          }
          nodeLegend={reasoningNodeLegend}
          nodes={sceneNodes}
          onActivateNode={onSelectObject}
        />
      ) : mode === "graph" ? (
        <p className={styles.viewNote}>候选没有可展示的推理路径。</p>
      ) : (
        <ReasoningMatrix
          groups={seed.reasoningGroups ?? []}
          onSelectObject={onSelectObject}
          selectedObjectId={selectedObjectId}
          onTransitionConclusion={onTransitionConclusion}
          transitionBusy={transitionBusy}
        />
      )}
      {mode === "graph" ? <div className={styles.reasoningTables}>
        {seed.reasoningPaths.map((path) => {
          const summary = path.steps
            .map((step) => `${step.verb}：${step.claim}`)
            .join("；");
          return (
            <details className={styles.reasoningAlternative} key={path.id}>
              <summary>
                推理表 · {path.question}（
                {reasoningOutcomeLabels[path.outcome]}）
              </summary>
              <div className={styles.reasoningTableWrap}>
                <table>
                  <thead>
                    <tr>
                      <th>依据</th>
                      <th>推理</th>
                      <th>最终结论</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>
                        {path.evidenceIds
                          .map((id) => evidenceById.get(id)?.label)
                          .filter(Boolean)
                          .join("、")}
                      </td>
                      <td>{summary}</td>
                      <td>{path.conclusion}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          );
        })}
      </div> : null}
    </section>
  );
}
