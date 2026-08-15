import { useMemo } from "react";

import {
  getObject,
  objectKindLabels,
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
import type { WorkbenchGraphNode } from "./workbench-real-data";

const graphReferenceLabels: Record<string, string> = {
  casefile: "卷宗",
  resolution_spec: "核心问题",
  entity: "实体",
  person: "人物",
  information_unit: "信息",
  information: "信息",
  evidence: "证据",
  event: "事件",
  location: "地点",
  hypothesis: "假设",
  relationship: "关系",
  claim: "主张",
  reasoning_path: "推理路径",
  constraint: "约束",
  structure_lock: "结构锁",
  source_fragment: "来源片段",
  unknown: "引用",
};

const relationshipNodeAccents: Record<string, string> = {
  casefile: "#263d42",
  resolution_spec: "#d07a22",
  entity: "#2f7891",
  person: "#4b6fb1",
  information: "#a84f78",
  information_unit: "#9b5aa4",
  evidence: "#c17c12",
  event: "#c54b4b",
  location: "#6e862d",
  hypothesis: "#7f4a92",
  relationship: "#2e7b67",
  claim: "#087f8c",
  reasoning_path: "#5361a5",
  constraint: "#bd4068",
  structure_lock: "#465154",
  source_fragment: "#25849b",
  unknown: "#747c7b",
};

function relationshipNodeAccent(kind: string) {
  return relationshipNodeAccents[kind] ?? relationshipNodeAccents.unknown;
}

export function RelationshipGraph({
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
  const graphNodes = seed.graphNodes;
  const mappedSeed = seed as WorkbenchSeed & Partial<{ origin: "contract" | "fixture" }>;
  const sceneNodes = useMemo<WorkbenchCanvasSceneNode[]>(
    () =>
      graphNodes.map((node) => {
        const mappedNode = node as typeof node & Partial<WorkbenchGraphNode>;
        const object = getObject(seed, node.objectId);
        const selectableId = object?.id ?? mappedNode.directoryObjectId ?? undefined;
        const kind =
          mappedSeed.origin === "fixture"
            ? mappedNode.kind ?? object?.kind ?? "unknown"
            : object?.kind ?? mappedNode.kind ?? "unknown";
        const label = object?.label ?? mappedNode.label ?? node.objectId;
        const isConclusionNode = node.objectId.startsWith("resolution-conclusion:");
        const caption =
          isConclusionNode
            ? "最终结论"
            : mappedSeed.origin === "fixture"
            ? (graphReferenceLabels[String(kind)] ?? "引用")
            : object
              ? objectKindLabels[object.kind]
              : (graphReferenceLabels[String(kind)] ?? "引用");
        return {
          id: node.objectId,
          variant: "relationship",
          kind: String(kind),
          caption,
          label,
          ariaLabel: `${caption}：${label}`,
          accent: isConclusionNode ? "#a84b32" : relationshipNodeAccent(String(kind)),
          selectableId,
          width: 232,
          height: 76,
        };
      }),
    [graphNodes, mappedSeed.origin, seed],
  );
  const nodeLegend = useMemo<WorkbenchCanvasLegendItem[]>(() => {
    const seen = new Set<string>();
    return sceneNodes.flatMap((node) => {
      if (seen.has(node.kind)) return [];
      seen.add(node.kind);
      return [{ id: node.kind, label: node.caption, accent: node.accent }];
    });
  }, [sceneNodes]);
  const sceneEdges = useMemo<WorkbenchCanvasSceneEdge[]>(
    () =>
      seed.graphEdges.map((edge, index) => ({
        id:
          (edge as { id?: string }).id ??
          `${edge.from}-${edge.to}-${edge.label}-${index}`,
        source: edge.from,
        target: edge.to,
        label: edge.label,
        kind: "relationship",
      })),
    [seed.graphEdges],
  );
  const visibleNodeIds = new Set(graphNodes.map((node) => node.objectId));
  const externalSelectedNodeIds = useMemo(
    () =>
      graphNodes.flatMap((node) => {
        const mappedNode = node as typeof node & Partial<WorkbenchGraphNode>;
        const selectableId =
          getObject(seed, node.objectId)?.id ?? mappedNode.directoryObjectId;
        return selectableId === selectedObjectId ? [node.objectId] : [];
      }),
    [graphNodes, seed, selectedObjectId],
  );
  const identity = useMemo<WorkbenchCanvasLayoutIdentity>(
    () => ({
      scope: layoutScope,
      revision: seed.caseMeta.revision,
      view: "relations",
    }),
    [layoutScope, seed.caseMeta.revision],
  );
  const graphNodeById = new Map(
    sceneNodes.map((node) => [node.id, node.label]),
  );

  return (
    <section className={styles.relationsView} aria-labelledby="relations-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>关系网络</span>
          <h2 id="relations-heading">核心问题如何收束为最终结论</h2>
        </div>
        <div className={styles.sectionTrailing}>
          <small>
            {visibleNodeIds.size} 个节点 · {sceneEdges.length} 条关系
          </small>
        </div>
      </header>
      <p className={styles.srOnly} id="relationship-graph-summary">
        {seed.caseMeta.relationshipSummary}
      </p>
      {sceneNodes.length ? (
        <WorkbenchCanvasKernel
          ariaLabel="事件关系图"
          direction="LR"
          edges={sceneEdges}
          externalSelectedNodeIds={externalSelectedNodeIds}
          identity={identity}
          key={`${identity.scope}:${identity.revision}:${identity.view}`}
          legend={
            <span data-kind="focus">
              铜色线索表示当前聚焦关系 · Ctrl + 左键选中节点
            </span>
          }
          nodeLegend={nodeLegend}
          nodes={sceneNodes}
          onActivateNode={onSelectObject}
          requireModifierForNodeSelection
        />
      ) : (
        <p className={styles.viewNote}>当前工作稿没有可展示的关系节点。</p>
      )}
      <details className={styles.graphAlternative}>
        <summary>查看关系表与文字摘要</summary>
        <div className={styles.graphTableWrap}>
          <table>
            <thead>
              <tr>
                <th>来源</th>
                <th>关系</th>
                <th>目标</th>
              </tr>
            </thead>
            <tbody>
              {seed.graphEdges.map((edge, edgeIndex) => (
                <tr key={`table-${edge.from}-${edge.to}-${edgeIndex}`}>
                  <td>{graphNodeById.get(edge.from)}</td>
                  <td>{edge.label}</td>
                  <td>{graphNodeById.get(edge.to)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <span className={styles.srOnly}>
        {visibleNodeIds.size} 个可访问节点
      </span>
    </section>
  );
}
