import { useMemo } from "react";

import {
  type ReasoningOutcome,
  type ReasoningPath,
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
import { reasoningOutcomeLabels } from "./workbench-presenters";

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
          <small>{seed.reasoningPaths.length} PATHS</small>
        </div>
      </header>
      {sceneNodes.length ? (
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
      ) : (
        <p className={styles.viewNote}>候选没有可展示的推理路径。</p>
      )}
      <div className={styles.reasoningTables}>
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
      </div>
    </section>
  );
}
