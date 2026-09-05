import { type CSSProperties, useMemo } from "react";

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
import type {
  WorkbenchGraphEdge,
  WorkbenchGraphNode,
} from "./workbench-real-data";

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
  casefile: "#5eead4",
  resolution_spec: "#fbbf24",
  entity: "#647b9b",
  person: "#647b9b",
  information: "#d946ef",
  information_unit: "#c084fc",
  evidence: "#fb923c",
  event: "#b96d70",
  location: "#4b8d7d",
  hypothesis: "#8c77a3",
  relationship: "#438994",
  claim: "#22d3ee",
  reasoning_path: "#4b7a9c",
  constraint: "#f472b6",
  structure_lock: "#94a3b8",
  source_fragment: "#2dd4bf",
  unknown: "#8892a4",
};

const relationshipNodeKinds = new Set(["entity", "person", "location"]);

const relationshipEdgePalette = [
  "#4b7a9c",
  "#438994",
  "#488c70",
  "#aa7b3e",
  "#b96d70",
  "#8c77a3",
] as const;

function relationshipNodeAccent(kind: string) {
  return relationshipNodeAccents[kind] ?? relationshipNodeAccents.unknown;
}

function relationshipEdgeAppearance(label: string) {
  const normalized = label.trim().toLocaleLowerCase();
  if (/敌|冲突|竞争|加害|杀|操控|rival|enemy|conflict|harm|control/u.test(normalized)) {
    return { accent: "#b96d70", strokeDasharray: "2 5" };
  }
  if (/亲|家|父|母|兄|姐|弟|妹|family|parent|sibling/u.test(normalized)) {
    return { accent: "#8c77a3", strokeDasharray: "2 5" };
  }
  if (/成员|雇佣|隶属|member|employ/u.test(normalized)) {
    return { accent: "#aa7b3e" };
  }
  if (/盟|友|支持|协作|ally|friend|support|cooperat/u.test(normalized)) {
    return { accent: "#488c70", strokeDasharray: "7 5" };
  }
  if (/知|联系|调查|目击|know|contact|investigat|witness/u.test(normalized)) {
    return { accent: "#4b7a9c" };
  }
  const paletteIndex = [...normalized].reduce(
    (value, character) => value + (character.codePointAt(0) ?? 0),
    0,
  ) % relationshipEdgePalette.length;
  return { accent: relationshipEdgePalette[paletteIndex] };
}

