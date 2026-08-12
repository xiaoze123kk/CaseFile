"use client";

import { scaleUtc } from "d3-scale";
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  TimelineTemporalPosition,
  TimelineTimePreviewView,
} from "@/lib/api-client";

import {
  getEvent,
  type IssueStatus,
  type WorkbenchSeed,
} from "../analyst-fixture";
import { ExposurePlanEditor } from "./exposure-plan-editor";
import styles from "./timeline.module.css";
import {
  buildTimelineCertaintySummary,
  buildTimelineLanes,
  timelineCertainty,
  timelineCertaintyLabel,
  type TimelineDisplayEvent,
  type TimelineLaneMode,
} from "./timeline-lanes";
import {
  absoluteTemporalBounds,
  formatAxisTime,
  isV2TemporalPosition,
  keyboardStep,
  parseWallClock,
  shiftTemporalPosition,
  timelineClock,
  timelineEventTime,
  type TimelineAxisLabelMode,
  type TimelinePrecision,
} from "./timeline-time";

type SaveResult = "saved" | "conflict" | "error";
type TimelineValidationStatus =
  | "passed"
  | "failed"
  | "unavailable"
  | "loading"
  | "error";

interface EditorDraft {
  kind: TimelineTemporalPosition["kind"];
  precision: TimelinePrecision;
  value: string;
  start: string;
  end: string;
  anchorEventId: string;
  relation: "before" | "after" | "same_time";
  offsetMinutes: string;
}

interface PendingPreview {
  eventId: string;
  proposedTime: TimelineTemporalPosition;
  status: "loading" | "ready" | "error" | "saving";
  data: TimelineTimePreviewView | null;
  message: string | null;
}

const VIEW_WIDTH = 1080;
const PLOT_LEFT = 260;
const PLOT_RIGHT = 1036;
const AXIS_Y = 50;
const ROW_TOP = 88;
const ROW_HEIGHT = 62;

function displayBounds(
  event: TimelineDisplayEvent,
  proposedTime: TimelineTemporalPosition | null = null,
) {
  if (proposedTime) return absoluteTemporalBounds(proposedTime);
  const normalized = absoluteTemporalBounds(event.source?.time);
  if (normalized) return normalized;
  if (!event.start) return null;
  const start = parseWallClock(
    event.start.replace(/(?:Z|[+-]\d{2}:\d{2})$/, ""),
  );
  const end = event.end
    ? parseWallClock(event.end.replace(/(?:Z|[+-]\d{2}:\d{2})$/, ""))
    : start;
  return start === null || end === null
    ? null
    : { start, end, precision: "minute" as const };
}

function isRelativeProjection(event: TimelineDisplayEvent) {
  return event.timeProjection === "relative-resolved";
}

function editableTime(event: TimelineDisplayEvent) {
  const time = event.source?.time;
  return isV2TemporalPosition(time) ? time : null;
}

function editorDraft(event: TimelineDisplayEvent): EditorDraft {
  const time = editableTime(event);
  const firstValue = event.start ?? "";
  if (!time) {
    return {
      kind: "unknown",
      precision: "minute",
      value: firstValue,
      start: firstValue,
      end: event.end ?? "",
      anchorEventId: "",
      relation: "after",
      offsetMinutes: "",
    };
  }
  if (time.kind === "relative") {
    return {
      kind: time.kind,
      precision: "minute",
      value: "",
      start: "",
      end: "",
      anchorEventId: String(time.anchor_event_ref.object_id),
      relation: time.relation,
      offsetMinutes:
        time.offset_minutes === null ? "" : String(time.offset_minutes),
    };
  }
  if (time.kind === "unknown") {
    return {
      kind: time.kind,
      precision: "minute",
      value: "",
      start: "",
      end: "",
      anchorEventId: "",
      relation: "after",
      offsetMinutes: "",
    };
  }
  if (time.kind === "range") {
    return {
      kind: time.kind,
      precision: time.precision,
      value: "",
      start: time.start,
      end: time.end,
      anchorEventId: "",
      relation: "after",
      offsetMinutes: "",
    };
  }
  return {
    kind: time.kind,
    precision: time.precision,
    value: time.value,
    start: "",
    end: "",
    anchorEventId: "",
    relation: "after",
    offsetMinutes: "",
  };
}

