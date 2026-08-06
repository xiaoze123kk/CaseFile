"use client";

import Link from "next/link";
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  defaultWorkbenchSeed,
  getEvent,
  getObject,
  type InspectorTab,
  type IssueStatus,
  type ObjectKind,
  objectKindLabels,
  type PrototypeDraftCandidate,
  type PrototypeReasoningPath,
  type PrototypeWorkbenchSeed,
  type ReasoningOutcome,
  viewOptions,
  type WorkbenchView,
} from "./analyst-fixture";
import {
  type PrototypeDraftCandidateStatus,
  useDemoPrototype,
} from "@/features/demo-prototype/demo-prototype-provider";
import styles from "./analyst-workbench.module.css";
import relayStyles from "./prototype-relay.module.css";

type MobileRegion = "objects" | "canvas" | "inspector" | "sources";
type DrawerTab = "audio" | "transcript" | "logs" | "retrieval";
type ValidationPhase = "idle" | "recomputing" | "running";

interface AuditEntry {
  id: string;
  time: string;
  actor: string;
  action: string;
  detail: string;
}

function createIssueStatuses(seed: PrototypeWorkbenchSeed) {
  return Object.fromEntries(
    seed.validationIssues.map((issue) => [issue.id, "open"]),
  ) as Record<string, IssueStatus>;
}

const kindOrder: ObjectKind[] = [
  "person",
  "evidence",
  "event",
  "location",
  "hypothesis",
];

const inspectorTabs: Array<{ id: InspectorTab; label: string }> = [
  { id: "issues", label: "验证问题" },
  { id: "sources", label: "引用来源" },
  { id: "patch", label: "补丁审阅" },
  { id: "audit", label: "审计记录" },
];

const mobileRegions: Array<{ id: MobileRegion; label: string }> = [
  { id: "objects", label: "对象" },
  { id: "canvas", label: "主画布" },
  { id: "inspector", label: "检查器" },
  { id: "sources", label: "来源" },
];

const drawerTabs: Array<{ id: DrawerTab; label: string; count?: number }> = [
  { id: "audio", label: "证词录音", count: 1 },
  { id: "transcript", label: "转写文本", count: 3 },
  { id: "logs", label: "模型日志摘要", count: 4 },
  { id: "retrieval", label: "检索命中", count: 3 },
];

function WorkbenchIcon({
  name,
  className,
}: {
  name:
    | "search"
    | "command"
    | "validate"
    | "export"
    | "chevron"
    | "play"
    | "pause"
    | "close"
    | "reset"
    | "chat";
  className?: string;
}) {
  const paths = {
    search: <><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3.5 3.5" /></>,
    command: <><path d="M5 2.5v11M11 2.5v11M2.5 5h11M2.5 11h11" /><circle cx="5" cy="5" r="2.5" /><circle cx="11" cy="11" r="2.5" /></>,
    validate: <><path d="m3 8 3 3 7-7" /><path d="M13 8v5H3V3h6" /></>,
    export: <><path d="M8 10V2m0 0L5 5m3-3 3 3" /><path d="M3 9v4h10V9" /></>,
    chevron: <path d="m5 6 3 3 3-3" />,
    play: <path d="m5 3 8 5-8 5Z" />,
    pause: <><path d="M5 3v10M11 3v10" /></>,
    close: <path d="m3 3 10 10M13 3 3 13" />,
    reset: <><path d="M3 6a5 5 0 1 1 1 5" /><path d="M3 2v4h4" /></>,
    chat: <><path d="M2.5 4.5h11v6.5h-7L3 14v-3h-.5Z" /><path d="M5.5 7h5M5.5 9h3" /></>,
  } as const;

  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      viewBox="0 0 16 16"
    >
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.35">
        {paths[name]}
      </g>
    </svg>
  );
}

function statusLabel(status: IssueStatus) {
  if (status === "patch-ready") return "补丁待审批";
  if (status === "resolved") return "已解决";
  if (status === "exception") return "已知例外";
  return "待处理";
}

function currentClock() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

function FocusTrapDialog({
  open,
  query,
  onQueryChange,
  onClose,
  modalRef,
  inputRef,
  children,
}: {
  open: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  onClose: () => void;
  modalRef: RefObject<HTMLElement | null>;
  inputRef: RefObject<HTMLInputElement | null>;
  children: ReactNode;
}) {
  if (!open) return null;

  return (
    <div className={styles.paletteBackdrop} onMouseDown={onClose} role="presentation">
      <section
        aria-labelledby="command-palette-title"
        aria-modal="true"
        className={styles.palette}
        onMouseDown={(event) => event.stopPropagation()}
        ref={modalRef}
        role="dialog"
      >
        <header className={styles.paletteHeader}>
          <div>
            <span>全局命令</span>
            <strong id="command-palette-title">定位对象、视图或问题</strong>
          </div>
          <button aria-label="关闭命令面板" onClick={onClose} type="button">
            <WorkbenchIcon name="close" />
          </button>
        </header>
        <label className={styles.paletteSearch}>
          <WorkbenchIcon name="search" />
          <span className={styles.srOnly}>搜索命令或对象</span>
          <input
            aria-label="搜索命令或对象"
            autoComplete="off"
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="输入对象、问题、来源或命令…"
            ref={inputRef}
            value={query}
          />
          <kbd>ESC</kbd>
        </label>
        <div className={styles.paletteResults}>{children}</div>
        <footer className={styles.paletteFooter}>
          <span>↑↓ 浏览</span>
          <span>Enter 打开</span>
          <span>Tab 循环焦点</span>
        </footer>
      </section>
    </div>
  );
}

