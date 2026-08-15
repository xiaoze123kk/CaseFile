import type {
  WorkbenchSpatialLocation,
  WorkbenchSpatialPosition,
} from "../workbench-real-data-types";
import styles from "./spatial-map.module.css";

const sourceLabels: Record<WorkbenchSpatialLocation["source"], string> = {
  wgs84: "WGS84 地理坐标",
  schematic: "明确场景坐标",
  inferred: "关系推算位置",
};

export type SpatialEditStatus =
  | "idle"
  | "saving"
  | "conflict"
  | "reviewing"
  | "error";

export interface SpatialEditSession {
  locationId: string;
  baseline: WorkbenchSpatialPosition;
  preview: WorkbenchSpatialPosition;
  dirty: boolean;
  latestChanged: boolean;
  notice: string | null;
  status: SpatialEditStatus;
}

export function spatialCoordinateLabel(position: WorkbenchSpatialPosition): string {
  return position.kind === "wgs84"
    ? `${position.latitude.toFixed(5)}, ${position.longitude.toFixed(5)}`
    : `X ${position.x.toFixed(1)} / Y ${position.y.toFixed(1)}`;
}

export function SpatialMapPreviewCard({
  activeLocation,
  editActionLabel,
  editSession,
  readOnlyReason,
  selectedEventId,
  showEvents,
  onCancelEdit,
  onClear,
  onReviewLatest,
  onSaveEdit,
  onSelectEvent,
  onStartEdit,
}: {
  activeLocation: WorkbenchSpatialLocation;
  editActionLabel: string;
  editSession: SpatialEditSession | null;
  readOnlyReason: string | null;
  selectedEventId: string | null;
  showEvents: boolean;
  onCancelEdit: () => void;
  onClear: () => void;
  onReviewLatest: () => void;
  onSaveEdit: () => void;
  onSelectEvent: (eventId: string) => void;
  onStartEdit: () => void;
}) {
  const editing = editSession?.locationId === activeLocation.locationId;
  return (
    <aside
      aria-labelledby="spatial-preview-title"
      className={styles.previewCard}
      role="dialog"
    >
      <header>
        <div>
          <span>{activeLocation.locationId ?? "FIXTURE LOCATION"}</span>
          <h3 id="spatial-preview-title">{activeLocation.label}</h3>
        </div>
        <button aria-label="关闭地点快览" onClick={onClear} type="button">×</button>
      </header>
      <dl>
        <div><dt>坐标来源</dt><dd data-source={activeLocation.source}>{sourceLabels[activeLocation.source]}</dd></div>
        <div><dt>当前坐标</dt><dd>{spatialCoordinateLabel(activeLocation.position)}</dd></div>
        <div><dt>关联对象</dt><dd>{activeLocation.relatedObjectIds.length}</dd></div>
        <div><dt>地点事件</dt><dd>{activeLocation.events.length}</dd></div>
      </dl>

      {editing && editSession ? (
        <section className={styles.editPanel} aria-label="位置编辑预览">
          <div className={styles.editComparison}>
            <span><small>持久化位置</small><b>{spatialCoordinateLabel(editSession.baseline)}</b></span>
            <span data-changed={editSession.dirty}><small>本地预览</small><b>{spatialCoordinateLabel(editSession.preview)}</b></span>
          </div>
          <p>拖动标记，或聚焦标记后使用方向键微调；按住 Shift 可增大步长。</p>
          {editSession.latestChanged ? (
            <p className={styles.editWarning}>最新版已修改此地点坐标，请核对差异后再次保存。</p>
          ) : null}
          {editSession.notice ? (
            <p className={styles.editNotice} role="status">{editSession.notice}</p>
          ) : null}
          <div className={styles.editActions}>
            <button
              disabled={editSession.status === "saving" || editSession.status === "reviewing"}
              onClick={onCancelEdit}
              type="button"
            >取消</button>
            {editSession.status === "conflict" ? (
              <button onClick={onReviewLatest} type="button">核对最新版</button>
            ) : null}
            <button
              disabled={!editSession.dirty || editSession.baseline.kind !== editSession.preview.kind || editSession.status === "saving" || editSession.status === "reviewing" || editSession.status === "conflict"}
              onClick={onSaveEdit}
              type="button"
            >{editSession.status === "saving" ? "正在保存…" : "保存位置"}</button>
          </div>
        </section>
      ) : (
        <section className={styles.previewActions}>
          {readOnlyReason ? <p>{readOnlyReason}</p> : null}
          {!readOnlyReason && activeLocation.locationId ? (
            <button onClick={onStartEdit} type="button">{editActionLabel}</button>
          ) : null}
        </section>
      )}

      {showEvents ? (
        activeLocation.events.length ? (
          <ol aria-label="地点关联事件">
            {activeLocation.events.slice(0, 4).map((event) => (
              <li key={event.eventId}>
                <button
                  aria-pressed={selectedEventId === event.eventId}
                  onClick={() => onSelectEvent(event.eventId)}
                  onKeyDown={(keyboardEvent) => {
                    if (keyboardEvent.key !== "Enter" && keyboardEvent.key !== " ") return;
                    keyboardEvent.preventDefault();
                    onSelectEvent(event.eventId);
                  }}
                  type="button"
                >
                  <time>{event.time}</time>
                  <span>{event.label}</span>
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className={styles.noEvents}>这个地点尚未关联事件。</p>
        )
      ) : (
        <p className={styles.noEvents}>事件图层已关闭；地点关联仍保留在卷宗中。</p>
      )}
      {showEvents && activeLocation.events.length > 4 ? (
        <small className={styles.moreEvents}>另有 {activeLocation.events.length - 4} 个事件</small>
      ) : null}
    </aside>
  );
}
