import type {
  SpatialLayerId,
  SpatialLayerVisibility,
  WorkbenchMapModel,
  WorkbenchSpatialRelation,
  WorkbenchUnlocatedReason,
} from "../workbench-real-data-types";
import styles from "./spatial-map.module.css";

const layerCopy: Array<{
  id: SpatialLayerId;
  label: string;
  detail: string;
}> = [
  { id: "locations", label: "地点", detail: "明确坐标" },
  { id: "events", label: "事件", detail: "地点聚合" },
  { id: "relations", label: "空间关系", detail: "只读核对" },
  { id: "unconfirmed", label: "待确认位置", detail: "推算与未定位" },
];

const unlocatedReasonCopy: Record<WorkbenchUnlocatedReason, string> = {
  no_coordinates: "尚无坐标",
  dangling_topology: "空间引用指向不存在的地点",
};

export function SpatialStatusStrip({ counts }: { counts: WorkbenchMapModel["counts"] }) {
  const items = [
    { key: "geographic", label: "真实坐标", value: counts.geographic },
    { key: "scene", label: "场景坐标", value: counts.scene },
    { key: "inferred", label: "自动布局", value: counts.inferred },
    { key: "unlocated", label: "未定位", value: counts.unlocated },
  ];
  return (
    <dl aria-label="坐标来源核验条" className={styles.statusStrip}>
      {items.map((item) => (
        <div data-source={item.key} key={item.key}>
          <dt><i aria-hidden="true" />{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SpatialAuditPanel({
  layers,
  mobileOpen,
  desktopCollapsed,
  highlightedUnlocatedId,
  relations,
  unlocatedLocations,
  onMobileOpenChange,
  onDesktopCollapsedChange,
  onOpenUnlocated,
  onToggleLayer,
}: {
  layers: SpatialLayerVisibility;
  mobileOpen: boolean;
  desktopCollapsed: boolean;
  highlightedUnlocatedId: string | null;
  relations: WorkbenchSpatialRelation[];
  unlocatedLocations: WorkbenchMapModel["unlocatedLocations"];
  onMobileOpenChange: (open: boolean) => void;
  onDesktopCollapsedChange: (collapsed: boolean) => void;
  onOpenUnlocated: (locationId: string) => void;
  onToggleLayer: (layer: SpatialLayerId) => void;
}) {
  function closeAuditPanel() {
    onDesktopCollapsedChange(true);
    onMobileOpenChange(false);
  }

  function toggleAuditPanel() {
    onDesktopCollapsedChange(false);
    onMobileOpenChange(!mobileOpen);
  }

  return (
    <div className={styles.auditDock} data-collapsed={desktopCollapsed}>
      <button
        aria-controls="spatial-audit-panel"
        aria-expanded={mobileOpen}
        className={styles.auditToggle}
        onClick={toggleAuditPanel}
        type="button"
      >
        空间核验
      </button>
      <aside
        aria-label="空间核验工具"
        className={styles.auditPanel}
        data-collapsed={desktopCollapsed}
        data-open={mobileOpen}
        id="spatial-audit-panel"
      >
        <header>
          <div><span>坐标核对</span><strong>图层与待确认项</strong></div>
          <button
            aria-label="收起空间核验工具"
            onClick={closeAuditPanel}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              closeAuditPanel();
            }}
            type="button"
          >−</button>
        </header>
        <fieldset>
          <legend>图层</legend>
          {layerCopy.map((layer) => {
            const disabled =
              layer.id === "events" && !layers.locations;
            return (
              <label data-disabled={disabled || undefined} key={layer.id}>
                <input
                  checked={disabled ? false : layers[layer.id]}
                  disabled={disabled}
                  onChange={() => onToggleLayer(layer.id)}
                  type="checkbox"
                />
                <span>
                  <b>{layer.label}</b>
                  <small>{disabled ? "地点关闭时不可用" : layer.detail}</small>
                </span>
              </label>
            );
          })}
        </fieldset>
        {layers.relations ? (
          <section className={styles.relationAudit}>
            <strong>空间关系 · {relations.length}</strong>
            <p>关系连线不代表实际路线。</p>
            {relations.length ? (
              <ol aria-label="当前可见空间关系">
                {relations.map((relation) => (
                  <li key={relation.relationId}>
                    <span>{relation.kind === "travel" ? "→" : "—"}</span>
                    <b>{relation.fromLocationId}</b>
                    <small>{relation.label}</small>
                    <b>{relation.toLocationId}</b>
                  </li>
                ))}
              </ol>
            ) : (
              <small>当前可见地点之间没有可展示的空间关系。</small>
            )}
          </section>
        ) : null}
        {layers.unconfirmed ? (
          <section className={styles.unlocatedAudit}>
            <strong>未定位地点 · {unlocatedLocations.length}</strong>
            {unlocatedLocations.length ? (
              <ol aria-label="未定位地点">
                {unlocatedLocations.map((location) => (
                  <li
                    data-highlighted={
                      highlightedUnlocatedId === location.locationId || undefined
                    }
                    data-reason={location.reason}
                    key={location.locationId}
                  >
                    <button onClick={() => onOpenUnlocated(location.locationId)} type="button">
                      <i aria-hidden="true" />
                      <span>
                        <b>{location.label}</b>
                        <small>{location.locationId} · {unlocatedReasonCopy[location.reason]}</small>
                      </span>
                    </button>
                  </li>
                ))}
              </ol>
            ) : (
              <small>当前工作稿没有未定位地点。</small>
            )}
          </section>
        ) : null}
      </aside>
    </div>
  );
}
