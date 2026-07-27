"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Connection,
  useNodesState,
} from "@xyflow/react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo } from "react";

import {
  getReasoningSource,
  type ReasoningEdge,
} from "@/lib/reasoning-prototype";
import { usePrototype } from "@/store/prototype-store";

import { layoutReasoningPath } from "./reasoning-layout";
import {
  ReasoningCanvasNode,
  type ReasoningFlowNode,
} from "./reasoning-node";
import styles from "./reasoning-lab.module.css";

const nodeTypes = {
  "reasoning-node": ReasoningCanvasNode,
};

function edgeColor(edge: ReasoningEdge): string {
  if (edge.kind === "refutes") return "#9c3f2c";
  if (edge.status === "candidate") return "#a46d1f";
  if (edge.status === "conflict") return "#b07825";
  if (edge.status === "excluded") return "#8a746c";
  return "#3f5f4b";
}

function edgeDash(edge: ReasoningEdge): string | undefined {
  if (
    edge.status === "candidate" ||
    edge.status === "excluded" ||
    edge.status === "conflict"
  ) {
    return "7 6";
  }
  return undefined;
}

export function ReasoningCanvas() {
  const router = useRouter();
  const { state, dispatch } = usePrototype();
  const reasoning = state.reasoning;
  const path = reasoning.paths.find(
    (item) => item.id === reasoning.activePathId,
  );

  const pathNodes = useMemo(
    () =>
      reasoning.nodes.filter((node) => node.pathId === reasoning.activePathId),
    [reasoning.activePathId, reasoning.nodes],
  );
  const pathEdges = useMemo(
    () =>
      reasoning.edges.filter((edge) => edge.pathId === reasoning.activePathId),
    [reasoning.activePathId, reasoning.edges],
  );
  const layoutPositions = useMemo(
    () => layoutReasoningPath(pathNodes, pathEdges),
    [pathEdges, pathNodes],
  );

  const openSource = useCallback((sourceId: string) => {
    const source = getReasoningSource(sourceId);
    if (!source?.targetEventId) return;
    dispatch({ type: "select-event", id: source.targetEventId });
    router.push(`/workbench#event=${encodeURIComponent(source.targetEventId)}`);
  }, [dispatch, router]);

  const projectedNodes = useMemo<ReasoningFlowNode[]>(
    () =>
      pathNodes.map((node) => ({
        id: node.id,
        type: "reasoning-node",
        position:
          reasoning.positions[node.id] ??
          layoutPositions[node.id] ?? { x: 0, y: 0 },
        selected: reasoning.selectedNodeId === node.id,
        data: {
          node,
          expanded: reasoning.expandedBundleIds.includes(node.id),
          onToggleBundle: (id: string) =>
            dispatch({ type: "toggle-reasoning-bundle", id }),
          onOpenSource: openSource,
        },
      })),
    [
      dispatch,
      layoutPositions,
      openSource,
      pathNodes,
      reasoning.expandedBundleIds,
      reasoning.positions,
      reasoning.selectedNodeId,
    ],
  );

  const projectedEdges = useMemo<Edge[]>(
    () =>
      pathEdges.map((edge) => {
        const color = edgeColor(edge);
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.label,
          type: "smoothstep",
          animated: edge.status === "candidate",
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color,
            width: 17,
            height: 17,
          },
          style: {
            stroke: color,
            strokeWidth: edge.status === "candidate" ? 2.4 : 1.8,
            strokeDasharray: edgeDash(edge),
          },
          labelStyle: {
            fill: color,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "0.08em",
          },
          labelBgStyle: {
            fill: "#fafaf5",
            fillOpacity: 0.92,
          },
          labelBgPadding: [5, 3] as [number, number],
          labelBgBorderRadius: 2,
        };
      }),
    [pathEdges],
  );

  const [flowNodes, setFlowNodes, onNodesChange] =
    useNodesState<ReasoningFlowNode>(projectedNodes);

  useEffect(() => {
    setFlowNodes(projectedNodes);
  }, [projectedNodes, setFlowNodes]);

  function handleConnect(connection: Connection) {
    if (
      reasoning.status !== "ready" ||
      !path ||
      !connection.source ||
      !connection.target
    ) {
      return;
    }
    dispatch({
      type: "connect-reasoning-nodes",
      pathId: path.id,
      source: connection.source,
      target: connection.target,
    });
  }

  function autoLayout() {
    dispatch({
      type: "set-reasoning-positions",
      positions: layoutReasoningPath(pathNodes, pathEdges),
    });
  }

  if (!path) {
    return (
      <div className={styles.canvasEmpty}>
        <span>NO ACTIVE PATH</span>
        <strong>请选择一条推理路径</strong>
      </div>
    );
  }

  return (
    <section
      aria-label={`${path.title}推理画布`}
      className={styles.canvasPanel}
    >
      <div className={styles.canvasToolbar}>
        <div>
          <span>{path.code}</span>
          <strong>{path.question}</strong>
        </div>
        <div>
          <button
            disabled={reasoning.status !== "ready"}
            onClick={() =>
              dispatch({ type: "add-user-reasoning-node", pathId: path.id })
            }
            type="button"
          >
            ＋ 人工假设
          </button>
          <button onClick={autoLayout} type="button">
            自动布局
          </button>
        </div>
      </div>

      <div className={styles.canvasViewport}>
        <ReactFlow<ReasoningFlowNode>
          colorMode="light"
          defaultEdgeOptions={{ type: "smoothstep" }}
          edges={projectedEdges}
          elementsSelectable
          fitView
          fitViewOptions={{ maxZoom: 1.05, padding: 0.2 }}
          minZoom={0.28}
          nodeTypes={nodeTypes}
          nodes={flowNodes}
          nodesConnectable={reasoning.status === "ready"}
          nodesDraggable={reasoning.status !== "stale"}
          onConnect={handleConnect}
          onNodeClick={(_, node) =>
            dispatch({ type: "select-reasoning-node", id: node.id })
          }
          onNodeDragStop={(_, node) =>
            dispatch({
              type: "set-reasoning-node-position",
              id: node.id,
              position: node.position,
            })
          }
          onNodesChange={onNodesChange}
          panOnScroll
          selectionOnDrag
        >
          <Background
            color="rgba(41, 42, 37, 0.22)"
            gap={24}
            size={1}
            variant={BackgroundVariant.Dots}
          />
          <MiniMap
            className={styles.miniMap}
            maskColor="rgba(244, 244, 239, 0.76)"
            nodeColor={(node) => {
              const status = (node.data as ReasoningFlowNode["data"]).node
                .status;
              if (status === "candidate") return "#c58b34";
              if (status === "excluded") return "#9b7268";
              if (status === "conflict") return "#b14b31";
              if (status === "confirmed") return "#496954";
              return "#87928a";
            }}
            pannable
            zoomable
          />
          <Controls
            className={styles.flowControls}
            position="bottom-left"
            showInteractive={false}
          />
        </ReactFlow>
      </div>
    </section>
  );
}
