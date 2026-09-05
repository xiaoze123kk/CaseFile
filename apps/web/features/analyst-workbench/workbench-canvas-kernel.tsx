"use client";

import {
  applyNodeChanges,
  BaseEdge,
  EdgeLabelRenderer,
  getStraightPath,
  getSmoothStepPath,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  SelectionMode,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type OnNodeDrag,
  type ReactFlowInstance,
  type Viewport,
} from "@xyflow/react";
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import styles from "./workbench-canvas.module.css";
import {
  type WorkbenchCanvasDirection,
  type WorkbenchCanvasLayoutIdentity,
  type WorkbenchCanvasPoint,
  elasticRelationshipDragPositions,
  layoutWorkbenchCanvas,
  layoutWorkbenchConstellationCanvas,
  layoutWorkbenchMatrixCanvas,
  restoreWorkbenchCanvasLayout,
  saveWorkbenchCanvasLayout,
  workbenchCanvasLayoutStorageKey,
} from "./workbench-canvas-layout";
import {
  type CanvasTool,
  CanvasKernelControls,
} from "./workbench-canvas-controls";

export interface WorkbenchCanvasSceneNode {
  id: string;
  variant: "relationship" | "reasoning";
  kind: string;
  caption: string;
  label: string;
  ariaLabel: string;
  accent: string;
  width: number;
  height: number;
  selectableId?: string;
  outcome?: string;
}

export interface WorkbenchCanvasLegendItem {
  id: string;
  label: string;
  accent: string;
}

export interface WorkbenchCanvasSceneEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  kind?: string;
  ariaLabel?: string;
  accent?: string;
  strokeDasharray?: string;
  direction?: "directed" | "undirected" | "bidirectional";
}

interface WorkbenchCanvasNodeData extends Record<string, unknown> {
  variant: WorkbenchCanvasSceneNode["variant"];
  kind: string;
  caption: string;
  label: string;
  ariaLabel: string;
  accent: string;
  selectableId?: string;
  outcome?: string;
  direction: WorkbenchCanvasDirection;
  active: boolean;
  dimmed: boolean;
  selected: boolean;
  related: boolean;
  onActivate: () => void;
  onFocusChange: (focused: boolean) => void;
}

type WorkbenchFlowNode = Node<WorkbenchCanvasNodeData, "casefile">;

interface CanvasHistoryFrame {
  positions: Record<string, WorkbenchCanvasPoint>;
  viewport: Viewport;
}

interface CanvasHistory {
  past: CanvasHistoryFrame[];
  future: CanvasHistoryFrame[];
}

