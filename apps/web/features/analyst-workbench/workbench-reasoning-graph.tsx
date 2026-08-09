import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type ReasoningOutcome,
  type ReasoningPath,
  type WorkbenchSeed,
} from "./analyst-fixture";
import styles from "./analyst-workbench.module.css";
import {
  type CanvasTool,
  CanvasTools,
  ZoomControls,
} from "./workbench-canvas-controls";
import { clamp } from "./workbench-geometry";
import { reasoningOutcomeLabels } from "./workbench-presenters";

interface ReasoningPoint {
  x: number;
  y: number;
}

type ReasoningNodeKind = "evidence" | "reason" | "hypothesis";

interface ReasoningCanvasNode {
  id: string;
  kind: ReasoningNodeKind;
  caption: string;
  label: string;
  outcome?: ReasoningOutcome;
  objectId?: string;
}

interface ReasoningCanvasEdge {
  key: string;
  from: string;
  to: string;
  kind: "evidence" | "chain" | ReasoningOutcome;
}

interface ReasoningCanvasLayout {
  nodes: ReasoningCanvasNode[];
  edges: ReasoningCanvasEdge[];
  initialPositions: Record<string, ReasoningPoint>;
}

// 所有推理路径合并为一张 100×100 逻辑坐标画布：结论收束在顶部、
// 推理步骤按路径分列居中、证据共享并铺在底部；边由引用关系生成。
function buildReasoningCanvas(
  paths: ReasoningPath[],
): ReasoningCanvasLayout {
  const nodes: ReasoningCanvasNode[] = [];
  const edges: ReasoningCanvasEdge[] = [];
  const initialPositions: Record<string, ReasoningPoint> = {};
  const pathCount = Math.max(paths.length, 1);
  const evidenceIds = [...new Set(paths.flatMap((path) => path.evidenceIds))];

  paths.forEach((path, pathIndex) => {
    const columnX = ((pathIndex + 0.5) * 100) / pathCount;
    const conclusionId = `conclusion-${path.id}`;
    nodes.push({
      id: conclusionId,
      kind: "hypothesis",
      caption: reasoningOutcomeLabels[path.outcome],
      label: path.conclusion,
      outcome: path.outcome,
      objectId: path.hypothesisId,
    });
    initialPositions[conclusionId] = { x: columnX, y: 10 };
    const stepCount = Math.max(path.steps.length, 1);
    path.steps.forEach((step, stepIndex) => {
      const stepId = `step-${step.id}`;
      nodes.push({
        id: stepId,
        kind: "reason",
        caption: step.verb,
        label: step.claim,
      });
      initialPositions[stepId] = {
        x: columnX,
        y:
          stepCount === 1
            ? 45
            : 32 + (stepIndex * 26) / (stepCount - 1),
      };
      for (const evidenceId of step.evidenceIds) {
        if (!evidenceIds.includes(evidenceId)) continue;
        edges.push({
          key: `${evidenceId}-${step.id}`,
          from: evidenceId,
          to: stepId,
          kind: "evidence",
        });
      }
      if (stepIndex > 0) {
        const previous = path.steps[stepIndex - 1];
        edges.push({
          key: `${previous.id}-${step.id}`,
          from: `step-${previous.id}`,
          to: stepId,
          kind: "chain",
        });
      }
    });
    const lastStep = path.steps[path.steps.length - 1];
    if (lastStep) {
      edges.push({
        key: `${lastStep.id}-${path.id}`,
        from: `step-${lastStep.id}`,
        to: conclusionId,
        kind: path.outcome,
      });
    }
  });

  evidenceIds.forEach((id, index) => {
    initialPositions[id] = {
      x: ((index + 0.5) * 100) / Math.max(evidenceIds.length, 1),
      y: 84,
    };
    nodes.push({
      id,
      kind: "evidence",
      caption: "证据",
      label: id,
      objectId: id,
    });
  });

  return { nodes, edges, initialPositions };
}

