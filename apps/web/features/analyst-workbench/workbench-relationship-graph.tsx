import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  getObject,
  objectKindLabels,
  type WorkbenchSeed,
} from "./analyst-fixture";
import styles from "./analyst-workbench.module.css";
import {
  type CanvasTool,
  CanvasTools,
  ZoomControls,
} from "./workbench-canvas-controls";
import { clamp } from "./workbench-geometry";
import type { WorkbenchGraphNode } from "./workbench-real-data";

interface GraphPoint {
  x: number;
  y: number;
}

const graphReferenceLabels: Record<string, string> = {
  casefile: "卷宗",
  resolution_spec: "核心问题",
  entity: "实体",
  information_unit: "信息",
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

export function RelationshipGraph({
  seed,
  selectedObjectId,
  relatedObjectIds,
  onSelectObject,
  compact = false,
}: {
  seed: WorkbenchSeed;
  selectedObjectId: string | null;
  relatedObjectIds: string[];
  onSelectObject: (objectId: string) => void;
  compact?: boolean;
}) {
  const graphNodes = seed.graphNodes;
  const [zoom, setZoom] = useState(1);
  const initialPositions = useMemo(
    () =>
      Object.fromEntries(
        graphNodes.map((node) => [
          node.objectId,
          { x: node.x, y: node.y },
        ]),
      ),
    [graphNodes],
  );
  // 布局随候选种子变化：渲染期直接调整 state，拖动位置只在同一布局内保留。
  const [canvasState, setCanvasState] = useState(() => ({
    graphNodes,
    positions: initialPositions,
  }));
  if (canvasState.graphNodes !== graphNodes) {
    setCanvasState({ graphNodes, positions: initialPositions });
  }
  const positions = canvasState.positions;
  const selectedGraphNodeIds = useMemo(
    () =>
      new Set(
        graphNodes.flatMap((node) => {
          const mappedNode = node as typeof node & Partial<WorkbenchGraphNode>;
          const selectableId =
            getObject(seed, node.objectId)?.id ??
            mappedNode.directoryObjectId ??
            null;
          return selectableId === selectedObjectId ? [node.objectId] : [];
        }),
      ),
    [graphNodes, seed, selectedObjectId],
  );
  const directlyRelatedNodeIds = useMemo(() => {
    const nodeIds = new Set(selectedGraphNodeIds);
    for (const edge of seed.graphEdges) {
      if (selectedGraphNodeIds.has(edge.from)) nodeIds.add(edge.to);
      if (selectedGraphNodeIds.has(edge.to)) nodeIds.add(edge.from);
    }
    return nodeIds;
  }, [seed.graphEdges, selectedGraphNodeIds]);
  const setPositions = useMemo(
    () =>
      (
        updater:
          | Record<string, GraphPoint>
          | ((previous: Record<string, GraphPoint>) => Record<string, GraphPoint>),
      ) =>
        setCanvasState((previous) => ({
          ...previous,
          positions:
            typeof updater === "function"
              ? updater(previous.positions)
              : updater,
        })),
    [],
  );
  const boardRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    mode: "node" | "pan";
    id?: string;
    startX: number;
    startY: number;
    startPan?: { x: number; y: number };
    moved: boolean;
  } | null>(null);
  const suppressClickRef = useRef(false);
  const [tool, setTool] = useState<CanvasTool>("select");
  const [pan, setPan] = useState({ x: 0, y: 0 });

  function startNodeDrag(
    event: ReactPointerEvent<HTMLElement>,
    objectId: string,
  ) {
    if (tool !== "select") return;
    dragRef.current = {
      mode: "node",
      id: objectId,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function startPanDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (tool !== "pan") return;
    dragRef.current = {
      mode: "pan",
      startX: event.clientX,
      startY: event.clientY,
      startPan: pan,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const board = boardRef.current;
    if (!drag || !board) return;
    if (
      !drag.moved &&
      Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4
    ) {
      drag.moved = true;
    }
    if (!drag.moved) return;
    if (drag.mode === "node") {
      const rect = board.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      const x = clamp(((event.clientX - rect.left) / rect.width) * 100, 6, 94);
      const y = clamp(((event.clientY - rect.top) / rect.height) * 100, 6, 94);
      setPositions((previous) => ({ ...previous, [drag.id!]: { x, y } }));
    } else if (drag.startPan) {
      setPan({
        x: clamp(drag.startPan.x + (event.clientX - drag.startX), -600, 600),
        y: clamp(drag.startPan.y + (event.clientY - drag.startY), -600, 600),
      });
    }
  }

  function endDrag() {
    if (dragRef.current?.moved) suppressClickRef.current = true;
    dragRef.current = null;
  }

  function selectNode(objectId: string) {
    if (tool !== "select") return;
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    onSelectObject(objectId);
  }

  const visibleNodeIds = new Set(graphNodes.map((node) => node.objectId));
  const board = (
    <div
      aria-describedby="relationship-graph-summary"
      aria-label="事件关系图"
      className={compact ? styles.graphBoard : styles.relationsBoard}
      data-tool={tool}
      onPointerCancel={endDrag}
      onPointerDown={startPanDrag}
      onPointerMove={moveDrag}
      onPointerUp={endDrag}
      ref={boardRef}
      role="group"
    >
      <svg
        aria-hidden="true"
        className={styles.graphEdges}
        preserveAspectRatio="none"
        viewBox="0 0 100 100"
      >
        {seed.graphEdges.map((edge, edgeIndex) => {
          const from = positions[edge.from];
          const to = positions[edge.to];
          if (!from || !to) return null;
          const active = compact
            ? relatedObjectIds.includes(edge.from) ||
              relatedObjectIds.includes(edge.to)
            : selectedGraphNodeIds.has(edge.from) ||
              selectedGraphNodeIds.has(edge.to);
          return (
            <g
              data-active={active}
              key={`${(edge as { id?: string }).id ?? `${edge.from}-${edge.to}-${edge.label}`}-${edgeIndex}`}
            >
              <line x1={from.x} x2={to.x} y1={from.y} y2={to.y} />
              {!compact ? (
                <text
                  x={(from.x + to.x) / 2}
                  y={(from.y + to.y) / 2 - 1.5}
                >
                  {edge.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {graphNodes.map((node) => {
        const mappedNode = node as typeof node & Partial<WorkbenchGraphNode>;
        const object = getObject(seed, node.objectId);
        const position = positions[node.objectId];
        if (!position) return null;
        const selectableId = object?.id ?? mappedNode.directoryObjectId ?? null;
        const selected = selectableId === selectedObjectId;
        const related = compact
          ? relatedObjectIds.includes(node.objectId) ||
            (selectableId ? relatedObjectIds.includes(selectableId) : false)
          : directlyRelatedNodeIds.has(node.objectId);
        const kind = object?.kind ?? mappedNode.kind ?? "unknown";
        const label = object?.label ?? mappedNode.label ?? node.objectId;
        const style = {
          "--node-x": `${position.x}%`,
          "--node-y": `${position.y}%`,
        } as CSSProperties;
        return (
          <button
            aria-pressed={selected}
            className={styles.graphNode}
            data-kind={kind}
            data-related={related}
            disabled={!selectableId}
            key={node.objectId}
            onClick={() => { if (selectableId) selectNode(selectableId); }}
            onPointerDown={(event) => startNodeDrag(event, node.objectId)}
            style={style}
            type="button"
          >
            <small>{object ? objectKindLabels[object.kind] : graphReferenceLabels[String(kind)] ?? "引用"}</small>
            <strong>{label}</strong>
          </button>
        );
      })}
    </div>
  );
  const panStage = (
    <div
      className={styles.panStage}
      style={{ transform: `translate(${pan.x}px, ${pan.y}px)` }}
    >
      {board}
    </div>
  );

  if (compact) {
    return (
      <section className={styles.graphPanel} data-compact="true">
        <div className={styles.graphHeading}>
          <div>
            <span>同步关系图</span>
            <strong>事件、人物与证据</strong>
          </div>
          <small>{visibleNodeIds.size} NODES</small>
        </div>
        <p className={styles.srOnly} id="relationship-graph-summary">
          {seed.caseMeta.relationshipSummary}
        </p>
        <div className={styles.zoomViewport}>
          <div className={styles.zoomStage} style={{ zoom }}>
            {panStage}
          </div>
          <div
            aria-label="画布控制"
            className={styles.canvasOverlayControls}
            role="group"
          >
            <span className={styles.graphLegend}>
              <i /> 当前关联
            </span>
            <CanvasTools onToolChange={setTool} tool={tool} />
            <ZoomControls onZoomChange={setZoom} zoom={zoom} />
          </div>
        </div>
        <span className={styles.srOnly}>
          {visibleNodeIds.size} 个可访问节点
        </span>
      </section>
    );
  }

  const graphNodeById = new Map(
    graphNodes.map((node) => [
      node.objectId,
      (node as typeof node & Partial<WorkbenchGraphNode>).label ??
        getObject(seed, node.objectId)?.label ??
        node.objectId,
    ]),
  );

  return (
    <section className={styles.relationsView} aria-labelledby="relations-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>同步关系图</span>
          <h2 id="relations-heading">事件、人物与证据</h2>
        </div>
        <div className={styles.sectionTrailing}>
          <small>{visibleNodeIds.size} NODES</small>
        </div>
      </header>
      <p className={styles.srOnly} id="relationship-graph-summary">
        {seed.caseMeta.relationshipSummary}
      </p>
      <div className={styles.zoomViewport}>
        <div className={styles.zoomStage} style={{ zoom }}>
          {panStage}
        </div>
        <div
          aria-label="画布控制"
          className={styles.canvasOverlayControls}
          role="group"
        >
          <span className={styles.graphLegend}>
            <i /> 当前关联
          </span>
          <CanvasTools onToolChange={setTool} tool={tool} />
          <ZoomControls onZoomChange={setZoom} zoom={zoom} />
        </div>
      </div>
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
