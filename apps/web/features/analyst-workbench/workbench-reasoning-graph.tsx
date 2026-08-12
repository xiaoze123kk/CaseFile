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

type ReasoningNodeKind = "evidence" | "reason" | "hypothesis";

const reasoningNodeMeta: Record<
  ReasoningNodeKind,
  { accent: string; legend: string }
> = {
  evidence: { accent: "#c17c12", legend: "证据" },
  reason: { accent: "#277a83", legend: "推理步骤" },
  hypothesis: { accent: "#7f4a92", legend: "假设" },
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
  outcome?: ReasoningOutcome;
  objectId?: string;
}

interface ReasoningCanvasEdge {
  id: string;
  source: string;
  target: string;
  kind: "evidence" | "chain" | ReasoningOutcome;
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
}: {
  groups: WorkbenchReasoningGroup[];
  onSelectObject: (objectId: string) => void;
  selectedObjectId: string | null;
}) {
  const [activeResolutionId, setActiveResolutionId] = useState<string | null>(null);
  const [selection, setSelection] = useState<MatrixSelection | null>(null);
  const selectedGroup =
    groups.find((group) => group.resolutionSpecId === activeResolutionId) ??
    groups.find((group) => group.hypotheses.some((item) => item.id === selectedObjectId)) ??
    groups.find((group) => group.information.some((item) => item.id === selectedObjectId)) ??
    groups[0];

  if (!selectedGroup) {
    return (
      <section className={styles.reasoningMatrixEmpty}>
        <strong>当前工作稿还没有可比较的假设。</strong>
        <p>推理过程图仍会保留已有路径；竞争矩阵只读取当前工作稿的显式假设。</p>
      </section>
    );
  }

  if (selectedGroup.hypotheses.length < 2) {
    return (
      <section className={styles.reasoningMatrixEmpty}>
        <strong>当前问题只有一个假设，至少需要两个解释才能比较。</strong>
        <p>{selectedGroup.question}</p>
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
      <section className={styles.reasoningMatrixEmpty}>
        <strong>已有竞争解释，但尚未生成显式证据评估。</strong>
        <p>{selectedGroup.question}</p>
      </section>
    );
  }

  return (
    <section className={styles.reasoningMatrix} aria-labelledby="reasoning-matrix-heading">
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
              {selectedGroup.hypotheses.map((hypothesis) => (
                <th key={hypothesis.id} scope="col">
                  <button
                    aria-label={`定位假设：${hypothesis.title}`}
                    onClick={() => onSelectObject(hypothesis.id)}
                    type="button"
                  >
                    <span>{hypothesis.title}</span>
                    <small>{reasoningOutcomeLabels[hypothesis.outcome]}</small>
                  </button>
                </th>
              ))}
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
  paths: ReasoningPath[],
): ReasoningCanvasScene {
  const nodes: ReasoningCanvasNode[] = [];
  const edges: ReasoningCanvasEdge[] = [];
  const evidenceIds = [...new Set(paths.flatMap((path) => path.evidenceIds))];

  paths.forEach((path) => {
    const conclusionId = `conclusion-${path.id}`;
    nodes.push({
      id: conclusionId,
      kind: "hypothesis",
      caption: reasoningOutcomeLabels[path.outcome],
      label: path.conclusion,
      outcome: path.outcome,
      objectId: path.hypothesisId,
    });
    path.steps.forEach((step, stepIndex) => {
      const stepId = `step-${step.id}`;
      nodes.push({
        id: stepId,
        kind: "reason",
        caption: step.verb,
        label: step.claim,
      });
      for (const evidenceId of step.evidenceIds) {
        if (!evidenceIds.includes(evidenceId)) continue;
        edges.push({
          id: `${path.id}-${evidenceId}-${step.id}`,
          source: evidenceId,
          target: stepId,
          kind: "evidence",
        });
      }
      if (stepIndex > 0) {
        const previous = path.steps[stepIndex - 1];
        edges.push({
          id: `${path.id}-${previous.id}-${step.id}`,
          source: `step-${previous.id}`,
          target: stepId,
          kind: "chain",
        });
      }
    });
    const lastStep = path.steps[path.steps.length - 1];
    if (lastStep) {
      edges.push({
        id: `${path.id}-${lastStep.id}-conclusion`,
        source: `step-${lastStep.id}`,
        target: conclusionId,
        kind: path.outcome,
      });
    }
  });

  evidenceIds.forEach((id) => {
    nodes.push({
      id,
      kind: "evidence",
      caption: "证据",
      label: id,
      objectId: id,
    });
  });

  return { nodes, edges };
}

export function ReasoningGraphView({
  seed,
  selectedObjectId,
  onSelectObject,
  layoutScope,
}: {
  seed: WorkbenchSeed;
  selectedObjectId: string | null;
  onSelectObject: (objectId: string) => void;
  layoutScope: string;
}) {
  const [mode, setMode] = useState<ReasoningMode>("graph");
  const scene = useMemo(
    () => buildReasoningCanvas(seed.reasoningPaths),
    [seed.reasoningPaths],
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
          node.kind === "hypothesis"
            ? "结论"
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
          height: node.kind === "reason" ? 72 : 64,
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
          <h2 id="reasoning-heading">证据如何收束到假设</h2>
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
              <span data-kind="supported">支持</span>
              <span data-kind="contested">竞争</span>
              <span data-kind="eliminated">排除</span>
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
                      <th>结论</th>
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
