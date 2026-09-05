import { useCallback, useMemo, useState } from "react";

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
import type {
  WorkbenchConclusion,
  WorkbenchGraphEdge,
  WorkbenchGraphNode,
} from "./workbench-real-data-types";

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
  kind:
    | "evidence"
    | "chain"
    | "hypothesis"
    | "resolution"
    | "supports"
    | "contradicts"
    | "neutral"
    | ReasoningOutcome;
  label?: string;
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

function hypothesisConclusionRole(
  conclusion: WorkbenchConclusion | undefined,
  hypothesisId: string,
) {
  if (!conclusion?.selectedHypothesisIds.includes(hypothesisId)) return null;
  return conclusion.outcome === "undetermined" ? "并存解释" : "进入当前结论";
}

function ReasoningConclusionCard({
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
  const [open, setOpen] = useState(false);
  return (
    <aside
      aria-label="核心问题与最终结论"
      className={styles.reasoningCanvasCard}
      data-outcome={conclusion?.outcome ?? "missing"}
      data-status={conclusion?.reviewStatus ?? "missing"}
    >
      <header className={styles.reasoningCanvasCardHeader}>
        <button
          aria-label={`查看核心问题：${question}`}
          className={styles.reasoningCanvasCardQuestion}
          onClick={onSelectResolution}
          type="button"
        >
          <span>核心问题</span>
          <strong>{question}</strong>
        </button>
        <div className={styles.reasoningCanvasCardStatus}>
          <b>{conclusionStatusLabel(conclusion)}</b>
          <i>{conclusionOutcomeLabel(conclusion)}</i>
        </div>
        <button
          aria-expanded={open}
          className={styles.reasoningCanvasCardToggle}
          onClick={() => setOpen((value) => !value)}
          type="button"
        >
          {open ? "收起结论" : "展开结论"}
        </button>
      </header>
      {open ? (
        <div className={styles.reasoningCanvasCardBody}>
          <h3>{conclusion?.summary ?? "该核心问题尚未形成结论"}</h3>
          {conclusion ? (
            <>
              {conclusion.values.length ? (
                <dl className={styles.conclusionValues}>
                  {conclusion.values.map((item) => (
                    <div key={`${item.label}:${item.value}`}>
                      <dt>{item.label}</dt>
                      <dd>{item.value}</dd>
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
      ) : null}
    </aside>
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

function ReasoningAssessmentCard({
  cell,
  onClose,
}: {
  cell: MatrixSelection;
  onClose: () => void;
}) {
  const { hypothesis, information, assessment } = cell;
  const effect = assessment?.effect ?? "unassessed";
  return (
    <aside
      aria-label="判定依据"
      aria-live="polite"
      className={styles.reasoningAssessmentCard}
      data-effect={effect}
    >
      <header>
        <div>
          <span>判定依据 · {hypothesis.title}</span>
          <strong>{information.title}</strong>
        </div>
        <button aria-label="关闭判定依据" onClick={onClose} type="button">
          ×
        </button>
      </header>
      {assessment ? (
        <>
          <p className={styles.reasoningAssessmentEffect}>
            {assessmentEffectLabels[assessment.effect]} ·{" "}
            {assessmentStrengthLabels[assessment.strength]}
          </p>
          <p>{assessment.rationale}</p>
        </>
      ) : (
        <p>该信息与此假设尚未评估；系统不会根据其他引用推断结论。</p>
      )}
    </aside>
  );
}

const matrixNodeMeta: Record<
  "hypothesis" | "information",
  { accent: string; legend: string }
> = {
  hypothesis: { accent: "#7f4a92", legend: "假设" },
  information: { accent: "#2f6fb2", legend: "信息" },
};

const matrixNodeLegend: WorkbenchCanvasLegendItem[] = (
  Object.entries(matrixNodeMeta) as Array<
    [
      "hypothesis" | "information",
      (typeof matrixNodeMeta)["hypothesis" | "information"],
    ]
  >
).map(([id, meta]) => ({ id, label: meta.legend, accent: meta.accent }));

export type ReasoningMatrixEffect =
  | "supports"
  | "contradicts"
  | "neutral"
  | "unassessed";

export interface ReasoningMatrixSceneNode {
  id: string;
  kind: "hypothesis" | "information";
  caption: string;
  label: string;
  outcome?: ReasoningOutcome;
  objectId: string;
}

export interface ReasoningMatrixSceneEdge {
  id: string;
  source: string;
  target: string;
  kind: ReasoningMatrixEffect;
  label: string;
  ariaLabel: string;
}

export interface ReasoningMatrixScene {
  nodes: ReasoningMatrixSceneNode[];
  edges: ReasoningMatrixSceneEdge[];
}

export function buildReasoningMatrixScene(
  group: WorkbenchReasoningGroup,
): ReasoningMatrixScene {
  const conclusion = group.conclusion;
  const nodes: ReasoningMatrixSceneNode[] = [
    ...group.hypotheses.map((hypothesis) => {
      const role = hypothesisConclusionRole(conclusion, hypothesis.id);
      return {
        id: hypothesis.id,
        kind: "hypothesis" as const,
        caption: role ? `假设 · ${role}` : "假设",
        label: hypothesis.title,
        outcome: hypothesis.outcome,
        objectId: hypothesis.id,
      };
    }),
    ...group.information.map((information) => ({
      id: information.id,
      kind: "information" as const,
      caption: `信息 · ${reliabilityLabel(information.reliability)}`,
      label: information.title,
      objectId: information.id,
    })),
  ];
  const edges: ReasoningMatrixSceneEdge[] = group.information.flatMap(
    (information) =>
      group.hypotheses.map((hypothesis) => {
        const assessment = group.assessments.find(
          (item) =>
            item.hypothesisId === hypothesis.id &&
            item.informationId === information.id,
        );
        const label = assessment
          ? `${assessmentEffectLabels[assessment.effect]} · ${assessmentStrengthLabels[assessment.strength]}`
          : "未评估";
        return {
          id: `${information.id}×${hypothesis.id}`,
          source: information.id,
          target: hypothesis.id,
          kind: assessment?.effect ?? "unassessed",
          label,
          ariaLabel: `${information.title} 对 ${hypothesis.title}：${label}`,
        };
      }),
  );
  return { nodes, edges };
}

export function buildReasoningCanvas(
  paths: Array<ReasoningPath & Partial<{ targetLabel: string }>>,
  conclusions: WorkbenchConclusion[] = [],
  reasoningGroups: WorkbenchReasoningGroup[] = [],
  relationshipGraph: {
    nodes: Array<Partial<WorkbenchGraphNode> & { objectId: string }>;
    edges: Array<Partial<WorkbenchGraphEdge> & { from: string; to: string }>;
  } | null = null,
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

  if (!paths.length) {
    // 无显式推理路径：先用假设/信息/判定依据（推理组）补全过程图。
    const groupsWithContent = reasoningGroups.filter(
      (group) => group.hypotheses.length > 0,
    );
    groupsWithContent.forEach((group) => {
      const conclusion =
        conclusionsByResolution.get(group.resolutionSpecId) ?? group.conclusion;
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
      group.hypotheses.forEach((hypothesis) => {
        const hypothesisNodeId = `hypothesis-${hypothesis.id}`;
        const conclusionRole = hypothesisConclusionRole(conclusion, hypothesis.id);
        addNode({
          id: hypothesisNodeId,
          kind: "hypothesis",
          caption: conclusionRole ?? "候选假设",
          label: hypothesis.title,
          objectId: hypothesis.id,
          outcome: hypothesis.outcome,
        });
        if (conclusionRole || !conclusion) {
          addEdge({
            id: `${hypothesisNodeId}-${conclusionId}`,
            source: hypothesisNodeId,
            target: conclusionId,
            kind: "resolution",
          });
        }
      });
      group.information.forEach((information) => {
        addNode({
          id: information.id,
          kind: "evidence",
          caption: `信息 · ${reliabilityLabel(information.reliability)}`,
          label: information.title,
          objectId: information.id,
        });
      });
      group.assessments.forEach((assessment) => {
        addEdge({
          id: `assessment:${assessment.hypothesisId}:${assessment.informationId}`,
          source: assessment.informationId,
          target: `hypothesis-${assessment.hypothesisId}`,
          kind: assessment.effect,
        });
      });
    });

    // 连推理组也没有：从关系图中取推理相关子集，构成论证图雏形。
    if (!groupsWithContent.length && relationshipGraph) {
      const reasoningEdgeKinds = new Set([
        "hypothesis_requirement",
        "hypothesis_falsifier",
        "hypothesis_competitor",
        "hypothesis_conclusion",
        "reasoning_conclusion",
        "information_support",
        "information_refute",
      ]);
      const reasoningNodeKinds = new Set([
        "hypothesis",
        "information_unit",
        "information",
        "evidence",
        "claim",
        "reasoning_path",
      ]);
      const keptEdges = relationshipGraph.edges.filter((edge) =>
        reasoningEdgeKinds.has(String(edge.kind ?? "")),
      );
      const touchedIds = new Set<string>();
      keptEdges.forEach((edge) => {
        touchedIds.add(edge.from);
        touchedIds.add(edge.to);
      });
      relationshipGraph.nodes.forEach((node) => {
        if (
          !node.objectId.startsWith("resolution-conclusion:") &&
          !reasoningNodeKinds.has(String(node.kind ?? "")) &&
          !touchedIds.has(node.objectId)
        ) {
          return;
        }
        const isConclusion = node.objectId.startsWith("resolution-conclusion:");
        const kind: ReasoningNodeKind = isConclusion
          ? "conclusion"
          : node.kind === "hypothesis"
            ? "hypothesis"
            : node.kind === "claim" || node.kind === "reasoning_path"
              ? "reason"
              : "evidence";
        const caption = isConclusion
          ? "最终结论"
          : node.kind === "hypothesis"
            ? "候选假设"
            : node.kind === "claim"
              ? "主张"
              : node.kind === "reasoning_path"
                ? "推理路径"
                : "信息";
        addNode({
          id: node.objectId,
          kind,
          caption,
          label: node.label ?? node.objectId,
          objectId: node.directoryObjectId ?? undefined,
        });
      });
      keptEdges.forEach((edge) => {
        const kind: ReasoningCanvasEdge["kind"] =
          edge.kind === "information_support"
            ? "supports"
            : edge.kind === "information_refute" ||
                edge.kind === "hypothesis_competitor" ||
                edge.kind === "hypothesis_falsifier"
              ? "contradicts"
              : edge.kind === "hypothesis_conclusion" ||
                  edge.kind === "reasoning_conclusion"
                ? "resolution"
                : "evidence";
        addEdge({
          id: edge.id ?? `${edge.from}-${edge.to}`,
          source: edge.from,
          target: edge.to,
          kind,
          label: edge.label,
        });
      });
    }
  }

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
  const [matrixGroupId, setMatrixGroupId] = useState<string | null>(null);
  const [matrixCell, setMatrixCell] = useState<MatrixSelection | null>(null);
  const groups = seed.reasoningGroups ?? [];
  const activeReasoningGroup =
    groups.find((group) =>
      group.resolutionSpecId === selectedObjectId ||
      group.hypotheses.some((item) => item.id === selectedObjectId) ||
      group.information.some((item) => item.id === selectedObjectId) ||
      seed.reasoningPaths.some(
        (path) =>
          (path as ReasoningPath & Partial<{ resolutionSpecId: string | null }>).resolutionSpecId === group.resolutionSpecId &&
          (path.id === selectedObjectId || path.evidenceIds.includes(selectedObjectId ?? "")),
      ),
    ) ?? groups[0];
  const matrixGroup =
    groups.find((group) =>
      group.resolutionSpecId === selectedObjectId ||
      group.hypotheses.some((item) => item.id === selectedObjectId) ||
      group.information.some((item) => item.id === selectedObjectId),
    ) ??
    groups.find((group) => group.resolutionSpecId === matrixGroupId) ??
    groups[0];
  const scene = useMemo(
    () => buildReasoningCanvas(
      seed.reasoningPaths,
      seed.conclusions,
      seed.reasoningGroups ?? [],
      {
        nodes: seed.graphNodes as Array<
          Partial<WorkbenchGraphNode> & { objectId: string }
        >,
        edges: seed.graphEdges as Array<
          Partial<WorkbenchGraphEdge> & { from: string; to: string }
        >,
      },
    ),
    [
      seed.conclusions,
      seed.graphEdges,
      seed.graphNodes,
      seed.reasoningGroups,
      seed.reasoningPaths,
    ],
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
          edge.label ??
          (edge.kind === "evidence"
            ? "证据引用"
            : edge.kind === "chain"
              ? "推理推进"
              : edge.kind === "hypothesis"
                ? "收束到假设"
                : edge.kind === "resolution"
                  ? "进入最终结论"
                  : edge.kind === "supports"
                    ? "支持"
                    : edge.kind === "contradicts"
                      ? "冲突"
                      : edge.kind === "neutral"
                        ? "不区分"
                        : reasoningOutcomeLabels[edge.kind]),
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
  const matrixScene = useMemo(
    () =>
      matrixGroup &&
      matrixGroup.hypotheses.length >= 2 &&
      matrixGroup.information.length > 0
        ? buildReasoningMatrixScene(matrixGroup)
        : { nodes: [], edges: [] },
    [matrixGroup],
  );
  const matrixSceneNodes = useMemo<WorkbenchCanvasSceneNode[]>(
    () =>
      matrixScene.nodes.map((node) => ({
        id: node.id,
        variant: "reasoning",
        kind: node.kind,
        caption: node.caption,
        label: node.label,
        ariaLabel:
          node.kind === "hypothesis"
            ? `假设：${node.label}`
            : `信息：${node.label}`,
        accent: matrixNodeMeta[node.kind].accent,
        selectableId: node.objectId,
        outcome: node.outcome,
        width: 216,
        height: 64,
      })),
    [matrixScene.nodes],
  );
  const matrixSceneEdges = useMemo<WorkbenchCanvasSceneEdge[]>(
    () => matrixScene.edges.map((edge) => ({ ...edge })),
    [matrixScene.edges],
  );
  const matrixSelectedNodeIds = useMemo(
    () =>
      matrixScene.nodes.flatMap((node) =>
        node.objectId === selectedObjectId ? [node.id] : [],
      ),
    [matrixScene.nodes, selectedObjectId],
  );
  const activateMatrixCell = useCallback(
    (edgeId: string) => {
      if (!matrixGroup) return;
      const separator = edgeId.indexOf("×");
      if (separator <= 0) return;
      const informationId = edgeId.slice(0, separator);
      const hypothesisId = edgeId.slice(separator + 1);
      const hypothesis = matrixGroup.hypotheses.find(
        (item) => item.id === hypothesisId,
      );
      const information = matrixGroup.information.find(
        (item) => item.id === informationId,
      );
      if (!hypothesis || !information) return;
      setMatrixCell(matrixSelectionFor(matrixGroup, hypothesis, information));
      onSelectObject(information.id);
    },
    [matrixGroup, onSelectObject],
  );
  const selectMatrixGroup = (resolutionId: string) => {
    setMatrixGroupId(resolutionId);
    setMatrixCell(null);
    onSelectObject(resolutionId);
  };
  const activeMatrixEdgeId = matrixCell
    ? `${matrixCell.information.id}×${matrixCell.hypothesis.id}`
    : null;
  const matrixEmptyHint = !matrixGroup ? (
    <p className={styles.reasoningCanvasEmpty}>
      <strong>当前工作稿还没有可比较的假设。</strong>
      <span>
        推理过程图仍会保留已有路径；竞争矩阵只读取当前工作稿的显式假设。
      </span>
    </p>
  ) : matrixGroup.hypotheses.length < 2 ? (
    <p className={styles.reasoningCanvasEmpty}>
      <strong>当前问题只有一个假设，至少需要两个解释才能比较。</strong>
      <span>{matrixGroup.question}</span>
    </p>
  ) : matrixGroup.information.length === 0 ? (
    <p className={styles.reasoningCanvasEmpty}>
      <strong>已有竞争解释，但尚未生成显式证据评估。</strong>
      <span>{matrixGroup.question}</span>
    </p>
  ) : null;
  const identity = useMemo<WorkbenchCanvasLayoutIdentity>(
    () => ({
      scope: layoutScope,
      revision: seed.caseMeta.revision,
      view: mode === "graph" ? "reasoning" : "matrix",
    }),
    [layoutScope, mode, seed.caseMeta.revision],
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
          {mode === "graph" ? (
            <small>{seed.reasoningPaths.length} 条路径</small>
          ) : groups.length > 1 ? (
            <label className={styles.reasoningGroupPicker}>
              <span>待解问题</span>
              <select
                aria-label="选择待解问题"
                onChange={(event) => selectMatrixGroup(event.target.value)}
                value={matrixGroup?.resolutionSpecId ?? ""}
              >
                {groups.map((group) => (
                  <option
                    key={group.resolutionSpecId}
                    value={group.resolutionSpecId}
                  >
                    {group.question}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
      </header>
      <div className={styles.reasoningCanvasHost}>
        {mode === "graph" ? (
          <>
            <WorkbenchCanvasKernel
              ariaLabel="推理画布"
              direction="BT"
              edges={sceneEdges}
              emptyHint={
                <p className={styles.reasoningCanvasEmpty}>
                  <strong>当前工作稿还没有可展示的推理内容。</strong>
                  <span>
                    生成或采用深稿后，这里会以无限画布呈现 证据 → 推理步骤 → 假设 → 最终结论。
                  </span>
                </p>
              }
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
                  <span data-kind="contradicts">冲突</span>
                  <span data-kind="neutral">不区分</span>
                  <span data-kind="contested">解释竞争</span>
                  <span data-kind="eliminated">排除</span>
                  <span className={styles.srOnly}>已排除</span>
                </>
              }
              nodeLegend={reasoningNodeLegend}
              nodes={sceneNodes}
              onActivateNode={onSelectObject}
            />
            {activeReasoningGroup ? (
              <ReasoningConclusionCard
                busy={transitionBusy}
                conclusion={activeReasoningGroup.conclusion}
                onSelectResolution={() => onSelectObject(activeReasoningGroup.resolutionSpecId)}
                onTransitionConclusion={onTransitionConclusion
                  ? (action) => onTransitionConclusion(activeReasoningGroup.resolutionSpecId, action)
                  : undefined}
                question={activeReasoningGroup.question}
              />
            ) : null}
          </>
        ) : (
          <>
            <WorkbenchCanvasKernel
              activeEdgeId={activeMatrixEdgeId}
              ariaLabel="竞争矩阵画布"
              direction="BT"
              edges={matrixSceneEdges}
              emptyHint={matrixEmptyHint}
              externalSelectedNodeIds={matrixSelectedNodeIds}
              identity={identity}
              key={`${identity.scope}:${identity.revision}:${identity.view}`}
              layout="matrix"
              legend={
                <>
                  <span data-kind="supported">支持</span>
                  <span data-kind="contradicts">冲突</span>
                  <span data-kind="neutral">不区分</span>
                  <span data-kind="unassessed">未评估</span>
                </>
              }
              nodeLegend={matrixNodeLegend}
              nodes={matrixSceneNodes}
              onActivateEdge={activateMatrixCell}
              onActivateNode={onSelectObject}
            />
            {matrixGroup ? (
              <ReasoningConclusionCard
                busy={transitionBusy}
                conclusion={matrixGroup.conclusion}
                onSelectResolution={() => onSelectObject(matrixGroup.resolutionSpecId)}
                onTransitionConclusion={onTransitionConclusion
                  ? (action) => onTransitionConclusion(matrixGroup.resolutionSpecId, action)
                  : undefined}
                question={matrixGroup.question}
              />
            ) : null}
            {matrixCell ? (
              <ReasoningAssessmentCard
                cell={matrixCell}
                onClose={() => setMatrixCell(null)}
              />
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