function buildEditorTime(draft: EditorDraft): TimelineTemporalPosition | null {
  if (draft.kind === "unknown") return { kind: "unknown" };
  if (draft.kind === "relative") {
    if (!draft.anchorEventId) return null;
    const offset = draft.offsetMinutes.trim()
      ? Number(draft.offsetMinutes)
      : null;
    if (offset !== null && (!Number.isFinite(offset) || offset < 0)) return null;
    return {
      kind: "relative",
      anchor_event_ref: {
        object_type: "event",
        object_id: draft.anchorEventId,
      },
      relation: draft.relation,
      offset_minutes: draft.relation === "same_time" ? null : offset,
    };
  }
  if (draft.kind === "range") {
    if (!draft.start.trim() || !draft.end.trim()) return null;
    return {
      kind: "range",
      start: draft.start.trim(),
      end: draft.end.trim(),
      precision: draft.precision,
    };
  }
  if (!draft.value.trim()) return null;
  return {
    kind: draft.kind,
    value: draft.value.trim(),
    precision: draft.precision,
  };
}

function positionLabel(position: number | null) {
  return position === null ? "轴外" : `第 ${position + 1} 位`;
}

function timeLabel(time: TimelineTemporalPosition) {
  if (time.kind === "unknown") return "时间未定";
  if (time.kind === "relative") {
    const relation = { before: "之前", after: "之后", same_time: "同时" }[
      time.relation
    ];
    const offset = time.offset_minutes === null ? "" : `${time.offset_minutes} 分钟`;
    return `${time.anchor_event_ref.object_id} ${offset}${relation}`;
  }
  if (time.kind === "range") {
    return `${timelineClock(time.start)} → ${timelineClock(time.end)}`;
  }
  return `${time.kind === "approximate" ? "约 " : ""}${timelineClock(time.value)}`;
}

function eventName(seed: WorkbenchSeed, eventId: string) {
  return getEvent(seed, eventId)?.label ?? eventId;
}

