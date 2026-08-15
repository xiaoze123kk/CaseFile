"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  defaultSpatialLayerVisibility,
  filterWorkbenchSpatialView,
} from "../workbench-spatial-model";
import type {
  ReloadedSpatialLocation,
  SpatialLayerId,
  SpatialLayerVisibility,
  SpatialPositionPayload,
  SpatialPositionSaveResult,
  WorkbenchMapModel,
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
  WorkbenchSpatialPosition,
} from "../workbench-real-data-types";
import {
  SpatialAuditPanel,
  SpatialStatusStrip,
} from "./spatial-map-controls";
import {
  SpatialMapPreviewCard,
  type SpatialEditSession,
} from "./spatial-map-preview-card";
import {
  createSpatialRenderer,
  resolveMapTileConfiguration,
  type SpatialRenderer,
  type SpatialViewport,
} from "./spatial-renderer";
import styles from "./spatial-map.module.css";

const modeLabels: Record<WorkbenchSpatialMode, string> = {
  geographic: "真实地图",
  scene: "场景图",
  topology: "自动布局",
};

function cloneDefaultLayers(): SpatialLayerVisibility {
  return { ...defaultSpatialLayerVisibility };
}

function initialLayerState(): Record<WorkbenchSpatialMode, SpatialLayerVisibility> {
  return {
    geographic: cloneDefaultLayers(),
    scene: cloneDefaultLayers(),
    topology: cloneDefaultLayers(),
  };
}

function relatedLocation(
  location: WorkbenchSpatialLocation,
  selectedObjectId: string | null,
  selectedEventId: string | null,
): boolean {
  return (
    location.locationId === selectedObjectId ||
    location.events.some((event) => event.eventId === selectedEventId) ||
    Boolean(selectedObjectId && location.relatedObjectIds.includes(selectedObjectId))
  );
}

function findLocation(
  map: WorkbenchMapModel,
  locationId: string,
  preferredMode: WorkbenchSpatialMode | null,
): WorkbenchSpatialLocation | null {
  const modes = preferredMode
    ? [preferredMode, ...map.availableModes.filter((mode) => mode !== preferredMode)]
    : map.availableModes;
  for (const mode of modes) {
    const location = map.views[mode].locations.find(
      (candidate) => candidate.locationId === locationId,
    );
    if (location) return location;
  }
  return null;
}

function positionsEqual(
  left: WorkbenchSpatialPosition,
  right: WorkbenchSpatialPosition,
): boolean {
  if (left.kind !== right.kind) return false;
  return left.kind === "wgs84" && right.kind === "wgs84"
    ? left.latitude === right.latitude && left.longitude === right.longitude
    : left.kind === "planar" && right.kind === "planar"
      ? left.x === right.x && left.y === right.y
      : false;
}

function payloadToPosition(
  payload: SpatialPositionPayload,
): WorkbenchSpatialPosition {
  return payload.coordinate_system === "wgs84"
    ? {
        kind: "wgs84",
        latitude: payload.latitude,
        longitude: payload.longitude,
      }
    : { kind: "planar", x: payload.x, y: payload.y };
}

function positionToPayload(
  position: WorkbenchSpatialPosition,
): SpatialPositionPayload {
  return position.kind === "wgs84"
    ? {
        coordinate_system: "wgs84",
        latitude: position.latitude,
        longitude: position.longitude,
      }
    : { coordinate_system: "schematic", x: position.x, y: position.y };
}

export interface SpatialMapViewProps {
  map: WorkbenchMapModel;
  title: string;
  meta: string;
  note: string;
  selectedObjectId: string | null;
  selectedEventId: string | null;
  readOnlyReason?: string | null;
  onSelectLocation: (locationId: string) => boolean;
  onSelectEvent: (eventId: string) => boolean;
  onClearSelection: () => boolean;
  onOpenLocationDetails: (locationId: string) => boolean;
  onRequestPositionEdit?: (locationId: string) => boolean;
  onPositionEditStateChange?: (active: boolean, dirty: boolean) => void;
  onReloadSpatialLocation?: (
    locationId: string,
  ) => Promise<ReloadedSpatialLocation>;
  onSaveSpatialPosition?: (
    locationId: string,
    position: SpatialPositionPayload,
  ) => Promise<SpatialPositionSaveResult>;
}