export function RelationshipGraph({
  seed,
  selectedObjectId,
  onSelectObject,
  layoutScope,
  onOpenRelation,
}: {
  seed: WorkbenchSeed;
  selectedObjectId: string | null;
  onSelectObject: (objectId: string) => void;
  layoutScope: string;
  onOpenRelation?: (relationId: string, objectId: string) => void;
}) {
  const graphNodes = seed.graphNodes;
  const mappedSeed = seed as WorkbenchSeed & Partial<{ origin: "contract" | "fixture" }>;
  const entityGraphNodes = useMemo(
    () =>
      graphNodes.filter((node) => {
        const mappedNode = node as typeof node & Partial<WorkbenchGraphNode>;
        const object = getObject(seed, node.objectId);
        const kind =
          mappedSeed.origin === "fixture"
            ? mappedNode.kind ?? object?.kind ?? "unknown"
            : object?.kind ?? mappedNode.kind ?? "unknown";
        return relationshipNodeKinds.has(String(kind));
      }),
    [graphNodes, mappedSeed.origin, seed],
  );
  const entityNodeIds = useMemo(
    () => new Set(entityGraphNodes.map((node) => node.objectId)),
    [entityGraphNodes],
  );
  const entityGraphEdges = useMemo(
    () =>
      seed.graphEdges.filter(
        (edge) =>
          entityNodeIds.has(edge.from) &&
          entityNodeIds.has(edge.to) &&
          (mappedSeed.origin === "fixture" ||
            (edge as Partial<WorkbenchGraphEdge>).kind === "relationship"),
      ),
    [entityNodeIds, mappedSeed.origin, seed.graphEdges],
  );
  const degreeByNodeId = useMemo(() => {
    const degrees = new Map<string, number>();
    entityGraphEdges.forEach((edge) => {
      degrees.set(edge.from, (degrees.get(edge.from) ?? 0) + 1);
      degrees.set(edge.to, (degrees.get(edge.to) ?? 0) + 1);
    });
    return degrees;
  }, [entityGraphEdges]);
  const sceneNodes = useMemo<WorkbenchCanvasSceneNode[]>(
    () =>
      entityGraphNodes.map((node) => {
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
        const size = Math.min(80, 62 + (degreeByNodeId.get(node.objectId) ?? 0) * 3);
        return {
          id: node.objectId,
          variant: "relationship",
          kind: String(kind),
          caption,
          label,
          ariaLabel: `${caption}：${label}`,
          accent: isConclusionNode ? "#a84b32" : relationshipNodeAccent(String(kind)),
          selectableId,
          width: size,
          height: size,
        };
      }),
    [degreeByNodeId, entityGraphNodes, mappedSeed.origin, seed],
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
      entityGraphEdges.map((edge, index) => {
        const appearance = relationshipEdgeAppearance(edge.label);
        return {
          id:
            (edge as { id?: string }).id ??
            `${edge.from}-${edge.to}-${edge.label}-${index}`,
          source: edge.from,
          target: edge.to,
          label: edge.label,
          kind: "relationship",
          accent: appearance.accent,
          strokeDasharray: appearance.strokeDasharray,
          direction:
            (edge as Partial<WorkbenchGraphEdge>).direction ?? "directed",
        };
      }),
    [entityGraphEdges],
  );
  const visibleNodeIds = new Set(entityGraphNodes.map((node) => node.objectId));
  const externalSelectedNodeIds = useMemo(
    () =>
      entityGraphNodes.flatMap((node) => {
        const mappedNode = node as typeof node & Partial<WorkbenchGraphNode>;
        const selectableId =
          getObject(seed, node.objectId)?.id ?? mappedNode.directoryObjectId;
        return selectableId === selectedObjectId ? [node.objectId] : [];
      }),
    [entityGraphNodes, seed, selectedObjectId],
  );
  const identity = useMemo<WorkbenchCanvasLayoutIdentity>(
    () => ({
      scope: layoutScope,
      revision: seed.caseMeta.revision,
      view: "relations",
    }),
    [layoutScope, seed.caseMeta.revision],
  );
  function activateRelation(edgeId: string) {
    const index = sceneEdges.findIndex((item) => item.id === edgeId);
    const edge = entityGraphEdges[index] as WorkbenchGraphEdge | undefined;
    if (edge) onOpenRelation?.(edge.sourceObjectId ? `relationship:${edge.sourceObjectId}` : edgeId, edge.from);
  }

  return (
    <section className={styles.relationsView} aria-labelledby="relations-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>关系网络</span>
          <h2 id="relations-heading">人物、实体与地点节点</h2>
        </div>
        <div className={styles.sectionTrailing}>
          <small>
            {visibleNodeIds.size} 个节点 · {sceneEdges.length} 条关系
          </small>
        </div>
      </header>
      <p className={styles.srOnly} id="relationship-graph-summary">
        当前关系图包含 {visibleNodeIds.size} 个人物、实体或地点节点，以及 {sceneEdges.length} 条实体语义关系；地点仅作为节点展示。
      </p>
      {sceneNodes.length ? (
        <WorkbenchCanvasKernel
          ariaLabel="实体关系图"
          direction="LR"
          edges={sceneEdges}
          externalSelectedNodeIds={externalSelectedNodeIds}
          focusDirectRelationsOnClick
          identity={identity}
          key={`${identity.scope}:${identity.revision}:${identity.view}`}
          layout="constellation"
          legend={sceneEdges.length ? (
            <>
              <b>关系类型</b>
              {[...new Map(
                sceneEdges.map((edge) => [edge.label ?? "关系", edge]),
              ).entries()]
                .slice(0, 7)
                .map(([label, edge]) => (
                  <span
                    data-kind="relationship"
                    key={label}
                    style={{ "--canvas-edge-accent": edge.accent } as CSSProperties}
                  >
                    {label}
                  </span>
                ))}
            </>
          ) : undefined}
          nodeLegend={nodeLegend}
          nodes={sceneNodes}
          onActivateNode={onSelectObject}
          onActivateEdge={onOpenRelation ? activateRelation : undefined}
        />
      ) : (
        <p className={styles.viewNote}>当前工作稿没有可展示的人物、实体或地点。</p>
      )}
      <span className={styles.srOnly}>
        {visibleNodeIds.size} 个可访问节点
      </span>
    </section>
  );
}