export function TimelineOverview({
  seed,
  selectedEventId,
  issueStatuses,
  onSelectEvent,
  validationStatus,
  editable = false,
  exposurePlanEditable = false,
  projectId,
  draftId,
  saving = false,
  onPreviewTime,
  onConfirmTime,
}: {
  seed: WorkbenchSeed;
  selectedEventId: string | null;
  issueStatuses: Record<string, IssueStatus>;
  onSelectEvent: (eventId: string) => void;
  validationStatus: TimelineValidationStatus;
  editable?: boolean;
  exposurePlanEditable?: boolean;
  projectId?: number;
  draftId?: number;
  saving?: boolean;
  onPreviewTime?: (
    eventId: string,
    time: TimelineTemporalPosition,
  ) => Promise<TimelineTimePreviewView>;
  onConfirmTime?: (
    eventId: string,
    time: TimelineTemporalPosition,
  ) => Promise<SaveResult>;
}) {
  const events = seed.timelineEvents as TimelineDisplayEvent[];
  const selectedEvent =
    (getEvent(seed, selectedEventId) as TimelineDisplayEvent | undefined) ??
    events[0];
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{ eventId: string } | null>(null);
  const [dragGhost, setDragGhost] = useState<{
    eventId: string;
    time: TimelineTemporalPosition;
  } | null>(null);
  const [editorEventId, setEditorEventId] = useState<string | null>(null);
  const [editor, setEditor] = useState<EditorDraft | null>(null);
  const [pending, setPending] = useState<PendingPreview | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<TimelineLaneMode>("events");
  const [diagnosticsVisible, setDiagnosticsVisible] = useState(true);

  const lanes = useMemo(
    () =>
      viewMode === "events"
        ? []
        : buildTimelineLanes(seed, events, viewMode),
    [events, seed, viewMode],
  );
  const certaintySummary = useMemo(
    () => buildTimelineCertaintySummary(events),
    [events],
  );
  const eventById = useMemo(
    () => new Map(events.map((event) => [event.id, event])),
    [events],
  );
  const rowCount = viewMode === "events" ? events.length : lanes.length;

  const axis = useMemo(() => {
    const plotted = events.flatMap((event) => {
      const ghostTime = dragGhost?.eventId === event.id ? dragGhost.time : null;
      const bounds = displayBounds(event, ghostTime);
      return bounds ? [{ event, bounds }] : [];
    });
    if (!plotted.length) return null;
    const minimum = Math.min(...plotted.map((item) => item.bounds.start));
    const maximum = Math.max(...plotted.map((item) => item.bounds.end));
    const span = Math.max(maximum - minimum, 30 * 60 * 1000);
    const padding = Math.max(5 * 60 * 1000, span * 0.06);
    const domainStart = minimum - padding;
    const domainEnd = maximum + padding;
    const scale = scaleUtc()
      .domain([new Date(domainStart), new Date(domainEnd)])
      .range([PLOT_LEFT, PLOT_RIGHT]);
    const ticks = scale.ticks(8);
    const startDate = new Date(domainStart);
    const endDate = new Date(domainEnd);
    const includeDate =
      startDate.getUTCFullYear() !== endDate.getUTCFullYear() ||
      startDate.getUTCMonth() !== endDate.getUTCMonth() ||
      startDate.getUTCDate() !== endDate.getUTCDate();
    const labelMode: TimelineAxisLabelMode = includeDate
      ? ticks.every(
          (tick) =>
            tick.getUTCHours() === 0 &&
            tick.getUTCMinutes() === 0 &&
            tick.getUTCSeconds() === 0,
        )
        ? "date"
        : "date-time"
      : "time";
    return {
      plotted,
      scale,
      ticks,
      labelMode,
    };
  }, [dragGhost, events]);

  if (!selectedEvent) return null;

  const unresolvedEvents = events.filter(
    (event) => event.timeProjection === "unresolved",
  );

  async function requestPreview(
    eventId: string,
    proposedTime: TimelineTemporalPosition,
  ) {
    if (!editable || !onPreviewTime) {
      setNotice("当前视图为只读，不能预演时间修改。");
      return;
    }
    setPending({
      eventId,
      proposedTime,
      status: "loading",
      data: null,
      message: null,
    });
    setNotice("正在核对事实顺序、相对依赖和契约规则…");
    try {
      const data = await onPreviewTime(eventId, proposedTime);
      setPending({
        eventId,
        proposedTime,
        status: "ready",
        data,
        message: null,
      });
      setNotice(
        data.can_confirm
          ? "影响预览已完成；确认后才会写入 Current Draft。"
          : "拟议时间未通过契约检查，请调整后重新预览。",
      );
    } catch (caught) {
      setPending({
        eventId,
        proposedTime,
        status: "error",
        data: null,
        message: caught instanceof Error ? caught.message : "时间预览失败。",
      });
      setNotice("时间预览失败，Current Draft 未被修改。");
    }
  }

  function pointerValue(event: ReactPointerEvent<SVGGElement>) {
    if (!axis || !svgRef.current) return null;
    const rect = svgRef.current.getBoundingClientRect();
    if (!rect.width) return null;
    const viewX = ((event.clientX - rect.left) / rect.width) * VIEW_WIDTH;
    return axis.scale.invert(Math.max(PLOT_LEFT, Math.min(PLOT_RIGHT, viewX))).valueOf();
  }

  function startDrag(
    event: ReactPointerEvent<SVGGElement>,
    timelineEvent: TimelineDisplayEvent,
  ) {
    const time = editableTime(timelineEvent);
    if (!editable || !time || !absoluteTemporalBounds(time)) return;
    event.preventDefault();
    onSelectEvent(timelineEvent.id);
    dragRef.current = { eventId: timelineEvent.id };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<SVGGElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const timelineEvent = events.find((item) => item.id === drag.eventId);
    const time = timelineEvent ? editableTime(timelineEvent) : null;
    const value = pointerValue(event);
    if (!time || value === null) return;
    const proposed = shiftTemporalPosition(time, value);
    if (proposed) setDragGhost({ eventId: drag.eventId, time: proposed });
  }

  function endDrag(event: ReactPointerEvent<SVGGElement>) {
    const drag = dragRef.current;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (!dragGhost || !drag || dragGhost.eventId !== drag.eventId) return;
    const proposed = dragGhost.time;
    setDragGhost(null);
    void requestPreview(drag.eventId, proposed);
  }

  function cancelDrag() {
    dragRef.current = null;
    setDragGhost(null);
  }

  function handleMarkerKey(
    event: ReactKeyboardEvent<SVGGElement>,
    timelineEvent: TimelineDisplayEvent,
  ) {
    if (event.key === "Enter") {
      event.preventDefault();
      openEditor(timelineEvent);
      return;
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const time = editableTime(timelineEvent);
    const bounds = time ? absoluteTemporalBounds(time) : null;
    if (
      !editable ||
      !time ||
      time.kind === "relative" ||
      time.kind === "unknown" ||
      !bounds
    ) return;
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const proposed = shiftTemporalPosition(
      time,
      bounds.start + direction * keyboardStep(time.precision),
    );
    if (proposed) void requestPreview(timelineEvent.id, proposed);
  }

  function openEditor(event: TimelineDisplayEvent) {
    onSelectEvent(event.id);
    setEditorEventId(event.id);
    setEditor(editorDraft(event));
    setPending(null);
    setNotice(
      editable
        ? "编辑后先查看影响，确认前不会写入 Current Draft。"
        : "当前视图只读；可以核对时间语义，但不能写入。",
    );
  }

  async function previewEditor() {
    if (!editor || !editorEventId) return;
    const proposed = buildEditorTime(editor);
    if (!proposed) {
      setNotice("请补全当前时间语义所需字段，再查看影响。");
      return;
    }
    await requestPreview(editorEventId, proposed);
  }

  async function confirmPreview() {
    if (!pending?.data?.can_confirm || !onConfirmTime) return;
    setPending({ ...pending, status: "saving" });
    const result = await onConfirmTime(pending.eventId, pending.proposedTime);
    if (result === "saved") {
      setPending(null);
      setEditor(null);
      setEditorEventId(null);
      setNotice("事件时间已写入 Current Draft，时间轴已按最新事实顺序重排。");
      return;
    }
    setPending({
      ...pending,
      status: "error",
      message:
        result === "conflict"
          ? "Draft 已更新；页面已刷新，请基于最新版重新预览。"
          : "时间未写入，请检查服务状态后重试。",
    });
  }

  return (
    <section className={styles.timelinePanel} aria-labelledby="timeline-heading">
      <div className={styles.timelineChrome}>
        <header className={styles.timelineHeader}>
          <div>
            <span>发生时间 · 比例轴</span>
            <h2 id="timeline-heading">{seed.caseMeta.timelineTitle}</h2>
          </div>
          <div className={styles.timelineMeta}>
            <small>{seed.caseMeta.timelineMeta}</small>
            <i data-status={validationStatus}>
              {validationStatus === "passed"
                ? "验证通过"
                : validationStatus === "failed"
                  ? "待复核"
                  : validationStatus === "loading"
                    ? "验证中"
                    : "待验证"}
            </i>
            <b data-editable={editable}>{editable ? "可编辑" : "只读"}</b>
          </div>
        </header>
        <div className={styles.timelineControls}>
          <div aria-label="时间线展示方式" className={styles.modeSwitch} role="group">
            <span>展示</span>
            {(
              [
                ["events", "事件"],
                ["people", "人物泳道"],
                ["locations", "地点泳道"],
              ] as const
            ).map(([mode, label]) => (
              <button
                aria-pressed={viewMode === mode}
                key={mode}
                onClick={() => setViewMode(mode)}
                type="button"
              >
                {label}
              </button>
            ))}
          </div>
          <button
            aria-pressed={diagnosticsVisible}
            className={styles.diagnosticsToggle}
            onClick={() => setDiagnosticsVisible((visible) => !visible)}
            type="button"
          >
            <span aria-hidden="true" />
            确定性叠层
          </button>
        </div>
        {diagnosticsVisible ? (
          <ul aria-label="时间确定性诊断" className={styles.certaintyLedger}>
            {certaintySummary.map((item) => (
              <li data-axis={item.axis} data-certainty={item.kind} key={item.kind}>
                <span aria-hidden="true" />
                <b>{item.label}</b>
                <small>{item.count}</small>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className={styles.axisViewport} data-testid="timeline-proportional-axis">
        {axis ? (
          <svg
            ref={svgRef}
            aria-label="按作品内时间等比例排列的事件轴"
            className={styles.axisSvg}
            role="group"
            viewBox={`0 0 ${VIEW_WIDTH} ${ROW_TOP + Math.max(rowCount, 1) * ROW_HEIGHT + 26}`}
          >
            <g aria-hidden="true">
              <text className={styles.axisCaption} x={28} y={31}>
                事件发生时间
              </text>
              <text className={styles.axisCaption} x={PLOT_LEFT} y={16}>
                故事发生时间轴
              </text>
              <line className={styles.axisLine} x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={AXIS_Y} y2={AXIS_Y} />
              {axis.ticks.map((tick) => {
                const x = axis.scale(tick);
                return (
                  <g key={tick.valueOf()}>
                    <line className={styles.axisGrid} x1={x} x2={x} y1={AXIS_Y} y2={ROW_TOP + Math.max(rowCount, 1) * ROW_HEIGHT} />
                    <text
                      className={styles.axisTick}
                      data-testid="timeline-axis-tick"
                      textAnchor="middle"
                      x={x}
                      y={31}
                    >
                      {formatAxisTime(tick.valueOf(), axis.labelMode)}
                    </text>
                  </g>
                );
              })}
            </g>
            {viewMode === "events" ? events.map((timelineEvent, index) => {
              const selected = timelineEvent.id === selectedEventId;
              const issue = seed.validationIssues.find((item) =>
                timelineEvent.issueIds.includes(item.id),
              );
              const issueStatus = issue ? issueStatuses[issue.id] : undefined;
              const ghostTime =
                dragGhost?.eventId === timelineEvent.id ? dragGhost.time : null;
              const bounds = displayBounds(timelineEvent, ghostTime);
              const y = ROW_TOP + index * ROW_HEIGHT;
              const startX = bounds ? axis.scale(new Date(bounds.start)) : PLOT_LEFT - 34;
              const endX = bounds ? axis.scale(new Date(bounds.end)) : startX;
              const isRange = Boolean(bounds && bounds.end > bounds.start);
              const isApproximate =
                (ghostTime ?? editableTime(timelineEvent))?.kind === "approximate";
              const projectedRelative = isRelativeProjection(timelineEvent);
              const canDrag = Boolean(
                editable && editableTime(timelineEvent) && bounds,
              );
              return (
                <g
                  key={timelineEvent.id}
                  aria-label={`${timelineEvent.label}，${timelineEventTime(timelineEvent.time)}${canDrag ? "，可拖动调整" : ""}`}
                  aria-pressed={selected}
                  className={styles.eventRow}
                  data-draggable={canDrag}
                  data-dragging={dragGhost?.eventId === timelineEvent.id}
                  data-projection={projectedRelative ? "relative" : "absolute"}
                  data-selected={selected}
                  onKeyDown={(event) => handleMarkerKey(event, timelineEvent)}
                  onPointerCancel={cancelDrag}
                  onPointerDown={(event) => startDrag(event, timelineEvent)}
                  onPointerMove={moveDrag}
                  onPointerUp={endDrag}
                  role="button"
                  tabIndex={0}
                >
                  <title>{`${timelineEvent.label} · ${timelineEventTime(timelineEvent.time)} · ${timelineEvent.location} · ${timelineCertaintyLabel(timelineEvent)}`}</title>
                  <rect className={styles.rowHitbox} height={ROW_HEIGHT - 6} width={VIEW_WIDTH - 24} x={12} y={y - 25} />
                  <text className={styles.rowTime} textAnchor="end" x={78} y={y - 4}>
                    {timelineEventTime(timelineEvent.time)}
                  </text>
                  <text className={styles.rowLabel} x={92} y={y - 5}>
                    {timelineEvent.label}
                  </text>
                  <text className={styles.rowLocation} x={92} y={y + 13}>
                    {timelineEvent.location}
                  </text>
                  {diagnosticsVisible ? (
                    <text
                      className={styles.certaintyTag}
                      data-certainty={timelineCertainty(timelineEvent)}
                      textAnchor="end"
                      x={PLOT_RIGHT - 28}
                      y={y - 10}
                    >
                      {timelineCertaintyLabel(timelineEvent)}
                    </text>
                  ) : null}
                  <line className={styles.rowRule} x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={y} y2={y} />
                  {bounds ? (
                    isRange ? (
                      <>
                        <rect
                          className={styles.rangeBand}
                          height={12}
                          rx={2}
                          width={Math.max(10, endX - startX)}
                          x={startX}
                          y={y - 6}
                        />
                        <circle className={styles.rangeHandle} cx={startX} cy={y} r={5} />
                        <circle className={styles.rangeHandle} cx={endX} cy={y} r={5} />
                      </>
                    ) : (
                      <>
                        {isApproximate ? <circle className={styles.approximateHalo} cx={startX} cy={y} r={14} /> : null}
                        {projectedRelative ? (
                          <circle
                            className={styles.relativeProjectionHalo}
                            cx={startX}
                            cy={y}
                            r={14}
                          />
                        ) : null}
                        <path className={styles.pointMarker} d={`M ${startX} ${y - 8} L ${startX + 8} ${y} L ${startX} ${y + 8} L ${startX - 8} ${y} Z`} />
                      </>
                    )
                  ) : (
                    <g>
                      <circle className={styles.offAxisMarker} cx={startX} cy={y} r={7} />
                      <text className={styles.offAxisLabel} textAnchor="middle" x={startX} y={y + 20}>轴外</text>
                    </g>
                  )}
                  {issue ? (
                    <g className={styles.issuePin} data-status={issueStatus}>
                      <circle cx={PLOT_RIGHT - 8} cy={y} r={8} />
                      <text textAnchor="middle" x={PLOT_RIGHT - 8} y={y + 3}>{timelineEvent.issueIds.length > 1 ? timelineEvent.issueIds.length : "!"}</text>
                    </g>
                  ) : null}
                </g>
              );
            }) : lanes.map((lane, index) => {
              const y = ROW_TOP + index * ROW_HEIGHT;
              const laneSelected = lane.eventIds.includes(selectedEventId ?? "");
              const offAxisCount = lane.eventIds.filter((eventId) => {
                const timelineEvent = eventById.get(eventId);
                return timelineEvent ? !displayBounds(timelineEvent) : false;
              }).length;
              return (
                <g
                  className={styles.laneRow}
                  data-selected={laneSelected}
                  data-testid={`timeline-lane-${lane.id}`}
                  key={lane.id}
                >
                  <rect className={styles.laneHitbox} height={ROW_HEIGHT - 6} width={VIEW_WIDTH - 24} x={12} y={y - 25} />
                  <text className={styles.laneKind} x={28} y={y - 8}>
                    {lane.kind === "person" ? "人物" : "地点"}
                  </text>
                  <text className={styles.laneLabel} x={28} y={y + 9}>
                    {lane.label}
                  </text>
                  <text className={styles.laneCount} textAnchor="end" x={PLOT_LEFT - 18} y={y + 9}>
                    {lane.eventIds.length} EVENT{lane.eventIds.length === 1 ? "" : "S"}
                  </text>
                  <line className={styles.rowRule} x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={y} y2={y} />
                  {lane.eventIds.map((eventId, markerIndex) => {
                    const timelineEvent = eventById.get(eventId);
                    if (!timelineEvent) return null;
                    const bounds = displayBounds(timelineEvent);
                    const certainty = timelineCertainty(timelineEvent);
                    const projectedRelative = isRelativeProjection(timelineEvent);
                    const selected = timelineEvent.id === selectedEventId;
                    const issue = seed.validationIssues.find((item) =>
                      timelineEvent.issueIds.includes(item.id),
                    );
                    const issueStatus = issue ? issueStatuses[issue.id] : undefined;
                    const x = bounds
                      ? axis.scale(new Date(bounds.start))
                      : PLOT_LEFT - 34 - markerIndex * 11;
                    const endX = bounds ? axis.scale(new Date(bounds.end)) : x;
                    return (
                      <g
                        aria-label={`${lane.label}，${timelineEvent.label}，${timelineEventTime(timelineEvent.time)}`}
                        aria-pressed={selected}
                        className={styles.laneMarker}
                        data-certainty={certainty}
                        data-projection={projectedRelative ? "relative" : "absolute"}
                        data-selected={selected}
                        key={eventId}
                        onClick={() => onSelectEvent(eventId)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            onSelectEvent(eventId);
                          }
                        }}
                        role="button"
                        tabIndex={0}
                      >
                        <title>{`${timelineEvent.label} · ${timelineEventTime(timelineEvent.time)} · ${timelineEvent.location} · ${timelineCertaintyLabel(timelineEvent)}`}</title>
                        {bounds && bounds.end > bounds.start ? (
                          <rect
                            className={styles.laneRange}
                            height={8}
                            rx={4}
                            width={Math.max(10, endX - x)}
                            x={x}
                            y={y - 4}
                          />
                        ) : (
                          <>
                            {diagnosticsVisible && certainty === "approximate" ? (
                              <circle className={styles.laneHalo} cx={x} cy={y} r={12} />
                            ) : null}
                            {projectedRelative ? (
                              <circle
                                className={styles.laneRelativeProjection}
                                cx={x}
                                cy={y}
                                r={11}
                              />
                            ) : null}
                            <circle className={styles.lanePoint} cx={x} cy={y} r={6} />
                          </>
                        )}
                        {issue ? (
                          <circle
                            className={styles.laneIssueRing}
                            cx={x}
                            cy={y}
                            data-issue={issue.id}
                            data-status={issueStatus}
                            r={10}
                          />
                        ) : null}
                      </g>
                    );
                  })}
                  {offAxisCount ? (
                    <text className={styles.laneOffAxis} textAnchor="end" x={PLOT_RIGHT} y={y + 18}>
                      轴外 {offAxisCount}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </svg>
        ) : (
          <div className={styles.axisEmpty}>
            <strong>没有可放入比例轴的绝对时间</strong>
            <p>当前工作稿缺少可解析到作品内壁钟时间的锚点；请补充时间后再生成完整时间线。</p>
            <ol aria-label="未解析事件清单" className={styles.axisEmptyList}>
              {events.map((event) => (
                <li key={event.id}>
                  <button onClick={() => onSelectEvent(event.id)} type="button">
                    <time>{timelineEventTime(event.time)}</time>
                    <span>{event.label}</span>
                    <small>{event.location}</small>
                  </button>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {viewMode === "events" ? (
        <ol className={styles.mobileList} aria-label="窄屏事件时间清单">
          {events.map((timelineEvent) => {
            const selected = timelineEvent.id === selectedEventId;
            const issue = seed.validationIssues.find((item) =>
              timelineEvent.issueIds.includes(item.id),
            );
            return (
              <li key={timelineEvent.id}>
                <button
                  aria-pressed={selected}
                  data-selected={selected}
                  onClick={() => onSelectEvent(timelineEvent.id)}
                  type="button"
                >
                  <time dateTime={timelineEvent.start ?? undefined}>
                    {timelineEventTime(timelineEvent.time)}
                  </time>
                  <span>
                    <strong>{timelineEvent.label}</strong>
                    <small>{timelineEvent.location}</small>
                  </span>
                  {diagnosticsVisible ? (
                    <em data-certainty={timelineCertainty(timelineEvent)}>
                      {timelineCertaintyLabel(timelineEvent)}
                    </em>
                  ) : null}
                  <i data-issue={Boolean(issue)}>{issue ? timelineEvent.issueIds.length : "·"}</i>
                </button>
                <button
                  disabled={!editable}
                  onClick={() => openEditor(timelineEvent)}
                  type="button"
                >
                  编辑时间
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <ol
          aria-label={viewMode === "people" ? "窄屏人物泳道" : "窄屏地点泳道"}
          className={styles.mobileLaneList}
        >
          {lanes.map((lane) => (
            <li data-selected={lane.eventIds.includes(selectedEventId ?? "")} key={lane.id}>
              <header>
                <span>{lane.kind === "person" ? "人物" : "地点"}</span>
                <strong>{lane.label}</strong>
                <small>{lane.eventIds.length} 个事件</small>
              </header>
              <div>
                {lane.eventIds.map((eventId) => {
                  const timelineEvent = eventById.get(eventId);
                  if (!timelineEvent) return null;
                  return (
                    <button
                      aria-pressed={eventId === selectedEventId}
                      data-selected={eventId === selectedEventId}
                      key={eventId}
                      onClick={() => onSelectEvent(eventId)}
                      type="button"
                    >
                      <time dateTime={timelineEvent.start ?? undefined}>
                        {timelineEventTime(timelineEvent.time)}
                      </time>
                      <span>
                        <strong>{timelineEvent.label}</strong>
                        <small>
                          {timelineCertaintyLabel(timelineEvent)}
                          {timelineEvent.issueIds.length
                            ? ` · ${timelineEvent.issueIds.length} 个问题`
                            : ""}
                        </small>
                      </span>
                    </button>
                  );
                })}
              </div>
            </li>
          ))}
        </ol>
      )}

      <footer className={styles.timelineFooter}>
        <p role="status">{notice ?? (unresolvedEvents.length ? `有 ${unresolvedEvents.length} 个事件尚不能解析到时间轴。` : editable ? "拖动菱形或区间带，松开后先查看影响。" : "历史与候选内容保持只读。")}</p>
        <div className={styles.timelineFooterActions}>
          <button
            className={styles.timeEditTrigger}
            disabled={!editable}
            onClick={() => openEditor(selectedEvent)}
            type="button"
          >
            编辑所选时间
          </button>
          {projectId !== undefined && draftId !== undefined ? (
            <ExposurePlanEditor
              draftId={draftId}
              editable={exposurePlanEditable}
              events={events}
              onSelectEvent={onSelectEvent}
              projectId={projectId}
              selectedEventId={selectedEventId}
            />
          ) : null}
        </div>
      </footer>

      {editor && editorEventId ? (
        <section className={styles.timeEditor} aria-label="编辑事件时间">
          <header>
            <div><span>时间语义</span><strong>{eventName(seed, editorEventId)}</strong></div>
            <button onClick={() => { setEditor(null); setEditorEventId(null); }} type="button">关闭</button>
          </header>
          <div className={styles.editorGrid}>
            <label>
              <span>语义类型</span>
              <select disabled={!editable} onChange={(event) => setEditor({ ...editor, kind: event.target.value as EditorDraft["kind"] })} value={editor.kind}>
                <option value="exact">准确时间</option>
                <option value="approximate">约略时间</option>
                <option value="range">时间区间</option>
                <option value="relative">相对时间</option>
                <option value="unknown">时间未定</option>
              </select>
            </label>
            {editor.kind === "exact" || editor.kind === "approximate" || editor.kind === "range" ? (
              <label>
                <span>精度</span>
                <select disabled={!editable} onChange={(event) => setEditor({ ...editor, precision: event.target.value as TimelinePrecision })} value={editor.precision}>
                  <option value="second">秒</option><option value="minute">分钟</option><option value="hour">小时</option><option value="day">日期</option>
                </select>
              </label>
            ) : null}
            {editor.kind === "exact" || editor.kind === "approximate" ? (
              <label className={styles.editorWide}><span>作品内时间</span><input disabled={!editable} onChange={(event) => setEditor({ ...editor, value: event.target.value })} placeholder="2042-06-01T20:15" value={editor.value} /></label>
            ) : null}
            {editor.kind === "range" ? (
              <>
                <label><span>开始</span><input disabled={!editable} onChange={(event) => setEditor({ ...editor, start: event.target.value })} value={editor.start} /></label>
                <label><span>结束</span><input disabled={!editable} onChange={(event) => setEditor({ ...editor, end: event.target.value })} value={editor.end} /></label>
              </>
            ) : null}
            {editor.kind === "relative" ? (
              <>
                <label><span>锚点事件</span><select disabled={!editable} onChange={(event) => setEditor({ ...editor, anchorEventId: event.target.value })} value={editor.anchorEventId}><option value="">选择事件</option>{events.filter((event) => event.id !== editorEventId).map((event) => <option key={event.id} value={event.id}>{event.label}</option>)}</select></label>
                <label><span>关系</span><select disabled={!editable} onChange={(event) => setEditor({ ...editor, relation: event.target.value as EditorDraft["relation"] })} value={editor.relation}><option value="before">之前</option><option value="after">之后</option><option value="same_time">同时</option></select></label>
                {editor.relation !== "same_time" ? <label className={styles.editorWide}><span>偏移分钟（可空）</span><input disabled={!editable} inputMode="decimal" min="0" onChange={(event) => setEditor({ ...editor, offsetMinutes: event.target.value })} type="number" value={editor.offsetMinutes} /></label> : null}
              </>
            ) : null}
            {editor.kind === "unknown" ? <p className={styles.unknownNote}>未知时间不携带占位日期；保存后事件会离开比例轴，但仍保留在事件清单。</p> : null}
          </div>
          <footer><span>所有绝对时间均为作品内无时区墙上时间。</span><button disabled={!editable || pending?.status === "loading"} onClick={() => void previewEditor()} type="button">查看影响</button></footer>
        </section>
      ) : null}

      {pending ? (
        <section className={styles.previewSheet} aria-label="时间修改影响预览" role="dialog">
          <header><div><span>写入前影响预览</span><strong>{eventName(seed, pending.eventId)}</strong></div><button disabled={pending.status === "saving"} onClick={() => setPending(null)} type="button">关闭</button></header>
          {pending.status === "loading" ? <p className={styles.previewLoading}>正在核对事实序列与确定性规则…</p> : null}
          {pending.data ? (
            <div className={styles.previewBody}>
              <dl>
                <div><dt>原时间</dt><dd>{timeLabel(pending.data.before_time)}</dd></div>
                <div><dt>拟议时间</dt><dd>{timeLabel(pending.data.proposed_time)}</dd></div>
                <div><dt>事实位置</dt><dd>{positionLabel(pending.data.order_change.from_index)} → {positionLabel(pending.data.order_change.to_index)}</dd></div>
                <div><dt>影响事件</dt><dd>{pending.data.affected_event_ids.length} 个</dd></div>
              </dl>
              <section><strong>跨越事件</strong><p>{pending.data.order_change.crossed_event_ids.length ? pending.data.order_change.crossed_event_ids.map((id) => eventName(seed, id)).join("、") : "没有跨越其他绝对时间事件"}</p></section>
              <section><strong>相对依赖</strong><p>{pending.data.relative_dependent_event_ids.length ? pending.data.relative_dependent_event_ids.map((id) => eventName(seed, id)).join("、") : "没有事件以它作为相对时间锚点"}</p></section>
              <section data-validation={pending.data.validation.status}><strong>确定性验证</strong><p>{pending.data.validation.status === "passed" ? "通过，可以确认写入。" : pending.data.validation.issues.map((issue) => `${issue.code} · ${issue.message}`).join("；")}</p></section>
            </div>
          ) : null}
          {pending.message ? <p className={styles.previewError}>{pending.message}</p> : null}
          <footer><button disabled={pending.status === "saving" || saving} onClick={() => setPending(null)} type="button">取消</button><button disabled={!pending.data?.can_confirm || pending.status === "saving" || saving} onClick={() => void confirmPreview()} type="button">{pending.status === "saving" || saving ? "正在写入…" : "确认写入 Current Draft"}</button></footer>
        </section>
      ) : null}
    </section>
  );
}