function browserStorage() {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function positionsFromNodes(nodes: WorkbenchFlowNode[]) {
  return Object.fromEntries(
    nodes.map((node) => [node.id, { x: node.position.x, y: node.position.y }]),
  );
}

function frameFromNodes(
  nodes: WorkbenchFlowNode[],
  viewport: Viewport,
): CanvasHistoryFrame {
  return { positions: positionsFromNodes(nodes), viewport: { ...viewport } };
}

function sameFrame(left: CanvasHistoryFrame, right: CanvasHistoryFrame) {
  const leftIds = Object.keys(left.positions);
  const rightIds = Object.keys(right.positions);
  if (leftIds.length !== rightIds.length) return false;
  return leftIds.every((id) => {
    const leftPoint = left.positions[id];
    const rightPoint = right.positions[id];
    return (
      rightPoint &&
      leftPoint.x === rightPoint.x &&
      leftPoint.y === rightPoint.y
    );
  });
}

function applyPositions(
  nodes: WorkbenchFlowNode[],
  positions: Record<string, WorkbenchCanvasPoint>,
) {
  return nodes.map((node) => ({
    ...node,
    position: positions[node.id] ?? node.position,
  }));
}

function WorkbenchCanvasNode({
  id,
  data,
  selected,
}: NodeProps<WorkbenchFlowNode>) {
  const horizontal = data.direction === "LR";
  const interactive = Boolean(data.selectableId);
  const pressed = selected || data.selected;

  function activateWithKeyboard(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!interactive || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    data.onActivate();
  }

  return (
    <div
      aria-label={data.ariaLabel}
      aria-pressed={interactive ? pressed : undefined}
      className={
        data.variant === "relationship"
          ? styles.graphNode
          : styles.reasoningNode
      }
      data-active={data.active}
      data-agent-object-id={data.selectableId ?? undefined}
      data-dimmed={data.dimmed}
      data-kind={data.kind}
      data-outcome={data.outcome}
      data-related={data.related}
      data-selected={pressed}
      onBlur={() => data.onFocusChange(false)}
      onFocus={() => data.onFocusChange(true)}
      onKeyDown={activateWithKeyboard}
      role={interactive ? "button" : "img"}
      style={{
        "--canvas-node-accent": data.accent,
        "--canvas-breath-delay": `${-([...id].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 60) / 10}s`,
      } as CSSProperties}
      tabIndex={0}
    >
      <Handle
        className={styles.canvasHandle}
        isConnectable={false}
        position={horizontal ? Position.Left : Position.Bottom}
        type="target"
      />
      <small>{data.caption}</small>
      <strong>{data.label}</strong>
      <Handle
        className={styles.canvasHandle}
        isConnectable={false}
        position={horizontal ? Position.Right : Position.Top}
        type="source"
      />
    </div>
  );
}

const nodeTypes = { casefile: WorkbenchCanvasNode };

interface WorkbenchInteractiveEdgeData {
  label: string;
  ariaLabel: string;
  effect: string;
  active: boolean;
  onActivate: () => void;
}

function WorkbenchInteractiveEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps) {
  const edgeData = (data ?? {}) as Partial<WorkbenchInteractiveEdgeData>;
  const label = edgeData.label ?? "";
  const ariaLabel = edgeData.ariaLabel ?? label;
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 12,
  });
  const active = edgeData.active ?? false;
  return (
    <>
      <path
        className={active ? styles.canvasEdgeActive : styles.canvasEdge}
        d={edgePath}
        fill="none"
        id={id}
        style={edgeStyle(edgeData.effect, active)}
      />
      <EdgeLabelRenderer>
        <div
          className="nodrag nopan"
          style={{
            position: "absolute",
            pointerEvents: "all",
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
        >
          <button
            aria-label={ariaLabel}
            aria-pressed={active}
            className={styles.canvasEdgeCell}
            data-active={active}
            data-effect={edgeData.effect}
            onClick={edgeData.onActivate}
            type="button"
          >
            {label}
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

function WorkbenchRelationshipEdge(props: EdgeProps) {
  const [path] = getStraightPath(props);
  const fraction = typeof props.data?.labelFraction === "number" ? props.data.labelFraction : 0.5;
  const labelX = props.sourceX + (props.targetX - props.sourceX) * fraction;
  const labelY = props.sourceY + (props.targetY - props.sourceY) * fraction;
  return <>
    <BaseEdge
      id={props.id} path={path} labelX={labelX} labelY={labelY}
      label={props.label} labelStyle={props.labelStyle}
      labelShowBg={props.labelShowBg} labelBgStyle={props.labelBgStyle}
      labelBgPadding={props.labelBgPadding} labelBgBorderRadius={props.labelBgBorderRadius}
      style={props.style} markerStart={props.markerStart} markerEnd={props.markerEnd}
      interactionWidth={props.interactionWidth}
    />
    {props.data?.flowing ? <path
      aria-hidden="true" className={styles.relationshipFlow}
      d={path} fill="none" pathLength={100}
      stroke={props.style?.stroke} strokeWidth={2}
    /> : null}
  </>;
}

const edgeTypes = { casefileEdge: WorkbenchInteractiveEdge, relationshipEdge: WorkbenchRelationshipEdge };

function edgeStyle(
  kind: string | undefined,
  active: boolean,
  accent?: string,
  strokeDasharray?: string,
) {
  if (kind === "relationship") {
    return {
      stroke: accent ?? "#60a5fa",
      strokeWidth: active ? 2.4 : 1.45,
      strokeDasharray,
      opacity: active ? 1 : 0.7,
      filter: active
        ? `drop-shadow(0 0 4px ${accent ?? "#60a5fa"})`
        : undefined,
    };
  }
  if (active) {
    return {
      stroke: "var(--primary)",
      strokeWidth: 2.2,
      strokeDasharray:
        kind === "contested" || kind === "eliminated" ? "6 4" : undefined,
    };
  }
  if (kind === "chain") {
    return { stroke: "var(--primary)", strokeWidth: 1.5, opacity: 0.72 };
  }
  if (kind === "supported" || kind === "supports") {
    return { stroke: "var(--success)", strokeWidth: 1.5, opacity: 0.72 };
  }
  if (kind === "neutral") {
    return { stroke: "#277a83", strokeWidth: 1.5, opacity: 0.72 };
  }
  if (kind === "contested") {
    return {
      stroke: "var(--warning)",
      strokeWidth: 1.5,
      strokeDasharray: "6 4",
      opacity: 0.72,
    };
  }
  if (kind === "eliminated" || kind === "contradicts") {
    return {
      stroke: "var(--issue)",
      strokeWidth: 1.5,
      strokeDasharray: "6 4",
      opacity: 0.72,
    };
  }
  if (kind === "unassessed") {
    return {
      stroke: "#8a8f8d",
      strokeWidth: 1.25,
      strokeDasharray: "4 4",
      opacity: 0.5,
    };
  }
  return { stroke: "var(--relation)", strokeWidth: 1.25, opacity: 0.56 };
}

export function WorkbenchCanvasKernel({
  ariaLabel,
  direction,
  emptyHint,
  externalSelectedNodeIds,
  identity,
  layout = "dagre",
  legend,
  nodeLegend,
  nodes: sceneNodes,
  edges: sceneEdges,
  onActivateEdge,
  onActivateNode,
  activeEdgeId = null,
  elasticConnectedDrag = false,
  focusDirectRelationsOnClick = false,
  requireModifierForNodeSelection = false,
}: {
  ariaLabel: string;
  direction: WorkbenchCanvasDirection;
  emptyHint?: ReactNode;
  externalSelectedNodeIds: string[];
  identity: WorkbenchCanvasLayoutIdentity;
  layout?: "dagre" | "matrix" | "constellation";
  legend?: ReactNode;
  nodeLegend?: WorkbenchCanvasLegendItem[];
  nodes: WorkbenchCanvasSceneNode[];
  edges: WorkbenchCanvasSceneEdge[];
  onActivateEdge?: (edgeId: string) => void;
  onActivateNode: (selectableId: string) => void;
  activeEdgeId?: string | null;
  elasticConnectedDrag?: boolean;
  focusDirectRelationsOnClick?: boolean;
  requireModifierForNodeSelection?: boolean;
}) {
  const automaticPositions = useMemo(
    () =>
      layout === "matrix"
        ? layoutWorkbenchMatrixCanvas(
            sceneNodes.map(({ id, width, height, kind }) => ({
              id,
              width,
              height,
              kind,
            })),
          )
        : layout === "constellation"
          ? layoutWorkbenchConstellationCanvas(
              sceneNodes.map(({ id, width, height }) => ({ id, width, height })),
              sceneEdges,
            )
          : layoutWorkbenchCanvas(
              sceneNodes.map(({ id, width, height }) => ({ id, width, height })),
              sceneEdges,
              direction,
            ),
    [direction, layout, sceneEdges, sceneNodes],
  );
  const interactiveEdges = Boolean(onActivateEdge);
  const automaticNodes = useMemo<WorkbenchFlowNode[]>(
    () => {
      const horizontal = direction === "LR";
      // 交互边场景（竞争矩阵）需要边在首帧即渲染：显式声明与节点 DOM
      // 手柄一致的 handles，保证没有 ResizeObserver 测量的测试环境也能
      // 立即渲染边；其余场景沿用测量后的真实手柄边界。
      const interactive = interactiveEdges;
      return sceneNodes.map((node) => {
        const automaticNode: WorkbenchFlowNode = {
          id: node.id,
          type: "casefile",
          position: automaticPositions[node.id] ?? { x: 0, y: 0 },
          width: node.width,
          height: node.height,
          ariaLabel: node.ariaLabel,
          selectable: requireModifierForNodeSelection ? false : undefined,
          data: {
            variant: node.variant,
            kind: node.kind,
            caption: node.caption,
            label: node.label,
            ariaLabel: node.ariaLabel,
            accent: node.accent,
            selectableId: node.selectableId,
            outcome: node.outcome,
            direction,
            active: false,
            dimmed: false,
            selected: false,
            related: false,
            onActivate: () => undefined,
            onFocusChange: () => undefined,
          },
        };
        if (!interactive) return automaticNode;
        return {
          ...automaticNode,
          measured: { width: node.width, height: node.height },
          handles: [
            {
              id: `${node.id}-source`,
              type: "source" as const,
              position: horizontal ? Position.Right : Position.Top,
              x: horizontal ? node.width : node.width / 2,
              y: horizontal ? node.height / 2 : 0,
            },
            {
              id: `${node.id}-target`,
              type: "target" as const,
              position: horizontal ? Position.Left : Position.Bottom,
              x: horizontal ? 0 : node.width / 2,
              y: horizontal ? node.height / 2 : node.height,
            },
          ],
        };
      });
    },
    [automaticPositions, direction, interactiveEdges, requireModifierForNodeSelection, sceneNodes],
  );
  const [nodes, setNodes] = useState(automaticNodes);
  const nodesRef = useRef(nodes);
  const [tool, setTool] = useState<CanvasTool>("select");
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [relationshipFocusNodeId, setRelationshipFocusNodeId] = useState<
    string | null
  >(null);
  const [hasCanvasSelectionIntent, setHasCanvasSelectionIntent] =
    useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [motionEnabled, setMotionEnabled] = useState(true);
  const [warning, setWarning] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState("");
  const [viewport, setViewportState] = useState<Viewport>({
    x: 0,
    y: 0,
    zoom: 1,
  });
  const viewportRef = useRef(viewport);
  const shellRef = useRef<HTMLDivElement>(null);
  const [instance, setInstance] =
    useState<ReactFlowInstance<WorkbenchFlowNode, Edge> | null>(null);
  const [storageReady, setStorageReady] = useState(false);
  const pendingViewportRef = useRef<Viewport | null | undefined>(undefined);
  const dragStartFrameRef = useRef<CanvasHistoryFrame | null>(null);
  const elasticFollowerCountRef = useRef(0);
  const selectedNodeIdsRef = useRef(new Set<string>());
  const selectionBoxActiveRef = useRef(false);
  const canvasActivationPendingRef = useRef(false);
  const previousExternalSelectionRef = useRef(
    [...externalSelectedNodeIds].sort().join("\u0000"),
  );
  const historyRef = useRef<CanvasHistory>({ past: [], future: [] });
  const [history, setHistory] = useState<CanvasHistory>({
    past: [],
    future: [],
  });

  const setHistoryState = useCallback((next: CanvasHistory) => {
    historyRef.current = next;
    setHistory(next);
  }, []);

  const persist = useCallback(
    (nextNodes: WorkbenchFlowNode[], nextViewport = viewportRef.current) => {
      const storage = browserStorage();
      const failure = saveWorkbenchCanvasLayout(
        storage,
        identity,
        positionsFromNodes(nextNodes),
        nextViewport,
      );
      if (failure) setWarning(failure);
    },
    [identity],
  );

  useEffect(() => {
    const restored = restoreWorkbenchCanvasLayout(
      browserStorage(),
      identity,
      automaticPositions,
    );
    const frame = window.requestAnimationFrame(() => {
      const restoredNodes = applyPositions(automaticNodes, restored.positions);
      nodesRef.current = restoredNodes;
      setNodes(restoredNodes);
      setWarning(restored.warning);
      pendingViewportRef.current = restored.viewport;
      setStorageReady(true);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [automaticNodes, automaticPositions, identity]);

  useEffect(() => {
    if (!instance || !storageReady) return;
    const restoredViewport = pendingViewportRef.current;
    pendingViewportRef.current = undefined;
    if (restoredViewport) {
      viewportRef.current = restoredViewport;
      setViewportState(restoredViewport);
      void instance.setViewport(restoredViewport, { duration: 0 });
      return;
    }
    if (!automaticNodes.length) return;
    void instance
      .fitView({ padding: 0.18, minZoom: 0.12, maxZoom: 1.2, duration: 0 })
      .then(() => {
        const fitted = instance.getViewport();
        viewportRef.current = fitted;
        setViewportState(fitted);
      });
  }, [instance, storageReady, automaticNodes.length]);

  useEffect(() => {
    function handleFullscreenChange() {
      const fullscreen = document.fullscreenElement === shellRef.current;
      setIsFullscreen(fullscreen);
      window.requestAnimationFrame(() => {
        if (!instance || !nodesRef.current.length) return;
        void instance
          .fitView({
            padding: 0.18,
            minZoom: 0.12,
            maxZoom: 1.2,
            duration: 0,
          })
          .then(() => {
            const fitted = instance.getViewport();
            viewportRef.current = fitted;
            setViewportState(fitted);
            persist(nodesRef.current, fitted);
          });
      });
    }

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () =>
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, [instance, persist]);

  const commitFrame = useCallback(
    (before: CanvasHistoryFrame, after: CanvasHistoryFrame) => {
      if (sameFrame(before, after)) return;
      setHistoryState({
        past: [...historyRef.current.past, before].slice(-50),
        future: [],
      });
    },
    [setHistoryState],
  );

  const onNodesChange = useCallback((changes: NodeChange<WorkbenchFlowNode>[]) => {
    const safeChanges = changes.filter(
      (change) =>
        change.type !== "remove" &&
        change.type !== "add" &&
        change.type !== "replace" &&
        (change.type !== "select" || selectionBoxActiveRef.current),
    );
    if (!safeChanges.length) return;
    const next = applyNodeChanges(safeChanges, nodesRef.current);
    nodesRef.current = next;
    setNodes(next);
  }, []);

  const toggleNodeSelection = useCallback((nodeId: string) => {
    const selectedIds = new Set(selectedNodeIdsRef.current);
    const willSelect = !selectedIds.has(nodeId);
    if (willSelect) selectedIds.add(nodeId);
    else selectedIds.delete(nodeId);
    selectedNodeIdsRef.current = selectedIds;
    const next = nodesRef.current.map((node) => ({
      ...node,
      selected: selectedIds.has(node.id),
    }));
    nodesRef.current = next;
    setNodes(next);
    setHasCanvasSelectionIntent(true);
    setLiveMessage(
      selectedIds.size
        ? `已选择 ${selectedIds.size} 个画布节点；再次点击可取消选择。`
        : "已清除画布节点选择。",
    );
    return willSelect;
  }, []);

  const clearCanvasSelection = useCallback(() => {
    selectedNodeIdsRef.current = new Set();
    const next = nodesRef.current.map((node) => ({
      ...node,
      selected: false,
    }));
    nodesRef.current = next;
    setNodes(next);
    setHasCanvasSelectionIntent(true);
    setFocusedNodeId(null);
    setRelationshipFocusNodeId(null);
    setLiveMessage("已清除画布节点选择。");
  }, []);

  const externalSelectionKey = [...externalSelectedNodeIds]
    .sort()
    .join("\u0000");
  useEffect(() => {
    if (previousExternalSelectionRef.current === externalSelectionKey) return;
    previousExternalSelectionRef.current = externalSelectionKey;
    if (canvasActivationPendingRef.current) return;
    selectedNodeIdsRef.current = new Set();
    const next = nodesRef.current.map((node) => ({
      ...node,
      selected: false,
    }));
    nodesRef.current = next;
    setNodes(next);
    setHasCanvasSelectionIntent(false);
  }, [externalSelectionKey]);

  const startLayoutDrag: OnNodeDrag<WorkbenchFlowNode> = useCallback(() => {
    dragStartFrameRef.current = frameFromNodes(
      nodesRef.current,
      viewportRef.current,
    );
  }, []);

  const moveConnectedNodes: OnNodeDrag<WorkbenchFlowNode> = useCallback(
    (_event, draggedNode, movedNodes) => {
      if (!elasticConnectedDrag || !dragStartFrameRef.current) return;
      const excludedNodeIds = new Set(
        [
          ...movedNodes.map((node) => node.id),
          ...selectedNodeIdsRef.current,
        ].filter((nodeId) => nodeId !== draggedNode.id),
      );
      const positions = elasticRelationshipDragPositions(
        dragStartFrameRef.current.positions,
        positionsFromNodes(nodesRef.current),
        sceneEdges,
        draggedNode.id,
        draggedNode.position,
        excludedNodeIds,
      );
      const next = nodesRef.current.map((node) => ({
        ...node,
        position: positions[node.id] ?? node.position,
      }));
      elasticFollowerCountRef.current = Object.keys(positions).filter(
        (nodeId) => {
          const initialPosition = dragStartFrameRef.current?.positions[nodeId];
          const nextPosition = positions[nodeId];
          return Boolean(
            nodeId !== draggedNode.id &&
              !excludedNodeIds.has(nodeId) &&
              initialPosition &&
              nextPosition &&
              (nextPosition.x !== initialPosition.x ||
                nextPosition.y !== initialPosition.y),
          );
        },
      ).length;
      nodesRef.current = next;
      setNodes(next);
    },
    [elasticConnectedDrag, sceneEdges],
  );

  const stopLayoutDrag: OnNodeDrag<WorkbenchFlowNode> = useCallback(
    (_event, _node, movedNodes) => {
      const movedById = new Map(movedNodes.map((node) => [node.id, node]));
      const finalNodes = nodesRef.current.map(
        (node) => movedById.get(node.id) ?? node,
      );
      nodesRef.current = finalNodes;
      setNodes(finalNodes);
      const before = dragStartFrameRef.current;
      dragStartFrameRef.current = null;
      if (before) {
        commitFrame(
          before,
          frameFromNodes(finalNodes, viewportRef.current),
        );
      }
      persist(finalNodes);
      const followerCount = elasticFollowerCountRef.current;
      elasticFollowerCountRef.current = 0;
      setLiveMessage(
        followerCount
          ? `已移动节点，并带动 ${followerCount} 个关联节点。`
          : movedNodes.length > 1
            ? `已移动 ${movedNodes.length} 个画布节点。`
            : "已移动画布节点。",
      );
    },
    [commitFrame, persist],
  );

  const stopSelectionDrag = useCallback(
    (_event: React.MouseEvent, movedNodes: WorkbenchFlowNode[]) => {
      stopLayoutDrag(_event.nativeEvent, movedNodes[0], movedNodes);
    },
    [stopLayoutDrag],
  );

  const startSelectionDrag = useCallback(() => {
    elasticFollowerCountRef.current = 0;
    dragStartFrameRef.current = frameFromNodes(
      nodesRef.current,
      viewportRef.current,
    );
  }, []);

  const anchorNodeIds = useMemo(() => {
    const ids = new Set(
      hasCanvasSelectionIntent ? [] : externalSelectedNodeIds,
    );
    nodes.forEach((node) => {
      if (node.selected) ids.add(node.id);
    });
    if (hoveredNodeId) ids.add(hoveredNodeId);
    if (focusedNodeId) ids.add(focusedNodeId);
    if (focusDirectRelationsOnClick && relationshipFocusNodeId) {
      ids.add(relationshipFocusNodeId);
    }
    return ids;
  }, [
    externalSelectedNodeIds,
    focusedNodeId,
    hasCanvasSelectionIntent,
    hoveredNodeId,
    focusDirectRelationsOnClick,
    nodes,
    relationshipFocusNodeId,
  ]);

  const relatedNodeIds = useMemo(() => {
    const ids = new Set(anchorNodeIds);
    sceneEdges.forEach((edge) => {
      if (anchorNodeIds.has(edge.source)) ids.add(edge.target);
      if (anchorNodeIds.has(edge.target)) ids.add(edge.source);
    });
    return ids;
  }, [anchorNodeIds, sceneEdges]);

  const relationshipFocusNodeIds = useMemo(() => {
    if (!focusDirectRelationsOnClick || !relationshipFocusNodeId) {
      return new Set<string>();
    }
    const ids = new Set([relationshipFocusNodeId]);
    sceneEdges.forEach((edge) => {
      if (edge.source === relationshipFocusNodeId) ids.add(edge.target);
      if (edge.target === relationshipFocusNodeId) ids.add(edge.source);
    });
    return ids;
  }, [focusDirectRelationsOnClick, relationshipFocusNodeId, sceneEdges]);
  const hasRelationshipFocus = relationshipFocusNodeIds.size > 0;

  const renderedNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          active: anchorNodeIds.has(node.id),
          dimmed:
            hasRelationshipFocus && !relationshipFocusNodeIds.has(node.id),
          selected:
            Boolean(node.selected) ||
            (!hasCanvasSelectionIntent &&
              externalSelectedNodeIds.includes(node.id)),
          related: relatedNodeIds.has(node.id),
          onActivate: () => {
            if (tool === "select") {
              const selected = toggleNodeSelection(node.id);
              if (selected && node.data.selectableId) {
                canvasActivationPendingRef.current = true;
                onActivateNode(node.data.selectableId);
                window.requestAnimationFrame(() => {
                  canvasActivationPendingRef.current = false;
                });
              }
            }
          },
          onFocusChange: (focused: boolean) =>
            setFocusedNodeId(focused ? node.id : null),
        },
      })),
    [
      anchorNodeIds,
      externalSelectedNodeIds,
      hasCanvasSelectionIntent,
      hasRelationshipFocus,
      nodes,
      onActivateNode,
      relatedNodeIds,
      relationshipFocusNodeIds,
      toggleNodeSelection,
      tool,
    ],
  );

  const renderedEdges = useMemo<Edge[]>(
    () =>
      sceneEdges.map((edge, index) => {
        const relationshipFocusEdge = Boolean(
          relationshipFocusNodeId &&
            (edge.source === relationshipFocusNodeId ||
              edge.target === relationshipFocusNodeId),
        );
        const active = hasRelationshipFocus
          ? relationshipFocusEdge
          : anchorNodeIds.has(edge.source) ||
            anchorNodeIds.has(edge.target) ||
            edge.id === activeEdgeId;
        const dimmed = hasRelationshipFocus && !relationshipFocusEdge;
        if (onActivateEdge && identity.view !== "relations") {
          return {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            type: "casefileEdge",
            selectable: false,
            focusable: false,
            interactionWidth: 28,
            data: {
              label: edge.label ?? "",
              ariaLabel: edge.ariaLabel ?? edge.label ?? edge.id,
              effect: edge.kind ?? "",
              active,
              onActivate: () => onActivateEdge(edge.id),
            },
          };
        }
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          className: active ? styles.canvasEdgeActive : styles.canvasEdge,
          selectable: false,
          focusable: false,
          label:
            identity.view === "relations" || active ? edge.label : undefined,
          labelShowBg: true,
          labelBgPadding: [6, 3],
          labelBgBorderRadius: 1,
          labelBgStyle: {
            fill:
              identity.view === "relations"
                ? "rgba(9, 11, 15, 0.88)"
                : "rgba(251, 250, 246, 0.96)",
            stroke:
              identity.view === "relations"
                ? "transparent"
                : "rgba(199, 139, 60, 0.3)",
          },
          labelStyle: {
            fill:
              identity.view === "relations"
                ? edge.accent ?? "#94a3b8"
                : "var(--ink-muted)",
            fontFamily: '"Cascadia Mono", Consolas, monospace',
            fontSize: identity.view === "relations" ? 12 : 9,
            opacity: dimmed ? 0.1 : 1,
          },
          markerEnd:
            identity.view === "relations" &&
            edge.direction !== "undirected" &&
            !dimmed
              ? {
                  type: MarkerType.ArrowClosed,
                  color: edge.accent ?? "#60a5fa",
                  width: 14,
                  height: 14,
                }
              : undefined,
          markerStart:
            identity.view === "relations" &&
            edge.direction === "bidirectional" &&
            !dimmed
              ? {
                  type: MarkerType.ArrowClosed,
                  color: edge.accent ?? "#60a5fa",
                  width: 14,
                  height: 14,
                }
              : undefined,
          style: {
            ...edgeStyle(
              edge.kind,
              active,
              edge.accent,
              edge.strokeDasharray,
            ),
            ...(dimmed ? { opacity: 0.1, filter: "none" } : {}),
          },
          data: {
            flowing: active && !dimmed && edge.direction === "directed",
            labelFraction: 0.38 + (index % 5) * 0.06,
          },
          type: identity.view === "relations" ? "relationshipEdge" : "default",
        };
      }),
    [
      activeEdgeId,
      anchorNodeIds,
      hasRelationshipFocus,
      identity.view,
      onActivateEdge,
      relationshipFocusNodeId,
      sceneEdges,
    ],
  );

  const applyFrame = useCallback(
    (frame: CanvasHistoryFrame) => {
      const nextNodes = applyPositions(nodesRef.current, frame.positions);
      nodesRef.current = nextNodes;
      setNodes(nextNodes);
      viewportRef.current = frame.viewport;
      setViewportState(frame.viewport);
      void instance?.setViewport(frame.viewport, { duration: 0 });
      persist(nextNodes, frame.viewport);
    },
    [instance, persist],
  );

  function undo() {
    const previous = historyRef.current.past.at(-1);
    if (!previous) return;
    const current = frameFromNodes(nodesRef.current, viewportRef.current);
    setHistoryState({
      past: historyRef.current.past.slice(0, -1),
      future: [current, ...historyRef.current.future],
    });
    applyFrame(previous);
    setLiveMessage("已撤销上一次画布布局修改。");
  }

  function redo() {
    const next = historyRef.current.future[0];
    if (!next) return;
    const current = frameFromNodes(nodesRef.current, viewportRef.current);
    setHistoryState({
      past: [...historyRef.current.past, current].slice(-50),
      future: historyRef.current.future.slice(1),
    });
    applyFrame(next);
    setLiveMessage("已重做画布布局修改。");
  }

  function relayout() {
    const before = frameFromNodes(nodesRef.current, viewportRef.current);
    const nextNodes = applyPositions(nodesRef.current, automaticPositions);
    nodesRef.current = nextNodes;
    setNodes(nextNodes);
    const after = frameFromNodes(nextNodes, viewportRef.current);
    commitFrame(before, after);
    persist(nextNodes);
    setLiveMessage("已按当前图谱结构重新整理画布。");
    if (!nextNodes.length) return;
    void instance
      ?.fitView({ padding: 0.18, minZoom: 0.12, maxZoom: 1.2, duration: 0 })
      .then(() => {
        const fitted = instance.getViewport();
        viewportRef.current = fitted;
        setViewportState(fitted);
        persist(nextNodes, fitted);
      });
  }

  async function fitAll() {
    if (!instance || !nodesRef.current.length) return;
    await instance.fitView({
      padding: 0.18,
      minZoom: 0.12,
      maxZoom: 1.2,
      duration: 0,
    });
    const fitted = instance.getViewport();
    viewportRef.current = fitted;
    setViewportState(fitted);
    persist(nodesRef.current, fitted);
    setLiveMessage("已将全部画布内容适配到当前视口。");
  }

  function zoomTo(nextZoom: number) {
    const zoom = Math.min(2.5, Math.max(0.12, nextZoom));
    viewportRef.current = { ...viewportRef.current, zoom };
    setViewportState(viewportRef.current);
    void instance?.zoomTo(zoom, { duration: 0 });
  }

  function resetViewport() {
    const reset = { x: 0, y: 0, zoom: 1 };
    viewportRef.current = reset;
    setViewportState(reset);
    void instance?.setViewport(reset, { duration: 0 });
    persist(nodesRef.current, reset);
    setLiveMessage("画布视口已恢复到 100%。");
  }

  async function toggleFullscreen() {
    const shell = shellRef.current;
    if (!shell) return;
    try {
      if (document.fullscreenElement === shell) {
        await document.exitFullscreen();
        setLiveMessage("已退出画布全屏。");
      } else if (shell.requestFullscreen) {
        await shell.requestFullscreen();
        setLiveMessage("画布已进入全屏；按 Esc 可以退出。");
      } else {
        setWarning("当前浏览器不支持画布全屏；仍可使用适配与缩放查看。");
      }
    } catch {
      setWarning("画布未能进入全屏，请检查浏览器权限后重试。");
    }
  }

  return (
    <div
      className={styles.canvasKernelShell}
      data-fullscreen={isFullscreen}
      data-motion={motionEnabled ? "running" : "paused"}
      data-scene={identity.view}
      data-layout-key={workbenchCanvasLayoutStorageKey(identity)}
      ref={shellRef}
    >
      <ReactFlow<WorkbenchFlowNode, Edge>
        aria-label={ariaLabel}
        className={styles.canvasKernel}
        data-scene={identity.view}
        data-tool={tool}
        deleteKeyCode={null}
        edgeTypes={edgeTypes}
        edges={renderedEdges}
        edgesFocusable={false}
        edgesReconnectable={false}
        elementsSelectable={tool === "select"}
        elevateEdgesOnSelect={false}
        fitView={false}
        maxZoom={2.5}
        minZoom={0.12}
        multiSelectionKeyCode={null}
        nodeTypes={nodeTypes}
        nodes={renderedNodes}
        nodesConnectable={false}
        nodesDraggable={tool === "select"}
        nodesFocusable={false}
        onInit={setInstance}
        onEdgeClick={(event, edge) => {
          if (identity.view !== "relations" || !onActivateEdge) return;
          event.stopPropagation();
          onActivateEdge(edge.id);
        }}
        onMove={(_event, nextViewport) => {
          viewportRef.current = nextViewport;
          setViewportState(nextViewport);
        }}
        onMoveEnd={(_event, nextViewport) => {
          viewportRef.current = nextViewport;
          setViewportState(nextViewport);
          persist(nodesRef.current, nextViewport);
        }}
        onNodeClick={(event, node) => {
          if (tool !== "select") return;
          if (focusDirectRelationsOnClick) {
            setRelationshipFocusNodeId((current) =>
              current === node.id ? null : node.id,
            );
          }
          if (
            requireModifierForNodeSelection &&
            !event.ctrlKey &&
            !event.metaKey
          ) {
            setLiveMessage(
              focusDirectRelationsOnClick
                ? `已聚焦“${node.data.label}”及其直接关系；按住 Ctrl 键点击可打开对象。`
                : "按住 Ctrl 键并左键点击节点，才会选中对象。",
            );
            return;
          }
          const selected = toggleNodeSelection(node.id);
          if (selected && node.data.selectableId) {
            canvasActivationPendingRef.current = true;
            onActivateNode(node.data.selectableId);
            window.requestAnimationFrame(() => {
              canvasActivationPendingRef.current = false;
            });
          }
        }}
        onNodeDragStart={startLayoutDrag}
        onNodeDrag={moveConnectedNodes}
        onNodeDragStop={stopLayoutDrag}
        onNodeMouseEnter={(_event, node) => setHoveredNodeId(node.id)}
        onNodeMouseLeave={() => setHoveredNodeId(null)}
        onNodesChange={onNodesChange}
        onPaneClick={clearCanvasSelection}
        onSelectionEnd={() => {
          selectionBoxActiveRef.current = false;
          const selectedIds = new Set(
            nodesRef.current
              .filter((node) => node.selected)
              .map((node) => node.id),
          );
          selectedNodeIdsRef.current = selectedIds;
          setHasCanvasSelectionIntent(true);
          setLiveMessage(
            selectedIds.size
              ? `已框选 ${selectedIds.size} 个画布节点。`
              : "框选范围内没有画布节点。",
          );
        }}
        onSelectionStart={() => {
          selectionBoxActiveRef.current = true;
        }}
        onSelectionDragStart={startSelectionDrag}
        onSelectionDragStop={stopSelectionDrag}
        panOnDrag={tool === "pan"}
        panOnScroll={tool === "pan"}
        preventScrolling
        proOptions={{ hideAttribution: true }}
        selectionMode={SelectionMode.Partial}
        selectionOnDrag={tool === "select"}
        zoomOnDoubleClick={false}
      />
      {identity.view === "relations" ? <button
        aria-label="关系图动效" aria-pressed={motionEnabled}
        className={styles.relationshipMotionToggle}
        onClick={() => setMotionEnabled((enabled) => !enabled)} type="button"
      ><span aria-hidden="true" />动效{motionEnabled ? "开启" : "暂停"}</button> : null}
      {!sceneNodes.length && emptyHint ? (
        <div className={styles.canvasEmptyHint} role="note">
          {emptyHint}
        </div>
      ) : null}
      {nodeLegend?.length ? (
        <div aria-label="节点类型图例" className={styles.canvasTypeLegend}>
          {nodeLegend.map((item) => (
            <span
              className={styles.canvasTypeLegendItem}
              key={item.id}
              style={{ "--canvas-node-accent": item.accent } as CSSProperties}
            >
              {item.label}
            </span>
          ))}
        </div>
      ) : null}
      {legend ? (
        <div aria-hidden="true" className={styles.canvasLegend}>
          {legend}
        </div>
      ) : null}
      <div
        aria-label="画布控制"
        className={styles.canvasOverlayControls}
        role="group"
      >
        <CanvasKernelControls
          canRedo={history.future.length > 0}
          canUndo={history.past.length > 0}
          isFullscreen={isFullscreen}
          onFit={() => void fitAll()}
          onRedo={redo}
          onRelayout={relayout}
          onResetViewport={resetViewport}
          onToolChange={setTool}
          onToggleFullscreen={() => void toggleFullscreen()}
          onUndo={undo}
          onZoomIn={() => zoomTo(viewport.zoom + 0.25)}
          onZoomOut={() => zoomTo(viewport.zoom - 0.25)}
          tool={tool}
          zoom={viewport.zoom}
        />
      </div>
      {warning ? (
        <p className={styles.canvasStorageNotice} role="status">
          {warning}
        </p>
      ) : null}
      <span aria-live="polite" className={styles.srOnly}>
        {liveMessage}
      </span>
    </div>
  );
}
