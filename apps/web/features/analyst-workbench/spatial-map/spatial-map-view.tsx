"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type {
  WorkbenchMapModel,
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
} from "../workbench-real-data-types";
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

const sourceLabels: Record<WorkbenchSpatialLocation["source"], string> = {
  wgs84: "WGS84 地理坐标",
  schematic: "明确场景坐标",
  inferred: "关系推算位置",
};

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

function coordinateLabel(location: WorkbenchSpatialLocation): string {
  return location.position.kind === "wgs84"
    ? `${location.position.latitude.toFixed(5)}, ${location.position.longitude.toFixed(5)}`
    : `X ${location.position.x.toFixed(1)} / Y ${location.position.y.toFixed(1)}`;
}

export interface SpatialMapViewProps {
  map: WorkbenchMapModel;
  title: string;
  meta: string;
  note: string;
  selectedObjectId: string | null;
  selectedEventId: string | null;
  onSelectLocation: (locationId: string) => boolean;
  onSelectEvent: (eventId: string) => boolean;
  onClearSelection: () => boolean;
}

export function SpatialMapView({
  map,
  title,
  meta,
  note,
  selectedObjectId,
  selectedEventId,
  onSelectLocation,
  onSelectEvent,
  onClearSelection,
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
  const [requestedMode, setRequestedMode] = useState<WorkbenchSpatialMode | null>(
    map.defaultMode,
  );
  const [manualModeSelectionKey, setManualModeSelectionKey] = useState<string | null>(
    null,
  );
  const [openedSpatialId, setOpenedSpatialId] = useState<string | null>(null);
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
  const currentView = mode ? map.views[mode] : null;
  const selectedLocation =
    currentView?.locations.find((location) =>
      relatedLocation(location, selectedObjectId, selectedEventId),
    ) ?? null;
  const openedLocation =
    currentView?.locations.find((location) => location.spatialId === openedSpatialId) ??
    null;
  const activeLocation = openedLocation ?? selectedLocation;
  const activeSpatialId = activeLocation?.spatialId ?? null;

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
    if (!renderer || !currentView) return;
    renderer.render(
      currentView,
      {
        activeSpatialId,
        selectedEventId,
        selectedLocationId:
          currentView.locations.find(
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
        onTileError() {
          setTileUnavailable(true);
        },
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
    mode,
    onClearSelection,
    onSelectEvent,
    onSelectLocation,
    selectedEventId,
    selectedObjectId,
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
    setManualModeSelectionKey(selectionKey);
    setOpenedSpatialId(null);
    setInitializationError(null);
    setTileUnavailable(false);
    setRequestedMode(nextMode);
  }

  function clearSelection() {
    if (onClearSelection()) setOpenedSpatialId(null);
  }

  const statusMessage =
    mode === "geographic" && tileConfiguration.kind === "error"
      ? tileConfiguration.message
      : mode === "geographic" && tileUnavailable
        ? "底图暂不可用；真实坐标点仍可核对。"
        : initializationError;

  return (
    <section className={styles.spatialView} aria-labelledby="spatial-map-heading">
      <header className={styles.header}>
        <div className={styles.heading}>
          <span>空间卷宗 · SPATIAL FILE</span>
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

      {map.availableModes.length ? (
        <div className={styles.mapFrame} data-mode={mode}>
          <div
            aria-label={`${mode ? modeLabels[mode] : "空间"}画布`}
            className={styles.mapCanvas}
            data-testid="spatial-map-canvas"
            ref={containerRef}
          />
          {statusMessage ? (
            <div className={styles.mapStatus} data-tone="warning" role="status">
              <span aria-hidden="true">!</span>
              <p>{statusMessage}</p>
            </div>
          ) : null}
          <div aria-label="地图缩放控制" className={styles.mapControls} role="group">
            <button aria-label="放大地图" onClick={() => rendererRef.current?.zoomIn()} type="button">+</button>
            <button aria-label="缩小地图" onClick={() => rendererRef.current?.zoomOut()} type="button">−</button>
            <button onClick={() => rendererRef.current?.fitAll()} type="button">适合全部</button>
          </div>
          {mode !== "geographic" ? (
            <div className={styles.planarLegend}>
              <span>无比例测绘底板</span>
              <small>仅表达卷宗中的位置与拓扑，不代表真实道路或边界</small>
            </div>
          ) : null}
          {activeLocation ? (
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
                <button aria-label="关闭地点快览" onClick={clearSelection} type="button">×</button>
              </header>
              <dl>
                <div><dt>坐标来源</dt><dd data-source={activeLocation.source}>{sourceLabels[activeLocation.source]}</dd></div>
                <div><dt>坐标</dt><dd>{coordinateLabel(activeLocation)}</dd></div>
                <div><dt>关联对象</dt><dd>{activeLocation.relatedObjectIds.length}</dd></div>
                <div><dt>地点事件</dt><dd>{activeLocation.events.length}</dd></div>
              </dl>
              {activeLocation.events.length ? (
                <ol aria-label="地点关联事件">
                  {activeLocation.events.slice(0, 4).map((event) => (
                    <li key={event.eventId}>
                      <button
                        aria-pressed={selectedEventId === event.eventId}
                        onClick={() => onSelectEvent(event.eventId)}
                        onKeyDown={(keyboardEvent) => {
                          if (
                            keyboardEvent.key !== "Enter" &&
                            keyboardEvent.key !== " "
                          ) {
                            return;
                          }
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
              )}
              {activeLocation.events.length > 4 ? (
                <small className={styles.moreEvents}>另有 {activeLocation.events.length - 4} 个事件</small>
              ) : null}
            </aside>
          ) : null}
        </div>
      ) : (
        <div className={styles.emptyState}>
          <span aria-hidden="true" />
          <strong>当前工作稿没有可呈现的位置</strong>
          <p>地点缺少明确坐标，也没有可用于确定性布局的父级、邻接或移动关系。</p>
        </div>
      )}

      <footer className={styles.footer}>
        <p>{note}</p>
        {map.counts.unlocated ? (
          <span className={styles.unlocatedStatus}>
            <i aria-hidden="true" />{map.counts.unlocated} 个地点未定位
          </span>
        ) : null}
      </footer>
    </section>
  );
}

export default SpatialMapView;
