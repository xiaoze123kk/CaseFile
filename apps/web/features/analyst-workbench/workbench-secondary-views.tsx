import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  useRef,
  useState,
} from "react";

import {
  getEvent,
  type IssueStatus,
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
import { RelationshipGraph } from "./workbench-relationship-graph";

const DEFAULT_TIMELINE_WIDTH = 340;

export function TimelineOverview({
  seed,
  selectedEventId,
  selectedObjectId,
  issueStatuses,
  onSelectEvent,
  onSelectObject,
}: {
  seed: WorkbenchSeed;
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
    setTimelineWidth(
      clamp(resize.startWidth + (event.clientX - resize.startX), 240, 560),
    );
  }

  function endTimelineResize() {
    timelineResizeRef.current = null;
  }

  return (
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
      <section
        className={styles.timelinePanel}
        aria-labelledby="timeline-heading"
      >
        <header className={styles.sectionHeader}>
          <div>
            <span>事件序列</span>
            <h2 id="timeline-heading">{seed.caseMeta.timelineTitle}</h2>
          </div>
          <small>{seed.caseMeta.timelineMeta}</small>
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
                    <span
                      className={styles.eventIssue}
                      data-status={issueStatus}
                    >
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
        relatedObjectIds={[
          selectedEvent.id,
          ...selectedEvent.relatedObjectIds,
        ]}
        seed={seed}
        selectedObjectId={selectedObjectId}
      />
    </div>
  );
}

export function MapView({
  seed,
  selectedEventId,
  onSelectEvent,
}: {
  seed: WorkbenchSeed;
  selectedEventId: string;
  onSelectEvent: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [tool, setTool] = useState<CanvasTool>("select");
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const panRef = useRef<{
    startX: number;
    startY: number;
    startPan: { x: number; y: number };
  } | null>(null);

  function startMapPan(event: ReactPointerEvent<HTMLDivElement>) {
    if (tool !== "pan") return;
    panRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      startPan: pan,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveMapPan(event: ReactPointerEvent<HTMLDivElement>) {
    const ref = panRef.current;
    if (!ref) return;
    setPan({
      x: clamp(ref.startPan.x + (event.clientX - ref.startX), -600, 600),
      y: clamp(ref.startPan.y + (event.clientY - ref.startY), -600, 600),
    });
  }

  function endMapPan() {
    panRef.current = null;
  }

  function selectMarker(eventId: string) {
    if (tool !== "select") return;
    onSelectEvent(eventId);
  }

  return (
    <section className={styles.mapView} aria-labelledby="map-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>空间核对</span>
          <h2 id="map-heading">{seed.caseMeta.mapTitle}</h2>
        </div>
        <div className={styles.sectionTrailing}>
          <small>{seed.caseMeta.mapMeta}</small>
        </div>
      </header>
      <div className={styles.zoomViewport}>
        <div className={styles.zoomStage} style={{ zoom }}>
          <div
            className={styles.panStage}
            style={{ transform: `translate(${pan.x}px, ${pan.y}px)` }}
          >
            <div
              className={styles.mapBoard}
              data-tool={tool}
              onPointerCancel={endMapPan}
              onPointerDown={startMapPan}
              onPointerMove={moveMapPan}
              onPointerUp={endMapPan}
            >
              <svg
                aria-hidden="true"
                preserveAspectRatio="none"
                viewBox="0 0 100 100"
              >
                <path d="M5 18h35v18h18v-12h37M18 5v90M40 18v50h38v27M58 24v28M78 52h17" />
                <path
                  className={styles.mapWater}
                  d="M0 78c18-8 30 8 46 0s29 8 54-2v24H0Z"
                />
                <path
                  className={styles.mapRoute}
                  d="M19 63C34 58 39 29 30 25s11 27 24 27 10 18 19 18"
                />
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
                  onClick={() => selectMarker(marker.eventId)}
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
        <div
          aria-label="画布控制"
          className={styles.canvasOverlayControls}
          role="group"
        >
          <CanvasTools onToolChange={setTool} tool={tool} />
          <ZoomControls onZoomChange={setZoom} zoom={zoom} />
        </div>
      </div>
      <p className={styles.viewNote}>{seed.caseMeta.mapNote}</p>
    </section>
  );
}

export function DossierView({
  seed,
  selectedEventId,
}: {
  seed: WorkbenchSeed;
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
        <div>
          <span>卷宗编辑器</span>
          <h2 id="dossier-heading">{event.label}</h2>
        </div>
        <small>结构化字段 · {seed.caseMeta.revision}</small>
      </header>
      <div className={styles.dossierSheet}>
        <div className={styles.sheetIndex}>
          <span>EV</span>
          <strong>{event.id.replace("EV-", "")}</strong>
        </div>
        <div className={styles.sheetFields}>
          <label>
            <span>发生时间</span>
            <input defaultValue={event.time} />
          </label>
          <label>
            <span>发生地点</span>
            <input defaultValue={event.location} />
          </label>
          <label className={styles.sheetWide}>
            <span>事件摘要</span>
            <textarea defaultValue={event.summary} rows={5} />
          </label>
          <label>
            <span>参与人物</span>
            <input defaultValue={people.join("、")} />
          </label>
          <label>
            <span>关联地点</span>
            <input defaultValue={locations.join("、")} />
          </label>
          <label>
            <span>关联证据</span>
            <input defaultValue={evidence.join("、")} />
          </label>
          <label>
            <span>候选假设</span>
            <input defaultValue={hypotheses.join("、")} />
          </label>
          <label className={styles.sheetWide}>
            <span>引用来源</span>
            <input
              defaultValue={sources.map((source) => source.label).join("、")}
            />
          </label>
        </div>
        <aside className={styles.marginNotes}>
          <span>引用 {String(sources.length).padStart(2, "0")}</span>
          {issues.map((issue) => (
            <p key={issue.id}>
              {issue.severity} · {issue.title}
            </p>
          ))}
          <p>知识状态存在冲突</p>
        </aside>
      </div>
    </section>
  );
}

export function ExportView({
  seed,
  unresolvedCount,
}: {
  seed: WorkbenchSeed;
  unresolvedCount: number;
}) {
  const ready = unresolvedCount === 0;

  return (
    <section className={styles.exportView} aria-labelledby="export-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>导出预览</span>
          <h2 id="export-heading">{seed.caseMeta.exportTitle}</h2>
        </div>
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
            <li data-state="pass">
              <span>结构完整性</span>
              <b>通过</b>
            </li>
            <li data-state="pass">
              <span>引用可追溯</span>
              <b>通过</b>
            </li>
            <li data-state={ready ? "pass" : "blocked"}>
              <span>语义验证</span>
              <b>{ready ? "通过" : `${unresolvedCount} 个问题`}</b>
            </li>
            <li data-state="pending">
              <span>作者批准</span>
              <b>待确认</b>
            </li>
          </ul>
          <button disabled={!ready} type="button">
            生成导出包
          </button>
          {!ready ? <p>先处理右侧检查器中的 S0/S1 问题。</p> : null}
        </div>
      </div>
    </section>
  );
}

type CompileTargetId =
  | "novel"
  | "script"
  | "interactive"
  | "dossier"
  | "test";

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
  seed: WorkbenchSeed,
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
        "编译产物为开发样例，正式版本由 Compiler 生成。",
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

export function CompileCenterView({
  seed,
  unresolvedCount,
}: {
  seed: WorkbenchSeed;
  unresolvedCount: number;
}) {
  const [targetId, setTargetId] = useState<CompileTargetId>("novel");
  const [compiled, setCompiled] = useState(false);
  const target =
    compileTargets.find((item) => item.id === targetId) ?? compileTargets[0];
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
              已生成 {target.label} 产物（开发样例，正式版本由 Compiler 生成）。
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
              <li data-state="pass">
                <span>结构完整性</span>
                <b>通过</b>
              </li>
              <li data-state="pass">
                <span>引用可追溯</span>
                <b>通过</b>
              </li>
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
          {blocked ? <p>存在未解决验证问题，编译产物可能携带矛盾。</p> : null}
        </aside>
      </div>
    </section>
  );
}