export function ReasoningGraphView({
  seed,
  onSelectObject,
}: {
  seed: WorkbenchSeed;
  onSelectObject: (objectId: string) => void;
}) {
  const layout = useMemo(
    () => buildReasoningCanvas(seed.reasoningPaths),
    [seed.reasoningPaths],
  );
  const [zoom, setZoom] = useState(1);
  // 布局随候选种子变化：渲染期直接调整 state（官方推荐模式），
  // 拖动产生的位置修改只在同一布局内保留。
  const [canvasState, setCanvasState] = useState(() => ({
    layout,
    positions: layout.initialPositions,
  }));
  if (canvasState.layout !== layout) {
    setCanvasState({ layout, positions: layout.initialPositions });
  }
  const { positions, setPositions } = useMemo(
    () => ({
      positions: canvasState.positions,
      setPositions: (
        updater:
          | Record<string, ReasoningPoint>
          | ((
              previous: Record<string, ReasoningPoint>,
            ) => Record<string, ReasoningPoint>),
      ) =>
        setCanvasState((previous) => ({
          ...previous,
          positions:
            typeof updater === "function"
              ? updater(previous.positions)
              : updater,
        })),
    }),
    [canvasState.positions],
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

  const evidenceById = useMemo(
    () => new Map(seed.caseObjects.map((object) => [object.id, object])),
    [seed.caseObjects],
  );

  function startNodeDrag(event: ReactPointerEvent<HTMLElement>, id: string) {
    if (tool !== "select") return;
    dragRef.current = {
      mode: "node",
      id,
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

  function selectNode(node: ReasoningCanvasNode) {
    if (tool !== "select") return;
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    if (node.objectId) onSelectObject(node.objectId);
  }

  const visibleEdges = layout.edges.filter(
    (edge) => positions[edge.from] && positions[edge.to],
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
      {layout.nodes.length ? (
        <>
          <div className={styles.zoomViewport}>
            <div className={styles.zoomStage} style={{ zoom }}>
              <div
                className={styles.panStage}
                style={{ transform: `translate(${pan.x}px, ${pan.y}px)` }}
              >
                <div
                  aria-label="推理画布"
                  className={styles.reasoningBoard}
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
                    className={styles.reasoningEdges}
                    preserveAspectRatio="none"
                    viewBox="0 0 100 100"
                  >
                    {visibleEdges.map((edge) => {
                      const from = positions[edge.from];
                      const to = positions[edge.to];
                      if (!from || !to) return null;
                      return (
                        <g data-kind={edge.kind} key={edge.key}>
                          <line
                            x1={from.x}
                            x2={to.x}
                            y1={from.y}
                            y2={to.y}
                          />
                        </g>
                      );
                    })}
                  </svg>
                  {layout.nodes.map((node) => {
                    const position = positions[node.id];
                    if (!position) return null;
                    const style = {
                      "--node-x": `${position.x}%`,
                      "--node-y": `${position.y}%`,
                    } as CSSProperties;
                    if (node.kind === "reason") {
                      return (
                        <div
                          aria-label={`推理：${node.caption}，${node.label}`}
                          className={styles.reasoningNode}
                          data-kind="reason"
                          key={node.id}
                          onPointerDown={(event) =>
                            startNodeDrag(event, node.id)
                          }
                          role="img"
                          style={style}
                        >
                          <small>{node.caption}</small>
                          <strong>{node.label}</strong>
                        </div>
                      );
                    }
                    const label =
                      node.kind === "hypothesis"
                        ? node.label
                        : (evidenceById.get(node.id)?.label ?? node.label);
                    return (
                      <button
                        aria-label={`${node.kind === "hypothesis" ? "结论" : "证据"}：${label}`}
                        className={styles.reasoningNode}
                        data-kind={node.kind}
                        data-outcome={node.outcome}
                        key={node.id}
                        onClick={() => selectNode(node)}
                        onPointerDown={(event) =>
                          startNodeDrag(event, node.id)
                        }
                        style={style}
                        type="button"
                      >
                        <small>{node.caption}</small>
                        <strong>{label}</strong>
                      </button>
                    );
                  })}
                  <div aria-hidden="true" className={styles.reasoningLegend}>
                    <span data-kind="evidence">证据引用</span>
                    <span data-kind="chain">推理推进</span>
                    <span data-kind="supported">支持</span>
                    <span data-kind="contested">竞争</span>
                    <span data-kind="eliminated">排除</span>
                  </div>
                </div>
              </div>
            </div>
            <div
              aria-label="画布控制"
              className={styles.canvasOverlayControls}
              role="group"
            >
              <CanvasTools onToolChange={setTool} tool={tool} />
              <ZoomControls onZoomChange={setZoom} zoom={zoom} />
            </div>
          </div>
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
        </>
      ) : (
        <p className={styles.viewNote}>候选没有可展示的推理路径。</p>
      )}
    </section>
  );
}