export function SpatialMapView({
  map,
  title,
  meta,
  note,
  selectedObjectId,
  selectedEventId,
  readOnlyReason = null,
  onSelectLocation,
  onSelectEvent,
  onClearSelection,
  onOpenLocationDetails,
  onRequestPositionEdit,
  onPositionEditStateChange,
  onReloadSpatialLocation,
  onSaveSpatialPosition,
}: SpatialMapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<SpatialRenderer | null>(null);
  const viewportsRef = useRef<Partial<Record<WorkbenchSpatialMode, SpatialViewport>>>(
    {},
  );
  const pendingViewportRef = useRef<{
    mode: WorkbenchSpatialMode;
    viewport: SpatialViewport | null;
  } | null>(null);
  const [layersByMode, setLayersByMode] = useState(initialLayerState);
  const [requestedMode, setRequestedMode] = useState<WorkbenchSpatialMode | null>(
    map.defaultMode,
  );
  const [manualModeSelectionKey, setManualModeSelectionKey] = useState<string | null>(
    null,
  );
  const [openedSpatialId, setOpenedSpatialId] = useState<string | null>(null);
  const [editSession, setEditSession] = useState<SpatialEditSession | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [interactionNotice, setInteractionNotice] = useState<string | null>(null);
  const [initializationError, setInitializationError] = useState<string | null>(null);
  const [tileUnavailable, setTileUnavailable] = useState(false);
  const tileConfiguration = useMemo(
    () =>
      resolveMapTileConfiguration(
        process.env.NEXT_PUBLIC_CASEFILE_MAP_TILE_URL,
        process.env.NEXT_PUBLIC_CASEFILE_MAP_ATTRIBUTION,
      ),
    [],
  );

  const selectionKey = `${selectedObjectId ?? ""}:${selectedEventId ?? ""}`;
  const availableRequestedMode =
    requestedMode && map.availableModes.includes(requestedMode)
      ? requestedMode
      : map.defaultMode;
  const selectedMode =
    selectedObjectId || selectedEventId
      ? map.availableModes.find((candidateMode) =>
          map.views[candidateMode].locations.some((location) =>
            relatedLocation(location, selectedObjectId, selectedEventId),
          ),
        ) ?? null
      : null;
  const requestedModeHasSelection = Boolean(
    availableRequestedMode &&
      map.views[availableRequestedMode].locations.some((location) =>
        relatedLocation(location, selectedObjectId, selectedEventId),
      ),
  );
  const mode =
    manualModeSelectionKey === selectionKey ||
    !selectedMode ||
    requestedModeHasSelection
      ? availableRequestedMode
      : selectedMode;
  const controlMode = mode ?? "topology";
  const layers = layersByMode[controlMode];
  const currentView = mode ? map.views[mode] : null;
  const selectedLocation =
    currentView?.locations.find((location) =>
      relatedLocation(location, selectedObjectId, selectedEventId),
    ) ?? null;
  const openedLocation =
    currentView?.locations.find((location) => location.spatialId === openedSpatialId) ??
    null;
  const editedLocation = editSession
    ? findLocation(map, editSession.locationId, mode)
    : null;
  const activeBaseLocation = editedLocation ?? openedLocation ?? selectedLocation;
  const activeLocation =
    activeBaseLocation &&
    editSession?.locationId === activeBaseLocation.locationId
      ? { ...activeBaseLocation, position: editSession.preview }
      : activeBaseLocation;
  const activeSpatialId = activeBaseLocation?.spatialId ?? null;
  const visibleView = useMemo(() => {
    if (!currentView) return null;
    const filtered = filterWorkbenchSpatialView(currentView, layers);
    return {
      ...filtered,
      locations: filtered.locations.map((location) =>
        editSession?.locationId === location.locationId
          ? { ...location, position: editSession.preview }
          : location,
      ),
    };
  }, [currentView, editSession, layers]);

  useEffect(() => {
    if (selectedLocation?.source !== "inferred" || layers.unconfirmed) return;
    const revealSelection = window.setTimeout(() => {
      setLayersByMode((current) => ({
        ...current,
        [controlMode]: { ...current[controlMode], unconfirmed: true },
      }));
    }, 0);
    return () => window.clearTimeout(revealSelection);
  }, [controlMode, layers.unconfirmed, selectedLocation?.source]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !mode) return;
    try {
      const renderer = createSpatialRenderer({
        container,
        mode,
        tileConfiguration,
      });
      rendererRef.current = renderer;
      pendingViewportRef.current = {
        mode,
        viewport: viewportsRef.current[mode] ?? null,
      };
      const viewports = viewportsRef.current;
      return () => {
        viewports[mode] = renderer.getViewport();
        if (rendererRef.current === renderer) rendererRef.current = null;
        renderer.destroy();
      };
    } catch {
      queueMicrotask(() =>
        setInitializationError("地图画布初始化失败，请重新进入地图视图。"),
      );
    }
  }, [mode, tileConfiguration]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer || !visibleView) return;
    renderer.render(
      visibleView,
      {
        activeSpatialId,
        selectedEventId,
        selectedLocationId:
          currentView?.locations.find(
            (location) => location.locationId === selectedObjectId,
          )?.locationId ?? null,
        selectedObjectId,
      },
      {
        onActivateLocation(location) {
          const accepted = location.locationId
            ? onSelectLocation(location.locationId)
            : location.events[0]
              ? onSelectEvent(location.events[0].eventId)
              : true;
          if (!accepted) return;
          setOpenedSpatialId(location.spatialId);
          renderer.focusLocation(location.spatialId);
        },
        onClearSelection() {
          if (onClearSelection()) setOpenedSpatialId(null);
        },
        onPreviewPosition(location, position) {
          if (!editSession || editSession.locationId !== location.locationId) return;
          const dirty = !positionsEqual(editSession.baseline, position);
          setEditSession({
            ...editSession,
            preview: position,
            dirty,
            latestChanged: false,
            notice: dirty ? "本地预览尚未写入当前工作稿。" : null,
            status: "idle",
          });
          onPositionEditStateChange?.(true, dirty);
        },
        onTileError() {
          setTileUnavailable(true);
        },
      },
      {
        editableLocationId: editSession?.locationId ?? null,
        layers,
      },
    );
    const pendingViewport = pendingViewportRef.current;
    if (pendingViewport?.mode === mode) {
      if (pendingViewport.viewport) renderer.setViewport(pendingViewport.viewport);
      else renderer.fitAll();
      renderer.invalidateSize();
      pendingViewportRef.current = null;
    }
    if (activeSpatialId) renderer.focusLocation(activeSpatialId);
  }, [
    activeSpatialId,
    currentView,
    editSession,
    layers,
    mode,
    onClearSelection,
    onPositionEditStateChange,
    onSelectEvent,
    onSelectLocation,
    selectedEventId,
    selectedObjectId,
    visibleView,
  ]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (typeof ResizeObserver === "undefined") {
      const handleResize = () => rendererRef.current?.invalidateSize();
      window.addEventListener("resize", handleResize);
      return () => window.removeEventListener("resize", handleResize);
    }
    const observer = new ResizeObserver(() => rendererRef.current?.invalidateSize());
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  function selectMode(nextMode: WorkbenchSpatialMode) {
    if (nextMode === mode) return;
    if (editSession) {
      setEditSession((current) =>
        current
          ? {
              ...current,
              notice: "请先保存或取消位置预览，再切换地图模式。",
            }
          : current,
      );
      return;
    }
    setManualModeSelectionKey(selectionKey);
    setOpenedSpatialId(null);
    setInteractionNotice(null);
    setInitializationError(null);
    setTileUnavailable(false);
    setRequestedMode(nextMode);
  }

  function toggleLayer(layer: SpatialLayerId) {
    const editingLayer =
      editSession && activeBaseLocation?.source === "inferred"
        ? "unconfirmed"
        : editSession
          ? "locations"
          : null;
    if (layer === editingLayer && layers[layer]) {
      setEditSession((current) =>
        current
          ? { ...current, notice: "编辑中的地点图层不能隐藏，请先保存或取消。" }
          : current,
      );
      return;
    }
    setLayersByMode((current) => ({
      ...current,
      [controlMode]: {
        ...current[controlMode],
        [layer]: !current[controlMode][layer],
      },
    }));
  }

  function clearSelection() {
    if (onClearSelection()) setOpenedSpatialId(null);
  }

  function startPositionEdit() {
    if (!activeBaseLocation?.locationId || readOnlyReason) return;
    if (!onSaveSpatialPosition || !onReloadSpatialLocation) {
      setInteractionNotice("当前空间卷宗没有可用的工作稿写入通道。");
      return;
    }
    if (onRequestPositionEdit && !onRequestPositionEdit(activeBaseLocation.locationId)) {
      setInteractionNotice("对象详情有未保存修改，请先保存或取消后再编辑位置。");
      return;
    }
    const nextMode =
      mode === "topology" && activeBaseLocation.source === "schematic"
        ? "scene"
        : mode;
    if (nextMode && nextMode !== mode) {
      setManualModeSelectionKey(selectionKey);
      setRequestedMode(nextMode);
    }
    const layer = activeBaseLocation.source === "inferred" ? "unconfirmed" : "locations";
    setLayersByMode((current) => ({
      ...current,
      [nextMode ?? controlMode]: {
        ...current[nextMode ?? controlMode],
        [layer]: true,
      },
    }));
    setOpenedSpatialId(activeBaseLocation.spatialId);
    setInteractionNotice(null);
    setEditSession({
      locationId: activeBaseLocation.locationId,
      baseline: activeBaseLocation.position,
      preview: activeBaseLocation.position,
      dirty: false,
      latestChanged: false,
      notice:
        activeBaseLocation.source === "inferred"
          ? "保存后将把关系推算位置确认为场景坐标。"
          : "拖动只更新本地预览，保存前不会修改当前工作稿。",
      status: "idle",
    });
    onPositionEditStateChange?.(true, false);
  }

  function cancelPositionEdit() {
    setEditSession(null);
    setInteractionNotice(null);
    onPositionEditStateChange?.(false, false);
  }

  async function savePositionEdit() {
    if (
      !editSession?.dirty ||
      editSession.baseline.kind !== editSession.preview.kind ||
      !onSaveSpatialPosition
    ) return;
    setEditSession((current) =>
      current ? { ...current, notice: "正在写入当前工作稿…", status: "saving" } : current,
    );
    const result = await onSaveSpatialPosition(
      editSession.locationId,
      positionToPayload(editSession.preview),
    );
    if (result === "saved") {
      setEditSession(null);
      onPositionEditStateChange?.(false, false);
      return;
    }
    setEditSession((current) =>
      current
        ? {
            ...current,
            notice:
              result === "conflict"
                ? "当前工作稿已更新。本地预览已保留，请先核对最新版。"
                : "位置未保存，请检查字段或服务状态后重试。",
            status: result === "conflict" ? "conflict" : "error",
          }
        : current,
    );
  }

  async function reviewLatestPosition() {
    if (!editSession || !onReloadSpatialLocation) return;
    setEditSession((current) =>
      current ? { ...current, notice: "正在读取最新版…", status: "reviewing" } : current,
    );
    try {
      const latest = await onReloadSpatialLocation(editSession.locationId);
      if (!latest.found) {
        setEditSession((current) =>
          current
            ? {
                ...current,
                latestChanged: true,
                notice: "最新版已不存在此地点，不能继续保存；可取消本地预览。",
                status: "error",
              }
            : current,
        );
        return;
      }
      const latestPosition = latest.position
        ? payloadToPosition(latest.position)
        : editSession.baseline;
      const coordinateSystemChanged = latestPosition.kind !== editSession.preview.kind;
      const latestChanged = !positionsEqual(editSession.baseline, latestPosition);
      setEditSession((current) =>
        current
          ? {
              ...current,
              baseline: latestPosition,
              dirty: !positionsEqual(latestPosition, current.preview),
              latestChanged,
              notice: coordinateSystemChanged
                ? "最新版已改变坐标系统；请取消预览后在对应模式重新编辑。"
                : latestChanged
                  ? "已载入最新版。请核对坐标差异后再次保存。"
                  : "最新版坐标未变，可以重新保存本地预览。",
              status: coordinateSystemChanged ? "error" : "idle",
            }
          : current,
      );
    } catch {
      setEditSession((current) =>
        current
          ? { ...current, notice: "最新版读取失败，请检查连接后重试。", status: "conflict" }
          : current,
      );
    }
  }

  const statusMessage =
    mode === "geographic" && tileConfiguration.kind === "error"
      ? tileConfiguration.message
      : mode === "geographic" && tileUnavailable
        ? "底图暂不可用；真实坐标点仍可核对。"
        : initializationError ?? interactionNotice;
  const effectiveReadOnlyReason =
    readOnlyReason ??
    (!onSaveSpatialPosition
      ? "此视图只读；只有当前工作稿可以编辑位置。"
      : null);
  const editActionLabel =
    mode === "topology" && activeBaseLocation?.source === "schematic"
      ? "转到场景图编辑"
      : activeBaseLocation?.source === "inferred"
        ? "确认推算位置"
        : "编辑位置";

  return (
    <section className={styles.spatialView} aria-labelledby="spatial-map-heading">
      <header className={styles.header}>
        <div className={styles.heading}>
          <span>空间卷宗 · 空间图</span>
          <h2 id="spatial-map-heading">{title}</h2>
        </div>
        <div className={styles.headerMeta}>
          <small>{meta}</small>
          <div aria-label="空间呈现模式" className={styles.modeTabs} role="group">
            {map.availableModes.map((candidateMode) => (
              <button
                aria-pressed={mode === candidateMode}
                key={candidateMode}
                onClick={() => selectMode(candidateMode)}
                type="button"
              >
                {modeLabels[candidateMode]}
              </button>
            ))}
          </div>
        </div>
      </header>

      <SpatialStatusStrip counts={map.counts} />

      <div className={styles.mapFrame} data-mode={mode ?? "empty"}>
        {mode ? (
          <div
            aria-label={`${modeLabels[mode]}画布`}
            className={styles.mapCanvas}
            data-testid="spatial-map-canvas"
            ref={containerRef}
          />
        ) : (
          <div className={styles.emptyState}>
            <span aria-hidden="true" />
            <strong>当前工作稿没有可呈现的位置</strong>
            <p>地点缺少明确坐标，也没有可用于确定性布局的父级、邻接或移动关系。</p>
          </div>
        )}
        {mode && currentView?.locations.length && !visibleView?.locations.length ? (
          <div className={styles.layerEmptyState} role="status">
            当前图层组合没有可见地点；可在空间核验中开启地点或待确认位置。
          </div>
        ) : null}
        {statusMessage ? (
          <div className={styles.mapStatus} data-tone="warning" role="status">
            <span aria-hidden="true">!</span>
            <p>{statusMessage}</p>
          </div>
        ) : null}
        <SpatialAuditPanel
          layers={layers}
          mobileOpen={auditOpen}
          onMobileOpenChange={setAuditOpen}
          onOpenUnlocated={onOpenLocationDetails}
          onToggleLayer={toggleLayer}
          relations={visibleView?.relations ?? []}
          unlocatedLocations={map.unlocatedLocations}
        />
        {mode ? (
          <div aria-label="地图缩放控制" className={styles.mapControls} role="group">
            <button aria-label="放大地图" onClick={() => rendererRef.current?.zoomIn()} type="button">+</button>
            <button aria-label="缩小地图" onClick={() => rendererRef.current?.zoomOut()} type="button">−</button>
            <button onClick={() => rendererRef.current?.fitAll()} type="button">适合全部</button>
          </div>
        ) : null}
        {mode && mode !== "geographic" ? (
          <div className={styles.planarLegend}>
            <span>无比例测绘底板</span>
            <small>仅表达卷宗中的位置与拓扑，不代表真实道路或边界</small>
          </div>
        ) : null}
        {activeLocation ? (
          <SpatialMapPreviewCard
            activeLocation={activeLocation}
            editActionLabel={editActionLabel}
            editSession={editSession}
            onCancelEdit={cancelPositionEdit}
            onClear={clearSelection}
            onReviewLatest={() => void reviewLatestPosition()}
            onSaveEdit={() => void savePositionEdit()}
            onSelectEvent={onSelectEvent}
            onStartEdit={startPositionEdit}
            readOnlyReason={effectiveReadOnlyReason}
            selectedEventId={selectedEventId}
            showEvents={layers.events}
          />
        ) : null}
      </div>

      <footer className={styles.footer}>
        <p>{note}</p>
        {map.counts.unlocated ? (
          <button
            className={styles.unlocatedStatus}
            onClick={() => {
              if (!layers.unconfirmed) toggleLayer("unconfirmed");
              setAuditOpen(true);
            }}
            type="button"
          >
            <i aria-hidden="true" />{map.counts.unlocated} 个地点未定位
          </button>
        ) : null}
      </footer>
    </section>
  );
}

export default SpatialMapView;
