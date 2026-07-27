import dagre from "@dagrejs/dagre";

import type {
  ReasoningEdge,
  ReasoningNode,
  ReasoningNodePosition,
} from "@/lib/reasoning-prototype";

const NODE_WIDTH = 244;
const NODE_HEIGHT = 124;
const BUNDLE_HEIGHT = 138;

export function layoutReasoningPath(
  nodes: ReasoningNode[],
  edges: ReasoningEdge[],
  direction: "LR" | "TB" = "LR",
): Record<string, ReasoningNodePosition> {
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
      height: node.kind === "source-bundle" ? BUNDLE_HEIGHT : NODE_HEIGHT,
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
      const height =
        node.kind === "source-bundle" ? BUNDLE_HEIGHT : NODE_HEIGHT;
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