function RelationshipGraph({
  seed,
  selectedObjectId,
  relatedObjectIds,
  onSelectObject,
  compact = false,
}: {
  seed: PrototypeWorkbenchSeed;
  selectedObjectId: string;
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
  const setPositions = useMemo(
    () =>
      (
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
    [],
  );
  const boardRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    id: string;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const suppressClickRef = useRef(false);

  function startDrag(event: ReactPointerEvent<HTMLElement>, objectId: string) {
    dragRef.current = {
      id: objectId,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const board = boardRef.current;
    if (!drag || !board) return;
    const rect = board.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const x = clamp(((event.clientX - rect.left) / rect.width) * 100, 6, 94);
    const y = clamp(((event.clientY - rect.top) / rect.height) * 100, 6, 94);
    if (
      !drag.moved &&
      Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4
    ) {
      drag.moved = true;
    }
    if (drag.moved) {
      setPositions((previous) => ({ ...previous, [drag.id]: { x, y } }));
    }
  }

  function endDrag() {
    if (dragRef.current?.moved) suppressClickRef.current = true;
    dragRef.current = null;
  }

  function selectNode(objectId: string) {
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
      onPointerCancel={endDrag}
      onPointerMove={moveDrag}
      onPointerUp={endDrag}
      ref={boardRef}
      role="group"
    >
      <svg aria-hidden="true" className={styles.graphEdges} preserveAspectRatio="none" viewBox="0 0 100 100">
        {seed.graphEdges.map((edge) => {
          const from = positions[edge.from];
          const to = positions[edge.to];
          if (!from || !to) return null;
          const active = relatedObjectIds.includes(edge.from) || relatedObjectIds.includes(edge.to);
          return (
            <g data-active={active} key={`${edge.from}-${edge.to}`}>
              <line x1={from.x} x2={to.x} y1={from.y} y2={to.y} />
              {!compact ? (
                <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 1.5}>
                  {edge.label}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {graphNodes.map((node) => {
        const object = getObject(seed, node.objectId);
        const position = positions[node.objectId];
        if (!object || !position) return null;
        const selected = object.id === selectedObjectId;
        const related = relatedObjectIds.includes(object.id);
        const style = {
          "--node-x": `${position.x}%`,
          "--node-y": `${position.y}%`,
        } as CSSProperties;
        return (
          <button
            aria-pressed={selected}
            className={styles.graphNode}
            data-kind={object.kind}
            data-related={related}
            key={object.id}
            onClick={() => selectNode(object.id)}
            onPointerDown={(event) => startDrag(event, object.id)}
            style={style}
            type="button"
          >
            <small>{objectKindLabels[object.kind]}</small>
            <strong>{object.label}</strong>
          </button>
        );
      })}
      {!compact ? (
        <span aria-hidden="true" className={styles.relationsLegend}>
          <i /> 当前关联
        </span>
      ) : null}
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
          <span className={styles.graphLegend}>
            <i /> 当前关联
          </span>
        </div>
        <p className={styles.srOnly} id="relationship-graph-summary">
          {seed.caseMeta.relationshipSummary}
        </p>
        {board}
        <span className={styles.srOnly}>{visibleNodeIds.size} 个可访问节点</span>
      </section>
    );
  }

  return (
    <section className={styles.relationsView} aria-labelledby="relations-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>同步关系图</span>
          <h2 id="relations-heading">事件、人物与证据</h2>
        </div>
        <div className={styles.sectionTrailing}>
          <small>{visibleNodeIds.size} NODES</small>
          <ZoomControls onZoomChange={setZoom} zoom={zoom} />
        </div>
      </header>
      <p className={styles.srOnly} id="relationship-graph-summary">
        {seed.caseMeta.relationshipSummary}
      </p>
      <div className={styles.zoomViewport}>
        <div className={styles.zoomStage} style={{ zoom }}>
          {board}
        </div>
      </div>
      <details className={styles.graphAlternative}>
        <summary>查看关系表与文字摘要</summary>
        <div className={styles.graphTableWrap}>
          <table>
            <thead>
              <tr><th>来源</th><th>关系</th><th>目标</th></tr>
            </thead>
            <tbody>
              {seed.graphEdges.map((edge) => (
                <tr key={`table-${edge.from}-${edge.to}`}>
                  <td>{getObject(seed, edge.from)?.label}</td>
                  <td>{edge.label}</td>
                  <td>{getObject(seed, edge.to)?.label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <span className={styles.srOnly}>{visibleNodeIds.size} 个可访问节点</span>
    </section>
  );
}

function TimelineOverview({
  seed,
  selectedEventId,
  selectedObjectId,
  issueStatuses,
  onSelectEvent,
  onSelectObject,
}: {
  seed: PrototypeWorkbenchSeed;
  selectedEventId: string;
  selectedObjectId: string;
  issueStatuses: Record<string, IssueStatus>;
  onSelectEvent: (eventId: string) => void;
  onSelectObject: (objectId: string) => void;
}) {
  const selectedEvent = getEvent(seed, selectedEventId) ?? seed.timelineEvents[0];
  const [timelineWidth, setTimelineWidth] = useState<number | null>(null);
  const timelineResizeRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);

  function startTimelineResize(event: ReactPointerEvent<HTMLDivElement>) {
    timelineResizeRef.current = {
      startX: event.clientX,
      startWidth: timelineWidth ?? DEFAULT_TIMELINE_WIDTH,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveTimelineResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = timelineResizeRef.current;
    if (!resize) return;
    const width = clamp(
      resize.startWidth + (event.clientX - resize.startX),
      240,
      560,
    );
    setTimelineWidth(width);
  }

  function endTimelineResize() {
    timelineResizeRef.current = null;
  }

  const [zoom, setZoom] = useState(1);

  return (
    <div className={styles.zoomViewport}>
      <div className={styles.zoomStage} style={{ zoom }}>
        <div
          className={styles.timelineOverview}
          style={
            {
              "--timeline-width": `${timelineWidth ?? DEFAULT_TIMELINE_WIDTH}px`,
            } as CSSProperties
          }
        >
          <div
            aria-hidden="true"
            className={styles.timelineResizeHandle}
            data-testid="timeline-resize-handle"
            onPointerCancel={endTimelineResize}
            onPointerDown={startTimelineResize}
            onPointerMove={moveTimelineResize}
            onPointerUp={endTimelineResize}
          />
          <section className={styles.timelinePanel} aria-labelledby="timeline-heading">
            <header className={styles.sectionHeader}>
              <div>
                <span>事件序列</span>
                <h2 id="timeline-heading">{seed.caseMeta.timelineTitle}</h2>
              </div>
              <div className={styles.sectionTrailing}>
                <small>{seed.caseMeta.timelineMeta}</small>
                <ZoomControls onZoomChange={setZoom} zoom={zoom} />
              </div>
            </header>
        <ol className={styles.timelineList}>
          {seed.timelineEvents.map((event) => {
            const selected = event.id === selectedEventId;
            const issue = seed.validationIssues.find((item) =>
              event.issueIds.includes(item.id),
            );
            const issueStatus = issue ? issueStatuses[issue.id] : undefined;
            return (
              <li key={event.id}>
                <button
                  aria-pressed={selected}
                  data-selected={selected}
                  onClick={() => onSelectEvent(event.id)}
                  type="button"
                >
                  <span className={styles.eventTime}>{event.time}</span>
                  <span className={styles.eventMarker} aria-hidden="true" />
                  <span className={styles.eventCopy}>
                    <strong>{event.label}</strong>
                    <small>{event.location}</small>
                    {selected ? <em>{event.summary}</em> : null}
                  </span>
                  {issue ? (
                    <span className={styles.eventIssue} data-status={issueStatus}>
                      {issue.severity}
                    </span>
                  ) : (
                    <span className={styles.eventClear}>通过</span>
                  )}
                </button>
              </li>
            );
          })}
        </ol>
      </section>
          <RelationshipGraph
            compact
            onSelectObject={onSelectObject}
            relatedObjectIds={[selectedEvent.id, ...selectedEvent.relatedObjectIds]}
            seed={seed}
            selectedObjectId={selectedObjectId}
          />
        </div>
      </div>
    </div>
  );
}

function MapView({
  seed,
  selectedEventId,
  onSelectEvent,
}: {
  seed: PrototypeWorkbenchSeed;
  selectedEventId: string;
  onSelectEvent: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1);
  return (
    <section className={styles.mapView} aria-labelledby="map-heading">
      <header className={styles.sectionHeader}>
        <div><span>空间核对</span><h2 id="map-heading">{seed.caseMeta.mapTitle}</h2></div>
        <div className={styles.sectionTrailing}>
          <small>{seed.caseMeta.mapMeta}</small>
          <ZoomControls onZoomChange={setZoom} zoom={zoom} />
        </div>
      </header>
      <div className={styles.zoomViewport}>
        <div className={styles.zoomStage} style={{ zoom }}>
          <div className={styles.mapBoard}>
            <svg aria-hidden="true" preserveAspectRatio="none" viewBox="0 0 100 100">
              <path d="M5 18h35v18h18v-12h37M18 5v90M40 18v50h38v27M58 24v28M78 52h17" />
              <path className={styles.mapWater} d="M0 78c18-8 30 8 46 0s29 8 54-2v24H0Z" />
              <path className={styles.mapRoute} d="M19 63C34 58 39 29 30 25s11 27 24 27 10 18 19 18" />
            </svg>
        {seed.mapLabels.map((label) => (
          <span
            className={styles.mapLabel}
            key={`${label.label}-${label.x}-${label.y}`}
            style={{ left: `${label.x}%`, top: `${label.y}%` }}
          >
            {label.label}
          </span>
        ))}
        {seed.mapMarkers.map((marker) => (
          <button
            aria-pressed={selectedEventId === marker.eventId}
            className={styles.mapMarker}
            key={marker.eventId}
            onClick={() => onSelectEvent(marker.eventId)}
            style={{ left: `${marker.x}%`, top: `${marker.y}%` }}
            type="button"
          >
            <i aria-hidden="true" />
            <span>{marker.label}</span>
          </button>
        ))}
          </div>
        </div>
      </div>
      <p className={styles.viewNote}>{seed.caseMeta.mapNote}</p>
    </section>
  );
}

function DossierView({
  seed,
  selectedEventId,
}: {
  seed: PrototypeWorkbenchSeed;
  selectedEventId: string;
}) {
  const event = getEvent(seed, selectedEventId) ?? seed.timelineEvents[0];
  const objectById = new Map(
    seed.caseObjects.map((object) => [object.id, object]),
  );
  const relatedObjects = event.relatedObjectIds
    .map((id) => objectById.get(id))
    .filter((object) => object !== undefined);
  const people = relatedObjects
    .filter((object) => object.kind === "person")
    .map((object) => object.label);
  const locations = relatedObjects
    .filter((object) => object.kind === "location")
    .map((object) => object.label);
  const evidence = relatedObjects
    .filter((object) => object.kind === "evidence")
    .map((object) => object.label);
  const hypotheses = relatedObjects
    .filter((object) => object.kind === "hypothesis")
    .map((object) => object.label);
  const sources = seed.sourceItems.filter(
    (source) => source.eventId === event.id,
  );
  const issues = seed.validationIssues.filter((issue) =>
    event.issueIds.includes(issue.id),
  );
  return (
    <section className={styles.dossierView} aria-labelledby="dossier-heading">
      <header className={styles.sectionHeader}>
        <div><span>卷宗编辑器</span><h2 id="dossier-heading">{event.label}</h2></div>
        <small>结构化字段 · {seed.caseMeta.revision}</small>
      </header>
      <div className={styles.dossierSheet}>
        <div className={styles.sheetIndex}><span>EV</span><strong>{event.id.replace("EV-", "")}</strong></div>
        <div className={styles.sheetFields}>
          <label><span>发生时间</span><input defaultValue={event.time} /></label>
          <label><span>发生地点</span><input defaultValue={event.location} /></label>
          <label className={styles.sheetWide}><span>事件摘要</span><textarea defaultValue={event.summary} rows={5} /></label>
          <label><span>参与人物</span><input defaultValue={people.join("、")} /></label>
          <label><span>关联地点</span><input defaultValue={locations.join("、")} /></label>
          <label><span>关联证据</span><input defaultValue={evidence.join("、")} /></label>
          <label><span>候选假设</span><input defaultValue={hypotheses.join("、")} /></label>
          <label className={styles.sheetWide}><span>引用来源</span><input defaultValue={sources.map((source) => source.label).join("、")} /></label>
        </div>
        <aside className={styles.marginNotes}>
          <span>引用 {String(sources.length).padStart(2, "0")}</span>
          {issues.map((issue) => (
            <p key={issue.id}>{issue.severity} · {issue.title}</p>
          ))}
          <p>知识状态存在冲突</p>
        </aside>
      </div>
    </section>
  );
}

function ExportView({
  seed,
  unresolvedCount,
}: {
  seed: PrototypeWorkbenchSeed;
  unresolvedCount: number;
}) {
  const ready = unresolvedCount === 0;
  return (
    <section className={styles.exportView} aria-labelledby="export-heading">
      <header className={styles.sectionHeader}>
        <div><span>导出预览</span><h2 id="export-heading">{seed.caseMeta.exportTitle}</h2></div>
        <small>{ready ? "READY" : "GATE BLOCKED"}</small>
      </header>
      <div className={styles.exportSheet}>
        <div className={styles.exportCover}>
          <span>{seed.caseMeta.exportCode}</span>
          <h3>{seed.caseMeta.title}</h3>
          <p>{seed.caseMeta.exportSubtitle}</p>
          <strong>{seed.caseMeta.revision}</strong>
        </div>
        <div className={styles.exportChecks}>
          <h3>发布门禁</h3>
          <ul>
            <li data-state="pass"><span>结构完整性</span><b>通过</b></li>
            <li data-state="pass"><span>引用可追溯</span><b>通过</b></li>
            <li data-state={ready ? "pass" : "blocked"}><span>语义验证</span><b>{ready ? "通过" : `${unresolvedCount} 个问题`}</b></li>
            <li data-state="pending"><span>作者批准</span><b>待确认</b></li>
          </ul>
          <button disabled={!ready} type="button">生成导出包</button>
          {!ready ? <p>先处理右侧检查器中的 S0/S1 问题。</p> : null}
        </div>
      </div>
    </section>
  );
}

type CompileTargetId = "novel" | "script" | "interactive" | "dossier" | "test";

const compileTargets: Array<{
  id: CompileTargetId;
  label: string;
  caption: string;
  description: string;
}> = [
  {
    id: "novel",
    label: "小说",
    caption: "章节叙事",
    description: "把事件序列编排成可读的章节化叙事。",
  },
  {
    id: "script",
    label: "剧本",
    caption: "剧本杀手册",
    description: "角色、场景、幕次与线索卡，供线下开本。",
  },
  {
    id: "interactive",
    label: "互动脚本",
    caption: "任务与对话树",
    description: "分支对话与任务数据，供互动游戏引擎使用。",
  },
  {
    id: "dossier",
    label: "作者卷宗",
    caption: "文档包",
    description: "面向作者的对象清单、时间线与编辑笔记。",
  },
  {
    id: "test",
    label: "测试材料",
    caption: "QA 用例",
    description: "验证问题与门禁检查，供测试与验收。",
  },
];

function composeCompilePreview(
  targetId: CompileTargetId,
  seed: PrototypeWorkbenchSeed,
  unresolvedCount: number,
): string {
  const people = seed.caseObjects
    .filter((object) => object.kind === "person")
    .map((object) => object.label);
  const evidence = seed.caseObjects
    .filter((object) => object.kind === "evidence")
    .map((object) => object.label);
  const events = seed.timelineEvents;
  switch (targetId) {
    case "novel":
      return [
        `《${seed.caseMeta.title}》`,
        seed.caseMeta.subtitle,
        "",
        ...events.map(
          (event, index) =>
            `第${"一二三四五六七八九十"[index] ?? index + 1}章 · ${event.label}\n${event.time}，${event.location}。${event.summary}`,
        ),
      ].join("\n");
    case "script":
      return [
        `剧本杀手册 · ${seed.caseMeta.title}`,
        `角色：${people.join("、")}`,
        `场景：${[...new Set(events.map((event) => event.location))].join("、")}`,
        "",
        ...events.map(
          (event) => `第 ${event.time} 幕 · ${event.label}\n${event.summary}`,
        ),
        `线索卡：${evidence.join("、")}`,
      ].join("\n");
    case "interactive":
      return [
        `互动脚本 · ${seed.caseMeta.title}`,
        "",
        ...events.map(
          (event) => `节点 ${event.id} · ${event.label}\n可停留：${event.summary}`,
        ),
        "",
        ...seed.reasoningPaths.map(
          (path) =>
            `分支 · ${path.question}\n→ ${path.conclusion}（${reasoningOutcomeLabels[path.outcome]}）`,
        ),
      ].join("\n");
    case "dossier":
      return [
        `作者卷宗 · ${seed.caseMeta.title}`,
        `修订 ${seed.caseMeta.revision}`,
        "",
        `对象 ${seed.caseObjects.length} 个（人物 ${people.length} · 证据 ${evidence.length}）`,
        `事件 ${events.length} 个 · 推理路径 ${seed.reasoningPaths.length} 条`,
        `待处理问题 ${unresolvedCount} 个`,
        "",
        "编译产物为演示样例，正式版本由 Compiler 生成。",
      ].join("\n");
    case "test":
      return [
        `测试材料 · ${seed.caseMeta.title}`,
        "",
        ...seed.validationIssues.map(
          (issue) =>
            `用例 ${issue.id} · ${issue.severity} ${issue.title}\n规则 ${issue.rule} · 依据 ${issue.evidenceIds.join("、")}`,
        ),
        "",
        `门禁：${unresolvedCount > 0 ? `语义验证阻断（${unresolvedCount} 个问题）` : "全部通过"}`,
      ].join("\n");
  }
}

function CompileCenterView({
  seed,
  unresolvedCount,
}: {
  seed: PrototypeWorkbenchSeed;
  unresolvedCount: number;
}) {
  const [targetId, setTargetId] = useState<CompileTargetId>("novel");
  const [compiled, setCompiled] = useState(false);
  const target = compileTargets.find((item) => item.id === targetId) ?? compileTargets[0];
  const blocked = unresolvedCount > 0;

  return (
    <section className={styles.compileView} aria-labelledby="compile-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>编译中心</span>
          <h2 id="compile-heading">同一份卷宗，多种形式</h2>
        </div>
        <small>{compileTargets.length} FORMATS</small>
      </header>
      <div className={styles.compileTargets} aria-label="编译目标">
        {compileTargets.map((item) => (
          <button
            aria-pressed={targetId === item.id}
            data-selected={targetId === item.id}
            key={item.id}
            onClick={() => {
              setTargetId(item.id);
              setCompiled(false);
            }}
            type="button"
          >
            <span>{item.caption}</span>
            <strong>{item.label}</strong>
            <small>{item.description}</small>
          </button>
        ))}
      </div>
      <div className={styles.compileWorkspace}>
        <section aria-label="编译预览" className={styles.compilePreview}>
          <header>
            <span>编译预览</span>
            <strong>
              {target.label} · {seed.caseMeta.title}
            </strong>
          </header>
          <pre>{composeCompilePreview(targetId, seed, unresolvedCount)}</pre>
          {compiled ? (
            <p className={styles.compileDone}>
              已生成 {target.label} 产物（演示样例，正式版本由 Compiler 生成）。
            </p>
          ) : null}
        </section>
        <aside className={styles.compilePanel}>
          <span>编译选项</span>
          <label>
            <span>产物标题</span>
            <input defaultValue={`${seed.caseMeta.title} · ${target.label}`} />
          </label>
          <label>
            <span>来源修订</span>
            <input defaultValue={seed.caseMeta.revision} />
          </label>
          <div className={styles.compileGate}>
            <span>发布门禁</span>
            <ul>
              <li data-state="pass"><span>结构完整性</span><b>通过</b></li>
              <li data-state="pass"><span>引用可追溯</span><b>通过</b></li>
              <li data-state={blocked ? "blocked" : "pass"}>
                <span>语义验证</span>
                <b>{blocked ? `${unresolvedCount} 个问题` : "通过"}</b>
              </li>
            </ul>
          </div>
          <button
            data-primary="true"
            disabled={blocked}
            onClick={() => setCompiled(true)}
            type="button"
          >
            {blocked ? "先处理验证问题" : `编译为${target.label}`}
          </button>
          {blocked ? (
            <p>存在未解决验证问题，编译产物可能携带矛盾。</p>
          ) : null}
        </aside>
      </div>
    </section>
  );
}

function EvidenceComparison({
  seed,
  issueId,
  status,
  manualValue,
  editing,
  onManualValueChange,
  onStartEditing,
  onSaveManual,
}: {
  seed: PrototypeWorkbenchSeed;
  issueId: string;
  status: IssueStatus;
  manualValue: string;
  editing: boolean;
  onManualValueChange: (value: string) => void;
  onStartEditing: () => void;
  onSaveManual: () => void;
}) {
  const issue =
    seed.validationIssues.find((item) => item.id === issueId) ??
    seed.validationIssues[0];
  return (
    <section className={styles.evidenceCompare} aria-labelledby="evidence-heading">
      <header className={styles.sectionHeader}>
        <div><span>证据 × 知识状态</span><h2 id="evidence-heading">{issue.title}</h2></div>
        <small>{issue.severity} · {statusLabel(status)}</small>
      </header>
      <div className={styles.knowledgeSequence}>
        <article>
          <span>事件前已知</span>
          <strong>22:31 前</strong>
          <p>{issue.beforeKnowledge}</p>
        </article>
        <i aria-hidden="true" />
        <article data-conflict="true">
          <span>事件声称</span>
          <strong>{getEvent(seed, issue.eventId)?.time}</strong>
          <p>{issue.eventClaim}</p>
        </article>
        <i aria-hidden="true" />
        <article>
          <span>证据实际进入</span>
          <strong>22:40</strong>
          <p>{issue.afterKnowledge}</p>
        </article>
      </div>
      <div className={styles.diffPanel}>
        <header><span>建议修订</span><b>人工批准前不会写入 Canon</b></header>
        <div className={styles.diffLine} data-kind="remove"><b>−</b><p>{issue.patchBefore}</p></div>
        <div className={styles.diffLine} data-kind="add"><b>+</b><p>{issue.patchAfter}</p></div>
        {editing ? (
          <label className={styles.manualEditor}>
            <span>人工修订文本</span>
            <textarea autoFocus onChange={(event) => onManualValueChange(event.target.value)} rows={4} value={manualValue} />
            <button onClick={onSaveManual} type="button">保存并局部重算</button>
          </label>
        ) : (
          <button className={styles.textAction} onClick={onStartEditing} type="button">改为人工修正</button>
        )}
      </div>
    </section>
  );
}

function ZoomControls({
  zoom,
  onZoomChange,
}: {
  zoom: number;
  onZoomChange: (zoom: number) => void;
}) {
  return (
    <div aria-label="画布缩放" className={styles.zoomControls}>
      <button
        aria-label="缩小"
        disabled={zoom <= 0.5}
        onClick={() => onZoomChange(Math.max(0.5, zoom - 0.25))}
        type="button"
      >
        −
      </button>
      <button
        aria-label={`缩放比例 ${Math.round(zoom * 100)}%`}
        onClick={() => onZoomChange(1)}
        type="button"
      >
        {Math.round(zoom * 100)}%
      </button>
      <button
        aria-label="放大"
        disabled={zoom >= 2.5}
        onClick={() => onZoomChange(Math.min(2.5, zoom + 0.25))}
        type="button"
      >
        +
      </button>
    </div>
  );
}

const reasoningOutcomeLabels: Record<ReasoningOutcome, string> = {
  supported: "证据支持",
  contested: "解释竞争",
  eliminated: "已排除",
};

const DEFAULT_RAIL_WIDTH = 254;
const DEFAULT_TIMELINE_WIDTH = 340;
const DEFAULT_INSPECTOR_WIDTH = 350;

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

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

// 所有推理路径合并为一张 100×100 逻辑坐标画布：结论收束在顶部、
// 推理步骤按路径分列居中、证据共享并铺在底部；边由引用关系生成。
function buildReasoningCanvas(
  paths: PrototypeReasoningPath[],
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
        y: 32 + (stepIndex * 26) / (stepCount - 1),
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

function ReasoningGraphView({
  seed,
  onSelectObject,
}: {
  seed: PrototypeWorkbenchSeed;
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
          | ((previous: Record<string, ReasoningPoint>) => Record<string, ReasoningPoint>),
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
    id: string;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const suppressClickRef = useRef(false);

  const evidenceById = useMemo(
    () => new Map(seed.caseObjects.map((object) => [object.id, object])),
    [seed.caseObjects],
  );

  function startDrag(event: ReactPointerEvent<HTMLElement>, id: string) {
    dragRef.current = {
      id,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    const board = boardRef.current;
    if (!drag || !board) return;
    const rect = board.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const x = clamp(((event.clientX - rect.left) / rect.width) * 100, 6, 94);
    const y = clamp(((event.clientY - rect.top) / rect.height) * 100, 6, 94);
    if (
      !drag.moved &&
      Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4
    ) {
      drag.moved = true;
    }
    if (drag.moved) {
      setPositions((previous) => ({ ...previous, [drag.id]: { x, y } }));
    }
  }

  function endDrag() {
    if (dragRef.current?.moved) suppressClickRef.current = true;
    dragRef.current = null;
  }

  function selectNode(node: ReasoningCanvasNode) {
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
    <section className={styles.reasoningView} aria-labelledby="reasoning-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>推理过程图</span>
          <h2 id="reasoning-heading">证据如何收束到假设</h2>
        </div>
        <div className={styles.sectionTrailing}>
          <small>{seed.reasoningPaths.length} PATHS</small>
          <ZoomControls onZoomChange={setZoom} zoom={zoom} />
        </div>
      </header>
      {layout.nodes.length ? (
        <>
          <div className={styles.zoomViewport}>
            <div className={styles.zoomStage} style={{ zoom }}>
              <div
                aria-label="推理画布"
                className={styles.reasoningBoard}
                onPointerCancel={endDrag}
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
                    <line x1={from.x} x2={to.x} y1={from.y} y2={to.y} />
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
                    onPointerDown={(event) => startDrag(event, node.id)}
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
                  onPointerDown={(event) => startDrag(event, node.id)}
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
          <div className={styles.reasoningTables}>
            {seed.reasoningPaths.map((path) => {
              const summary = path.steps
                .map((step) => `${step.verb}：${step.claim}`)
                .join("；");
              return (
                <details className={styles.reasoningAlternative} key={path.id}>
                  <summary>
                    推理表 · {path.question}（{reasoningOutcomeLabels[path.outcome]}）
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

const agentPromptPresets = [
  {
    id: "inspect",
    label: "全卷宗体检",
    prompt: "对整个卷宗做一次体检，列出待处理问题与推理收束情况。",
  },
  {
    id: "evidence",
    label: "证据链摘要",
    prompt: "汇总当前证据链，说明每份关键证据支撑了哪些推理。",
  },
  {
    id: "compare",
    label: "候选解释对比",
    prompt: "对比各推理路径的收束状态，指出仍存在竞争的解释。",
  },
  {
    id: "gate",
    label: "导出前检查",
    prompt: "按发布门禁检查导出就绪度。",
  },
] as const;

interface AgentMessage {
  id: string;
  role: "user" | "agent";
  text: string;
}

function composeAgentReply(
  prompt: string,
  seed: PrototypeWorkbenchSeed,
  unresolvedCount: number,
): string {
  if (/体检|问题/.test(prompt)) {
    const issueLines =
      seed.validationIssues
        .map((issue) => `· ${issue.severity} ${issue.title}（${issue.rule}）`)
        .join("\n") || "· 当前没有记录在案的问题";
    return `对“${seed.caseMeta.title}”的体检完成：\n\n${issueLines}\n\n时间线 ${seed.timelineEvents.length} 个事件，推理路径 ${seed.reasoningPaths.length} 条，当前 ${unresolvedCount} 个问题待人工决定。建议优先处理 S0。`;
  }
  if (/证据/.test(prompt)) {
    const evidenceItems = seed.caseObjects.filter(
      (object) => object.kind === "evidence",
    );
    const lines =
      evidenceItems
        .map((item) => {
          const referenced = seed.reasoningPaths.flatMap((path) =>
            path.steps.flatMap((step) => step.evidenceIds),
          ).filter((id) => id === item.id).length;
          return `· ${item.label}（${item.code}）：被 ${referenced} 处推理引用`;
        })
        .join("\n") || "· 卷宗中暂无证据对象";
    return `证据链摘要：\n\n${lines}\n\n问题依据可到检查器的“引用来源”核对。`;
  }
  if (/对比|竞争/.test(prompt)) {
    const lines =
      seed.reasoningPaths
        .map(
          (path) =>
            `· ${path.question} → ${reasoningOutcomeLabels[path.outcome]}`,
        )
        .join("\n") || "· 卷宗中暂无推理路径";
    const contested = seed.reasoningPaths.some(
      (path) => path.outcome === "contested",
    );
    return `候选解释对比：\n\n${lines}\n\n${
      contested
        ? "仍存在竞争解释，冻结前建议补齐证据。"
        : "当前解释已收束，可以进入导出门禁。"
    }`;
  }
  if (/导出|门禁/.test(prompt)) {
    return `导出前检查（${seed.caseMeta.revision}）：\n\n· 结构完整性 — 通过\n· 引用可追溯 — 通过\n· 语义验证 — ${
      unresolvedCount > 0 ? `阻断（${unresolvedCount} 个问题）` : "通过"
    }\n· 作者批准 — 待确认\n\n${
      unresolvedCount > 0
        ? `先处理检查器中的 ${unresolvedCount} 个问题。`
        : "门禁通过，可以生成导出包。"
    }`;
  }
  return `已收到：${prompt}\n\n该指令已记入卷宗统筹队列。目前卷宗共有 ${seed.caseObjects.length} 个对象、${seed.timelineEvents.length} 个事件、${unresolvedCount} 个待处理问题；可以使用上方统筹指令获得针对性分析。`;
}

function AgentPanel({
  seed,
  unresolvedCount,
  onClose,
}: {
  seed: PrototypeWorkbenchSeed;
  unresolvedCount: number;
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<AgentMessage[]>([
    {
      id: "AG-0",
      role: "agent",
      text: `我是卷宗统筹 Agent，可以围绕“${seed.caseMeta.title}”做全卷宗体检、证据链摘要、候选解释对比与导出前检查。`,
    },
  ]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const timersRef = useRef<number[]>([]);

  useEffect(
    () => () => timersRef.current.forEach((timer) => window.clearTimeout(timer)),
    [],
  );

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  function send(prompt: string) {
    const normalized = prompt.trim();
    if (!normalized || thinking) return;
    setMessages((previous) => [
      ...previous,
      { id: `US-${previous.length}`, role: "user", text: normalized },
    ]);
    setDraft("");
    setThinking(true);
    const timer = window.setTimeout(() => {
      setMessages((previous) => [
        ...previous,
        {
          id: `AG-${previous.length}`,
          role: "agent",
          text: composeAgentReply(normalized, seed, unresolvedCount),
        },
      ]);
      setThinking(false);
    }, 420);
    timersRef.current.push(timer);
  }

  return (
    <section aria-label="卷宗统筹 Agent 对话" className={styles.agentPanel}>
      <header className={styles.agentHeader}>
        <div>
          <span>卷宗统筹</span>
          <strong>Agent 对话</strong>
        </div>
        <button aria-label="关闭 Agent 对话" onClick={onClose} type="button">
          <WorkbenchIcon name="close" />
        </button>
      </header>
      <div aria-live="polite" className={styles.agentMessages}>
        {messages.map((message) => (
          <p className={styles.agentMessage} data-role={message.role} key={message.id}>
            {message.text}
          </p>
        ))}
        {thinking ? (
          <p className={styles.agentThinking}>Agent 正在统筹卷宗…</p>
        ) : null}
      </div>
      <div className={styles.agentPrompts} aria-label="统筹指令">
        {agentPromptPresets.map((preset) => (
          <button
            disabled={thinking}
            key={preset.id}
            onClick={() => send(preset.prompt)}
            type="button"
          >
            {preset.label}
          </button>
        ))}
      </div>
      <form
        className={styles.agentInput}
        onSubmit={(event) => {
          event.preventDefault();
          send(draft);
        }}
      >
        <input
          aria-label="给卷宗统筹 Agent 的指令"
          onChange={(event) => setDraft(event.target.value)}
          placeholder="布置卷宗任务…"
          value={draft}
        />
        <button disabled={thinking || !draft.trim()} type="submit">
          发送
        </button>
      </form>
    </section>
  );
}

export function AnalystWorkbench() {
  const {
    activeCandidate,
    adoptCandidate,
    candidateStatus,
  } = useDemoPrototype();
  const seed = activeCandidate?.workbenchSeed ?? defaultWorkbenchSeed;
  const activeCandidateStatus = activeCandidate
    ? candidateStatus(activeCandidate)
    : null;

  return (
    <AnalystWorkbenchSurface
      activeCandidate={activeCandidate}
      activeCandidateStatus={activeCandidateStatus}
      adoptCandidate={adoptCandidate}
      key={seed.id}
      seed={seed}
    />
  );
}

function AnalystWorkbenchSurface({
  seed,
  activeCandidate,
  activeCandidateStatus,
  adoptCandidate,
}: {
  seed: PrototypeWorkbenchSeed;
  activeCandidate: PrototypeDraftCandidate | null;
  activeCandidateStatus: PrototypeDraftCandidateStatus | null;
  adoptCandidate: (candidateId: string) => Promise<boolean>;
}) {
  const [view, setView] = useState<WorkbenchView>("timeline");
  const [selectedEventId, setSelectedEventId] = useState(seed.defaultEventId);
  const [selectedObjectId, setSelectedObjectId] = useState(seed.defaultObjectId);
  const [selectedIssueId, setSelectedIssueId] = useState(seed.defaultIssueId);
  const [issueStatuses, setIssueStatuses] = useState<Record<string, IssueStatus>>(
    () => createIssueStatuses(seed),
  );
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("issues");
  const [kindFilter, setKindFilter] = useState<ObjectKind | "all">("all");
  const [objectQuery, setObjectQuery] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("audio");
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(58);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [mobileRegion, setMobileRegion] = useState<MobileRegion>("canvas");
  const [liveMessage, setLiveMessage] = useState(
    `分析师工作台已就绪。当前打开“${seed.caseMeta.title}”。`,
  );
  const [validationPhase, setValidationPhase] = useState<ValidationPhase>("idle");
  const [manualEditing, setManualEditing] = useState(false);
  const [manualValue, setManualValue] = useState(
    seed.validationIssues[0].patchAfter,
  );
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([
    ...seed.initialAuditEntries,
  ]);
  const [railWidth, setRailWidth] = useState<number | null>(null);
  const railResizeRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState<number | null>(null);
  const inspectorResizeRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);
  const [agentOpen, setAgentOpen] = useState(false);
  const modalRef = useRef<HTMLElement>(null);
  const paletteInputRef = useRef<HTMLInputElement>(null);
  const commandTriggerRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const timersRef = useRef<number[]>([]);

  function startRailResize(event: ReactPointerEvent<HTMLDivElement>) {
    railResizeRef.current = {
      startX: event.clientX,
      startWidth: railWidth ?? DEFAULT_RAIL_WIDTH,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveRailResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = railResizeRef.current;
    if (!resize) return;
    const width = clamp(resize.startWidth + (event.clientX - resize.startX), 170, 460);
    setRailWidth(width);
  }

  function endRailResize() {
    railResizeRef.current = null;
  }

  function startInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    inspectorResizeRef.current = {
      startX: event.clientX,
      startWidth: inspectorWidth ?? DEFAULT_INSPECTOR_WIDTH,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = inspectorResizeRef.current;
    if (!resize) return;
    const width = clamp(
      resize.startWidth + resize.startX - event.clientX,
      250,
      520,
    );
    setInspectorWidth(width);
  }

  function endInspectorResize() {
    inspectorResizeRef.current = null;
  }

  const selectedEvent =
    getEvent(seed, selectedEventId) ?? seed.timelineEvents[0];
  const selectedIssue =
    seed.validationIssues.find((item) => item.id === selectedIssueId) ??
    seed.validationIssues[0];
  const selectedStatus = issueStatuses[selectedIssue.id] ?? "open";
  const relatedObjectIds = [selectedEvent.id, ...selectedEvent.relatedObjectIds];
  const unresolvedCount = seed.validationIssues.filter((issue) => {
    const status = issueStatuses[issue.id];
    return status === "open" || status === "patch-ready";
  }).length;

  const visibleObjects = useMemo(() => {
    const query = objectQuery.trim().toLocaleLowerCase("zh-CN");
    return seed.caseObjects.filter((object) => {
      const matchesKind = kindFilter === "all" || object.kind === kindFilter;
      const matchesQuery = !query || `${object.label} ${object.code} ${object.id}`.toLocaleLowerCase("zh-CN").includes(query);
      return matchesKind && matchesQuery;
    });
  }, [kindFilter, objectQuery, seed]);

  function schedule(callback: () => void, delay: number) {
    const timer = window.setTimeout(callback, delay);
    timersRef.current.push(timer);
  }

  useEffect(() => () => timersRef.current.forEach((timer) => window.clearTimeout(timer)), []);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  useEffect(() => {
    if (!paletteOpen) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    paletteInputRef.current?.focus();

    function handleDialogKeys(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setPaletteOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        modalRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleDialogKeys);
    return () => {
      document.removeEventListener("keydown", handleDialogKeys);
      previousFocusRef.current?.focus();
    };
  }, [paletteOpen]);

  function announce(message: string) {
    setLiveMessage(message);
  }

  function appendAudit(actor: string, action: string, detail: string) {
    setAuditEntries((entries) => [
      { id: `AUD-${Date.now()}`, time: currentClock(), actor, action, detail },
      ...entries,
    ]);
  }

  function selectEvent(eventId: string) {
    const event = getEvent(seed, eventId);
    if (!event) return;
    setSelectedEventId(event.id);
    setSelectedObjectId(event.id);
    const issueId = event.issueIds[0];
    if (issueId) setSelectedIssueId(issueId);
    setView("timeline");
    setMobileRegion("canvas");
    announce(`已选择事件“${event.label}”，关系图和检查器已同步定位。`);
  }

  function selectObject(objectId: string) {
    const object = getObject(seed, objectId);
    if (!object) return;
    setSelectedObjectId(object.id);
    const eventId = object.kind === "event" ? object.id : object.relatedEventIds[0];
    if (eventId) setSelectedEventId(eventId);
    announce(`已选择${objectKindLabels[object.kind]}“${object.label}”，相关事件已高亮。`);
  }

  function openIssue(issueId: string) {
    const issue = seed.validationIssues.find((item) => item.id === issueId);
    if (!issue) return;
    setSelectedIssueId(issue.id);
    setSelectedEventId(issue.eventId);
    setSelectedObjectId(issue.eventId);
    setView("evidence");
    setInspectorTab("issues");
    setMobileRegion("canvas");
    setManualEditing(false);
    setManualValue(issue.patchAfter);
    announce(`已打开${issue.severity}问题“${issue.title}”，主画布切换到证据与知识状态对照。`);
  }

  function requestPatch() {
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: "patch-ready" }));
    setInspectorTab("patch");
    setView("evidence");
    appendAudit("Agent", "生成建议补丁", `${selectedIssue.id} · 等待人工批准`);
    announce("Agent 补丁已生成，仅作为建议展示，等待人工批准。");
  }

  function resolveIssue(action: "approve" | "manual" | "exception") {
    const nextStatus: IssueStatus = action === "exception" ? "exception" : "resolved";
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: nextStatus }));
    setValidationPhase("recomputing");
    setInspectorTab("audit");
    setManualEditing(false);
    const actionLabel = action === "approve" ? "批准 Agent 补丁" : action === "manual" ? "保存人工修正" : "标记已知例外";
    appendAudit(seed.caseMeta.protagonist, actionLabel, `${selectedIssue.id} · 局部重算`);
    announce(`${actionLabel}已记录，正在执行局部重算。`);
    schedule(() => {
      setValidationPhase("idle");
      setLiveMessage(`${actionLabel}已完成。当前仍有 ${Math.max(0, unresolvedCount - 1)} 个待处理问题。`);
    }, 760);
  }

  function revalidateAll() {
    setValidationPhase("running");
    appendAudit(
      "Validator",
      "启动全量重新验证",
      `${seed.caseMeta.revision} · ${unresolvedCount} 个待处理问题`,
    );
    announce("全量重新验证已启动。页面保持可浏览，结果将通过状态消息更新。");
    schedule(() => {
      setValidationPhase("idle");
      setLiveMessage(`全量验证完成：${unresolvedCount} 个问题仍需人工决定。`);
    }, 980);
  }

  function resetDemo() {
    setView("timeline");
    setSelectedEventId(seed.defaultEventId);
    setSelectedObjectId(seed.defaultObjectId);
    setSelectedIssueId(seed.defaultIssueId);
    setIssueStatuses(createIssueStatuses(seed));
    setInspectorTab("issues");
    setKindFilter("all");
    setObjectQuery("");
    setDrawerOpen(true);
    setDrawerTab("audio");
    setPlaying(false);
    setPlayhead(58);
    setManualEditing(false);
    setManualValue(seed.validationIssues[0].patchAfter);
    setAuditEntries([...seed.initialAuditEntries]);
    setAgentOpen(false);
    announce(`演示数据已重置，已返回“${seed.caseMeta.title}”默认问题。`);
  }

  function runPaletteAction(action: () => void) {
    action();
    setPaletteOpen(false);
    setPaletteQuery("");
  }

  const paletteEntries = [
    {
      id: "view-timeline",
      label: "打开事件时间线",
      meta: "视图",
      action: () => { setView("timeline"); setMobileRegion("canvas"); announce("主画布已切换到事件时间线。"); },
    },
    {
      id: "view-relations",
      label: "打开人物与证据关系图",
      meta: "视图",
      action: () => { setView("relations"); setMobileRegion("canvas"); announce("主画布已切换到关系图。"); },
    },
    {
      id: "open-issue",
      label: "定位最高优先级验证问题",
      meta: "S0",
      action: () => openIssue(seed.defaultIssueId),
    },
    {
      id: "open-audio",
      label: `打开${seed.drawer.audioTitle}`,
      meta: "来源",
      action: () => { setDrawerOpen(true); setDrawerTab("audio"); setMobileRegion("sources"); announce(`已打开${seed.drawer.audioTitle}。`); },
    },
  ];
  const normalizedPaletteQuery = paletteQuery.trim().toLocaleLowerCase("zh-CN");
  const matchingPaletteEntries = paletteEntries.filter((item) => !normalizedPaletteQuery || `${item.label} ${item.meta}`.toLocaleLowerCase("zh-CN").includes(normalizedPaletteQuery));
  const matchingPaletteObjects = seed.caseObjects.filter((object) => !normalizedPaletteQuery || `${object.label} ${object.code} ${object.id}`.toLocaleLowerCase("zh-CN").includes(normalizedPaletteQuery)).slice(0, 6);

  function handleTimelineKeys(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const currentIndex = seed.timelineEvents.findIndex((item) => item.id === selectedEventId);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const nextIndex = Math.min(seed.timelineEvents.length - 1, Math.max(0, currentIndex + delta));
    selectEvent(seed.timelineEvents[nextIndex].id);
  }

  return (
    <div
      className={styles.workbench}
      data-mobile-region={mobileRegion}
      data-workbench-seed={seed.id}
    >
      <a className={styles.skipLink} href="#analyst-canvas">跳到主画布</a>
      <header className={styles.topbar}>
        <div className={styles.brandBlock}>
          <span className={styles.brandMark} aria-hidden="true" />
          <div><strong>CaseFile</strong><small>推理卷宗</small></div>
        </div>
        <div className={styles.caseIdentity}>
          <span>当前卷宗</span>
          <strong>{seed.caseMeta.title}</strong>
          <small>{seed.caseMeta.revision}</small>
        </div>
        <div className={styles.topStatus} aria-label="卷宗状态">
          <button data-tone={unresolvedCount > 0 ? "danger" : "success"} onClick={() => { setInspectorTab("issues"); setMobileRegion("inspector"); }} type="button">
            <WorkbenchIcon name="validate" />
            <span><small>验证</small><strong>{unresolvedCount > 0 ? `${unresolvedCount} 个问题` : "已通过"}</strong></span>
          </button>
          <button data-tone={unresolvedCount > 0 ? "muted" : "success"} onClick={() => { setView("export"); setMobileRegion("canvas"); }} type="button">
            <WorkbenchIcon name="export" />
            <span><small>导出</small><strong>{unresolvedCount > 0 ? "门禁阻断" : "可以导出"}</strong></span>
          </button>
        </div>
        <button className={styles.globalSearch} onClick={() => setPaletteOpen(true)} ref={commandTriggerRef} type="button">
          <WorkbenchIcon name="search" />
          <span>搜索对象或命令</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className={styles.topActions}>
          <button aria-label="打开命令面板" onClick={() => setPaletteOpen(true)} type="button"><WorkbenchIcon name="command" /></button>
          <button
            aria-expanded={agentOpen}
            aria-label="打开卷宗统筹 Agent 对话"
            onClick={() => setAgentOpen(true)}
            type="button"
          >
            <WorkbenchIcon name="chat" />
          </button>
          <button aria-label="重置演示数据" onClick={resetDemo} type="button"><WorkbenchIcon name="reset" /></button>
          <Link href="/demo/intake">建案中心</Link>
          <Link href="/">正式模式 ↗</Link>
        </div>
      </header>

      {activeCandidate ? (
        <section
          aria-label="工作稿接力状态"
          className={relayStyles.prototypeRelay}
          data-status={activeCandidateStatus ?? "pending"}
        >
          <div>
            <span>
              {activeCandidateStatus === "current"
                ? "当前工作稿"
                : activeCandidateStatus === "stale"
                  ? "旧简报"
                  : "预览稿"}
            </span>
            <strong>{activeCandidate.title}</strong>
            <p>
              {activeCandidate.focusLabel} · 简报 V
              {String(activeCandidate.briefVersion).padStart(2, "0")} ·
              工作台预览为本地样例
            </p>
          </div>
          <div>
            <Link href="/demo/intake">← 返回候选卷</Link>
            {activeCandidateStatus === "pending" ? (
              <button
                onClick={() => {
                  void adoptCandidate(activeCandidate.id)
                    .then((ok) => {
                      if (ok) announce("该候选已采用为当前工作稿。");
                    })
                    .catch((caught) => {
                      announce(
                        caught instanceof Error
                          ? caught.message
                          : "采用未完成，请稍后重试。",
                      );
                    });
                }}
                type="button"
              >
                采用为当前工作稿
              </button>
            ) : null}
            {activeCandidateStatus === "stale" ? (
              <small>旧简报候选仅供预览，不可采用</small>
            ) : null}
            {activeCandidateStatus === "current" ? (
              <small>已采用 · 客户端路由内保持</small>
            ) : null}
          </div>
        </section>
      ) : null}

      <nav aria-label="移动端工作台区域" className={styles.mobileRegionNav}>
        {mobileRegions.map((region) => (
          <button aria-pressed={mobileRegion === region.id} key={region.id} onClick={() => setMobileRegion(region.id)} type="button">{region.label}</button>
        ))}
      </nav>

      <div
        className={styles.workspaceBody}
        style={
          {
            "--rail-width": `${railWidth ?? DEFAULT_RAIL_WIDTH}px`,
            "--inspector-width": `${inspectorWidth ?? DEFAULT_INSPECTOR_WIDTH}px`,
          } as CSSProperties
        }
      >
        <div
          aria-hidden="true"
          className={styles.railResizeHandle}
          data-testid="rail-resize-handle"
          onPointerCancel={endRailResize}
          onPointerDown={startRailResize}
          onPointerMove={moveRailResize}
          onPointerUp={endRailResize}
        />
        <div
          aria-hidden="true"
          className={styles.inspectorResizeHandle}
          data-testid="inspector-resize-handle"
          onPointerCancel={endInspectorResize}
          onPointerDown={startInspectorResize}
          onPointerMove={moveInspectorResize}
          onPointerUp={endInspectorResize}
        />
        <aside aria-label="项目与对象导航" className={styles.objectRail}>
          <section className={styles.projectTree}>
            <div className={styles.railEyebrow}><span>项目树</span><b>01 / 03</b></div>
            <button className={styles.projectSelector} type="button">
              <span className={styles.projectMonogram}>{seed.caseMeta.monogram}</span>
              <span><strong>{seed.caseMeta.title}</strong><small>主卷宗 · {seed.caseMeta.revision}</small></span>
              <WorkbenchIcon name="chevron" />
            </button>
            <div className={styles.treeBranches}>
              <button data-active="true" type="button"><i />{seed.caseMeta.branchLabel} <b>{seed.timelineEvents.length}</b></button>
              <button type="button"><i />未采用候选 <b>03</b></button>
              <button type="button"><i />导出模板 <b>02</b></button>
            </div>
          </section>

          <section className={styles.objectCatalog}>
            <div className={styles.catalogHeading}>
              <div><span>对象目录</span><small>{seed.caseObjects.length} OBJECTS</small></div>
              <button aria-label="对象筛选器" onClick={() => setObjectQuery("")} type="button">筛</button>
            </div>
            <label className={styles.objectSearch}>
              <WorkbenchIcon name="search" />
              <span className={styles.srOnly}>筛选对象</span>
              <input onChange={(event) => setObjectQuery(event.target.value)} placeholder="筛选当前卷宗" value={objectQuery} />
            </label>
            <div className={styles.kindFilters} aria-label="对象类型筛选">
              <button aria-pressed={kindFilter === "all"} onClick={() => setKindFilter("all")} type="button"><span>全部</span><b>{seed.caseObjects.length}</b></button>
              {kindOrder.map((kind) => (
                <button aria-pressed={kindFilter === kind} key={kind} onClick={() => setKindFilter(kind)} type="button">
                  <span>{objectKindLabels[kind]}</span><b>{seed.caseObjects.filter((object) => object.kind === kind).length}</b>
                </button>
              ))}
            </div>
            <div className={styles.objectList}>
              {visibleObjects.map((object) => {
                const selected = object.id === selectedObjectId;
                const related = relatedObjectIds.includes(object.id);
                return (
                  <button aria-pressed={selected} data-related={related} key={object.id} onClick={() => selectObject(object.id)} type="button">
                    <span className={styles.objectKindMark} data-kind={object.kind}>{objectKindLabels[object.kind].slice(0, 1)}</span>
                    <span><strong>{object.label}</strong><small>{object.code}</small></span>
                    {related ? <i aria-label="与当前事件相关" /> : null}
                  </button>
                );
              })}
              {visibleObjects.length === 0 ? <p className={styles.emptyState}>没有匹配对象。清除筛选后查看完整目录。</p> : null}
            </div>
          </section>
        </aside>

        <main className={styles.canvas} id="analyst-canvas" onKeyDown={handleTimelineKeys} tabIndex={-1}>
          <header className={styles.canvasToolbar}>
            <div className={styles.viewTabs} aria-label="主画布视图" role="tablist">
              {viewOptions.map((option) => (
                <button aria-selected={view === option.id} key={option.id} onClick={() => { setView(option.id); announce(`主画布已切换到${option.label}。`); }} role="tab" type="button">
                  <span>{option.shortLabel}</span>{option.label}
                </button>
              ))}
              {view === "evidence" ? <button aria-selected="true" role="tab" type="button"><span>证</span>证据对照</button> : null}
            </div>
            <div className={styles.canvasMeta}><span>同步定位</span><b>{selectedEvent.time} / {selectedEvent.id}</b></div>
          </header>
          <div className={styles.canvasContent}>
            {view === "timeline" ? (
              <TimelineOverview issueStatuses={issueStatuses} onSelectEvent={selectEvent} onSelectObject={selectObject} seed={seed} selectedEventId={selectedEventId} selectedObjectId={selectedObjectId} />
            ) : null}
            {view === "relations" ? (
              <RelationshipGraph onSelectObject={selectObject} relatedObjectIds={relatedObjectIds} seed={seed} selectedObjectId={selectedObjectId} />
            ) : null}
            {view === "reasoning" ? (
              <ReasoningGraphView onSelectObject={selectObject} seed={seed} />
            ) : null}
            {view === "map" ? <MapView onSelectEvent={selectEvent} seed={seed} selectedEventId={selectedEventId} /> : null}
            {view === "dossier" ? <DossierView seed={seed} selectedEventId={selectedEventId} /> : null}
            {view === "export" ? <ExportView seed={seed} unresolvedCount={unresolvedCount} /> : null}
            {view === "compile" ? (
              <CompileCenterView seed={seed} unresolvedCount={unresolvedCount} />
            ) : null}
            {view === "evidence" ? (
              <EvidenceComparison
                editing={manualEditing}
                issueId={selectedIssueId}
                manualValue={manualValue}
                onManualValueChange={setManualValue}
                onSaveManual={() => resolveIssue("manual")}
                onStartEditing={() => { setManualEditing(true); announce("人工修订编辑器已打开。"); }}
                seed={seed}
                status={selectedStatus}
              />
            ) : null}
          </div>
        </main>

        <aside aria-label="上下文检查器" className={styles.inspector}>
          <header className={styles.inspectorHeader}>
            <div><span>上下文检查器</span><strong>{selectedEvent.label}</strong></div>
            <small>{selectedEvent.id}</small>
          </header>
          <div className={styles.inspectorTabs} aria-label="检查器内容" role="tablist">
            {inspectorTabs.map((tab) => {
              const count = tab.id === "issues" ? unresolvedCount : tab.id === "sources" ? selectedIssue.evidenceIds.length : tab.id === "patch" && selectedStatus === "patch-ready" ? 1 : undefined;
              return (
                <button aria-selected={inspectorTab === tab.id} key={tab.id} onClick={() => setInspectorTab(tab.id)} role="tab" type="button">
                  {tab.label}{count !== undefined ? <b>{count}</b> : null}
                </button>
              );
            })}
          </div>
          <div className={styles.inspectorContent}>
            {inspectorTab === "issues" ? (
              <div className={styles.issueInspector}>
                <div className={styles.issueList}>
                  {seed.validationIssues.map((issue) => {
                    const status = issueStatuses[issue.id] ?? "open";
                    return (
                      <button aria-pressed={issue.id === selectedIssueId} data-status={status} key={issue.id} onClick={() => openIssue(issue.id)} type="button">
                        <span data-severity={issue.severity}>{issue.severity}</span>
                        <span><strong>{issue.title}</strong><small>{statusLabel(status)}</small></span>
                      </button>
                    );
                  })}
                </div>
                <article className={styles.issueDetail}>
                  <header><span data-severity={selectedIssue.severity}>{selectedIssue.severity}</span><div><small>{selectedIssue.rule}</small><h2>{selectedIssue.title}</h2></div></header>
                  <p>{selectedIssue.summary}</p>
                  <dl>
                    <div><dt>定位事件</dt><dd>{getEvent(seed, selectedIssue.eventId)?.time} · {getEvent(seed, selectedIssue.eventId)?.label}</dd></div>
                    <div><dt>依据</dt><dd>{selectedIssue.evidenceIds.map((id) => getObject(seed, id)?.label).filter(Boolean).join("、")}</dd></div>
                    <div><dt>当前状态</dt><dd>{statusLabel(selectedStatus)}</dd></div>
                  </dl>
                  <button className={styles.inspectEvidence} onClick={() => openIssue(selectedIssue.id)} type="button">在主画布查看证据对照</button>
                  <div className={styles.issueActions}>
                    <button onClick={() => { setView("evidence"); setManualEditing(true); setMobileRegion("canvas"); }} type="button">手动修正</button>
                    <button disabled={selectedStatus === "resolved" || selectedStatus === "exception"} onClick={requestPatch} type="button">请求 Agent 补丁</button>
                    <button disabled={selectedStatus === "resolved" || selectedStatus === "exception"} onClick={() => resolveIssue("exception")} type="button">标记已知例外</button>
                  </div>
                </article>
              </div>
            ) : null}

            {inspectorTab === "sources" ? (
              <div className={styles.sourceInspector}>
                <p>引用只说明“依据来自哪里”，不会自动把检索结果提升为卷宗事实。</p>
                {seed.sourceItems.filter((source) => source.eventId === selectedEventId || (source.evidenceObjectId ? selectedIssue.evidenceIds.includes(source.evidenceObjectId) : false)).map((source) => (
                  <article key={source.id}>
                    <header><span>{source.kind}</span><small>{source.meta}</small></header>
                    <h2>{source.label}</h2><p>{source.excerpt}</p>
                    <div><button onClick={() => { setDrawerOpen(true); setDrawerTab(source.kind === "audio" ? "audio" : "transcript"); setMobileRegion("sources"); }} type="button">打开来源</button><button onClick={() => selectEvent(source.eventId)} type="button">定位事件</button></div>
                  </article>
                ))}
              </div>
            ) : null}

            {inspectorTab === "patch" ? (
              <div className={styles.patchInspector}>
                {selectedStatus === "patch-ready" || selectedStatus === "resolved" ? (
                  <>
                    <div className={styles.patchSummary} data-state={selectedStatus}><span>Agent 建议</span><b>{selectedStatus === "resolved" ? "已批准" : "等待批准"}</b></div>
                    <p>该补丁只调整事件措辞和知识进入时间，不新增人物、证据或关系。</p>
                    <div className={styles.compactDiff}><p data-kind="remove">− {selectedIssue.patchBefore}</p><p data-kind="add">+ {selectedIssue.patchAfter}</p></div>
                    <dl><div><dt>影响范围</dt><dd>1 个事件 · 1 个知识状态</dd></div><div><dt>引用变化</dt><dd>新增 A-13 时间锚点</dd></div></dl>
                    <div className={styles.patchActions}>
                      <button disabled={selectedStatus === "resolved"} onClick={() => { setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: "open" })); appendAudit(seed.caseMeta.protagonist, "拒绝 Agent 补丁", selectedIssue.id); announce("补丁已拒绝，验证问题保持待处理。"); }} type="button">拒绝</button>
                      <button disabled={selectedStatus === "resolved"} onClick={() => resolveIssue("approve")} type="button">批准并局部重算</button>
                    </div>
                  </>
                ) : (
                  <div className={styles.inspectorEmpty}><span>PATCH</span><h2>还没有建议补丁</h2><p>先在验证问题中请求 Agent 补丁，系统会展示逐字差异与影响范围。</p><button onClick={requestPatch} type="button">为当前问题生成补丁</button></div>
                )}
              </div>
            ) : null}

            {inspectorTab === "audit" ? (
              <div className={styles.auditInspector}>
                <div className={styles.auditStatus}><span>当前修订</span><strong>{seed.caseMeta.revision}</strong><small>只追加记录</small></div>
                <ol>{auditEntries.map((entry) => <li key={entry.id}><time>{entry.time}</time><i aria-hidden="true" /><div><span>{entry.actor}</span><strong>{entry.action}</strong><small>{entry.detail}</small></div></li>)}</ol>
              </div>
            ) : null}
          </div>
          <footer className={styles.inspectorFooter}>
            <div><span>{validationPhase === "idle" ? "验证器空闲" : validationPhase === "recomputing" ? "局部重算中…" : "全量验证中…"}</span><small>{unresolvedCount} 个问题待决定</small></div>
            <button disabled={validationPhase !== "idle"} onClick={revalidateAll} type="button">重新验证</button>
          </footer>
        </aside>
      </div>

      <section aria-label="来源与运行记录抽屉" className={styles.bottomDrawer} data-open={drawerOpen}>
        <header className={styles.drawerHeader}>
          <button aria-expanded={drawerOpen} className={styles.drawerToggle} onClick={() => setDrawerOpen((open) => !open)} type="button"><WorkbenchIcon name="chevron" /><span>来源抽屉</span><small>录音、转写与检索依据</small></button>
          <div className={styles.drawerTabs} role="tablist">
            {drawerTabs.map((tab) => <button aria-selected={drawerTab === tab.id} key={tab.id} onClick={() => { setDrawerTab(tab.id); setDrawerOpen(true); }} role="tab" type="button">{tab.label}{tab.count ? <b>{tab.count}</b> : null}</button>)}
          </div>
          <div className={styles.drawerObject}><span>绑定对象</span><strong>{selectedEvent.id}</strong></div>
        </header>
        {drawerOpen ? (
          <div className={styles.drawerContent}>
            {drawerTab === "audio" ? (
              <div className={styles.audioPlayer}>
                <button aria-label={playing ? "暂停录音" : "播放录音"} className={styles.playButton} onClick={() => { setPlaying((value) => !value); announce(playing ? "录音已暂停。" : `正在播放${seed.drawer.audioTitle}。`); }} type="button"><WorkbenchIcon name={playing ? "pause" : "play"} /></button>
                <div className={styles.waveform} aria-label={`录音播放进度 ${playhead}%`} role="progressbar" aria-valuemax={100} aria-valuemin={0} aria-valuenow={playhead}>{Array.from({ length: 42 }, (_, index) => <i data-played={index / 41 * 100 <= playhead} key={index} style={{ height: `${22 + ((index * 17) % 64)}%` }} />)}</div>
                <div className={styles.audioMeta}><span>{seed.drawer.audioProgress}</span><strong>{seed.drawer.audioTitle}</strong><small>关键短句将在 {seed.drawer.keyTime} 出现 · 共 {seed.drawer.audioDuration}</small></div>
                <button className={styles.jumpButton} onClick={() => { setPlayhead(62); announce(`播放位置已跳转到 ${seed.drawer.keyTime} 的关键证词。`); }} type="button">跳到 {seed.drawer.keyTime}</button>
              </div>
            ) : null}
            {drawerTab === "transcript" ? <div className={styles.transcriptPanel}><time>{seed.drawer.keyTime}</time><p><mark>“{seed.drawer.keyExcerpt}”</mark> {seed.drawer.transcript}</p><button onClick={() => openIssue(seed.defaultIssueId)} type="button">对照验证问题</button></div> : null}
            {drawerTab === "logs" ? <div className={styles.logPanel}><ul>{seed.drawer.logs.map((entry) => <li key={`${entry.time}-${entry.actor}`}><span>{entry.time}</span><strong>{entry.actor}</strong><p>{entry.detail}</p></li>)}</ul></div> : null}
            {drawerTab === "retrieval" ? <div className={styles.retrievalPanel}>{seed.sourceItems.filter((source) => source.kind === "retrieval" || source.kind === "record").map((source) => <article key={source.id}><span>{source.kind}</span><div><strong>{source.label}</strong><p>{source.excerpt}</p></div><button onClick={() => selectEvent(source.eventId)} type="button">定位</button></article>)}</div> : null}
          </div>
        ) : null}
      </section>

      <div aria-atomic="true" aria-live="polite" className={styles.liveStatus} role="status"><span>STATUS</span>{liveMessage}</div>

      <FocusTrapDialog inputRef={paletteInputRef} modalRef={modalRef} onClose={() => setPaletteOpen(false)} onQueryChange={setPaletteQuery} open={paletteOpen} query={paletteQuery}>
        <section><header><span>命令</span><small>{matchingPaletteEntries.length}</small></header>{matchingPaletteEntries.map((item) => <button key={item.id} onClick={() => runPaletteAction(item.action)} type="button"><span className={styles.paletteCommandMark}>⌘</span><span><strong>{item.label}</strong><small>{item.meta}</small></span><i>打开</i></button>)}{matchingPaletteEntries.length === 0 ? <p>没有匹配命令。</p> : null}</section>
        <section><header><span>卷宗对象</span><small>{matchingPaletteObjects.length}</small></header>{matchingPaletteObjects.map((object) => <button key={object.id} onClick={() => runPaletteAction(() => selectObject(object.id))} type="button"><span className={styles.paletteObjectMark}>{objectKindLabels[object.kind].slice(0, 1)}</span><span><strong>{object.label}</strong><small>{object.code}</small></span><i>{object.id}</i></button>)}{matchingPaletteObjects.length === 0 ? <p>没有匹配对象。</p> : null}</section>
      </FocusTrapDialog>

      {agentOpen ? (
        <AgentPanel
          onClose={() => setAgentOpen(false)}
          seed={seed}
          unresolvedCount={unresolvedCount}
        />
      ) : null}
    </div>
  );
}
