import dagre from "@dagrejs/dagre";

import type {
  ReasoningEdge,
  ReasoningNode,
  ReasoningNodePosition,
} from "@/lib/reasoning-prototype";

const NODE_WIDTH = 244;
const NODE_HEIGHT = 124;
const BUNDLE_HEIGHT = 138;
const BUNDLE_SOURCE_ROW_HEIGHT = 62;
const BUNDLE_SOURCE_LIST_MAX_HEIGHT = 248;

function reasoningNodeHeight(
  node: ReasoningNode,
  expandedBundleIds: ReadonlySet<string>,
) {
  if (node.kind !== "source-bundle") return NODE_HEIGHT;
  if (!expandedBundleIds.has(node.id)) return BUNDLE_HEIGHT;

  return (
    BUNDLE_HEIGHT +
    Math.min(
      node.sourceIds.length * BUNDLE_SOURCE_ROW_HEIGHT,
      BUNDLE_SOURCE_LIST_MAX_HEIGHT,
    )
  );
}

export function getReasoningAutoLayoutUpdates(
  nodeIds: readonly string[],
  storedPositions: Record<string, ReasoningNodePosition>,
  currentLayout: Record<string, ReasoningNodePosition>,
  nextLayout: Record<string, ReasoningNodePosition>,
): Record<string, ReasoningNodePosition> {
  return Object.fromEntries(
    nodeIds.flatMap((nodeId) => {
      const storedPosition = storedPositions[nodeId];
      const currentLayoutPosition = currentLayout[nodeId];
      const nextLayoutPosition = nextLayout[nodeId];
      const followsAutomaticLayout =
        !storedPosition ||
        !currentLayoutPosition ||
        (Math.abs(storedPosition.x - currentLayoutPosition.x) < 0.5 &&
          Math.abs(storedPosition.y - currentLayoutPosition.y) < 0.5);

      return followsAutomaticLayout && nextLayoutPosition
        ? [[nodeId, nextLayoutPosition]]
        : [];
    }),
  );
}

export function layoutReasoningPath(
  nodes: ReasoningNode[],
  edges: ReasoningEdge[],
  direction: "LR" | "TB" = "LR",
  expandedBundleIds: readonly string[] = [],
): Record<string, ReasoningNodePosition> {
  const expandedBundles = new Set(expandedBundleIds);
  const graph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: direction,
    ranksep: direction === "LR" ? 104 : 82,
    nodesep: 58,
    edgesep: 24,
    marginx: 44,
    marginy: 44,
  });

  nodes.forEach((node) => {
    graph.setNode(node.id, {
      width: NODE_WIDTH,
      height: reasoningNodeHeight(node, expandedBundles),
    });
  });

  edges.forEach((edge) => {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      graph.setEdge(edge.source, edge.target);
    }
  });

  dagre.layout(graph);

  return Object.fromEntries(
    nodes.map((node) => {
      const layoutNode = graph.node(node.id) as
        | { x: number; y: number }
        | undefined;
      const height = reasoningNodeHeight(node, expandedBundles);
      return [
        node.id,
        {
          x: (layoutNode?.x ?? 0) - NODE_WIDTH / 2,
          y: (layoutNode?.y ?? 0) - height / 2,
        },
      ];
    }),
  );
}
