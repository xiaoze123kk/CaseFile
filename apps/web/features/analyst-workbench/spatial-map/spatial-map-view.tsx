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
  WorkbenchSceneRegion,
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
  WorkbenchSpatialPosition,
  WorkbenchSpatialScene,
  WorkbenchUnlocatedReason,
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
  resolveSceneBackgroundConfiguration,
  type SpatialRenderCallbacks,
  type SpatialRenderSelection,
  type SpatialRenderer,
  type SpatialViewport,
} from "./spatial-renderer";
import styles from "./spatial-map.module.css";

const modeLabels: Record<WorkbenchSpatialMode, string> = {
  geographic: "真实地图",
  scene: "场景图",
  topology: "自动布局",
};

const unlocatedReasonLabels: Record<WorkbenchUnlocatedReason, string> = {
  no_coordinates: "尚无坐标",
  dangling_topology: "空间引用指向不存在的地点",
  dangling_scene_reference: "场景或楼层引用不存在",
};

type SpatialSearchResult =
  | {
      kind: "location";
      location: WorkbenchSpatialLocation;
      mode: WorkbenchSpatialMode;
    }
  | {
      kind: "region";
      region: WorkbenchSceneRegion;
      mode: "scene";
    };

interface SpatialSceneSelection {
  sceneId: string;
  /** Explicitly null means "all floors"; absent state means automatic. */
  floorId: string | null;
}

function sceneForLocation(
  location: WorkbenchSpatialLocation,
  scenes: WorkbenchMapModel["scenes"],
): string | null {
  const position = location.position;
  if (position.kind !== "planar" || !position.sceneId) {
    return null;
  }
  const sceneId = position.sceneId;
  return scenes?.some((scene) => scene.sceneId === sceneId) ? sceneId : null;
}

function spatialDimensionLabel(
  location: WorkbenchSpatialLocation,
  scenes: WorkbenchMapModel["scenes"],
): string | null {
  const position = location.position;
  if (position.kind !== "planar" || !scenes?.length) return null;
  const sceneId = sceneForLocation(location, scenes);
  const scene = scenes.find((candidate) => candidate.sceneId === sceneId);
  if (!scene) return position.sceneId ?? null;
  const floorId = position.floorId;
  if (floorId) {
    const floor = scene.floors.find(
      (candidate) => candidate.floorId === floorId,
    );
    if (floor) return `${scene.name} · ${floor.label}`;
  }
  return scene.name;
}

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
    : {
        kind: "planar",
        x: payload.x,
        y: payload.y,
        ...(payload.scene_id ? { sceneId: payload.scene_id } : {}),
        ...(payload.floor_id ? { floorId: payload.floor_id } : {}),
      };
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
    : {
        coordinate_system: "schematic",
        x: position.x,
        y: position.y,
        ...(position.sceneId ? { scene_id: position.sceneId } : {}),
        ...(position.floorId ? { floor_id: position.floorId } : {}),
      };
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
  const spatialCallbacksRef = useRef<SpatialRenderCallbacks | null>(null);
  const spatialSelectionRef = useRef<SpatialRenderSelection | null>(null);
  const pendingRegionFocusRef = useRef<string | null>(null);
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
  const [auditDesktopCollapsed, setAuditDesktopCollapsed] = useState(false);
  const [highlightedUnlocatedId, setHighlightedUnlocatedId] = useState<string | null>(
    null,
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const [searchActiveIndex, setSearchActiveIndex] = useState(0);
  const [interactionNotice, setInteractionNotice] = useState<string | null>(null);
  const [initializationError, setInitializationError] = useState<string | null>(null);
  const [tileUnavailable, setTileUnavailable] = useState(false);
  const [sceneSelection, setSceneSelection] = useState<SpatialSceneSelection | null>(
    null,
  );
  const [sceneErrorSignature, setSceneErrorSignature] = useState<string | null>(null);
  const tileConfiguration = useMemo(
    () =>
      resolveMapTileConfiguration(
        process.env.NEXT_PUBLIC_CASEFILE_MAP_TILE_URL,
        process.env.NEXT_PUBLIC_CASEFILE_MAP_ATTRIBUTION,
      ),
    [],
  );
  const environmentSceneBackground = useMemo(
    () =>
      resolveSceneBackgroundConfiguration(
        process.env.NEXT_PUBLIC_CASEFILE_MAP_SCENE_IMAGE_URL,
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
  const editLocationId = editSession?.locationId ?? null;
  const editPreview = editSession?.preview ?? null;
  const scenes = useMemo(() => map.scenes ?? [], [map.scenes]);
  const activePositionSceneId =
    activeLocation?.position.kind === "planar"
      ? activeLocation.position.sceneId ?? null
      : null;
  const activePositionFloorId =
    activeLocation?.position.kind === "planar"
      ? activeLocation.position.floorId ?? null
      : null;
  const requestedSceneId = sceneSelection?.sceneId ?? null;
  let activeScene: WorkbenchSpatialScene | null = null;
  if (requestedSceneId) {
    const found = scenes.find((scene) => scene.sceneId === requestedSceneId);
    if (found) activeScene = found;
  }
  if (!activeScene && activePositionSceneId) {
    const found = scenes.find((scene) => scene.sceneId === activePositionSceneId);
    if (found) activeScene = found;
  }
  if (!activeScene && scenes[0]) activeScene = scenes[0];
  const activeSceneId = activeScene?.sceneId ?? null;
  const activeFloorId = (() => {
    if (!activeScene) return null;
    if (sceneSelection && sceneSelection.sceneId === activeScene.sceneId) {
      const requestedFloorId = sceneSelection.floorId;
      if (
        requestedFloorId === null ||
        activeScene.floors.some((floor) => floor.floorId === requestedFloorId)
      ) {
        return requestedFloorId;
      }
    }
    if (
      activePositionSceneId === activeScene.sceneId &&
      activePositionFloorId &&
      activeScene.floors.some((floor) => floor.floorId === activePositionFloorId)
    ) {
      return activePositionFloorId;
    }
    return activeScene.floors[0]?.floorId ?? null;
  })();
  const activeSceneBackground = activeScene?.floors.find(
    (floor) => floor.floorId === activeFloorId,
  ) ?? null;
  const sceneBackground = useMemo(() => {
    if (mode !== "scene") return null;
    const sceneImageUrl =
      activeSceneBackground?.backgroundImageUrl ??
      activeScene?.backgroundImageUrl ??
      environmentSceneBackground?.url ??
      null;
    if (!sceneImageUrl) return null;
    return resolveSceneBackgroundConfiguration(sceneImageUrl, {
      alt: activeSceneBackground
        ? `场景图：${activeScene?.name ?? ""} ${activeSceneBackground.label}`
        : activeScene
          ? `场景图：${activeScene.name}`
          : environmentSceneBackground?.alt ?? "场景底图",
      imageWidth:
        activeSceneBackground?.imageWidth ?? activeScene?.imageWidth ?? null,
      imageHeight:
        activeSceneBackground?.imageHeight ?? activeScene?.imageHeight ?? null,
    });
  }, [
    activeScene,
    activeSceneBackground,
    environmentSceneBackground,
    mode,
  ]);
  const sceneBackgroundSignature = sceneBackground
    ? JSON.stringify(sceneBackground)
    : null;
  const selectedLocationId = useMemo(
    () =>
      currentView?.locations.find(
        (location) => location.locationId === selectedObjectId,
      )?.locationId ?? null,
    [currentView, selectedObjectId],
  );
  const spatialSelection = useMemo<SpatialRenderSelection>(
    () => ({
      activeSpatialId,
      selectedEventId,
      selectedLocationId,
      selectedObjectId,
    }),
    [activeSpatialId, selectedEventId, selectedLocationId, selectedObjectId],
  );
  const sceneView = useMemo(() => {
    if (!currentView) return null;
    if (mode !== "scene" || !activeSceneId) return currentView;
    const locations = currentView.locations.filter((location) => {
      if (location.position.kind !== "planar") return false;
      if (
        location.position.sceneId &&
        location.position.sceneId !== activeSceneId
      ) {
        return false;
      }
      if (
        activeFloorId &&
        location.position.floorId &&
        location.position.floorId !== activeFloorId
      ) {
        return false;
      }
      return true;
    });
    const visibleIds = new Set(
      locations.flatMap((location) =>
        location.locationId ? [location.locationId] : [],
      ),
    );
    return {
      ...currentView,
      locations,
      relations: currentView.relations.filter(
        (relation) =>
          visibleIds.has(relation.fromLocationId) &&
          visibleIds.has(relation.toLocationId),
      ),
      regions: (currentView.regions ?? []).filter(
        (region) => region.sceneId === activeSceneId,
      ),
    };
  }, [activeFloorId, activeSceneId, currentView, mode]);
  const visibleView = useMemo(() => {
    if (!sceneView) return null;
    const filtered = filterWorkbenchSpatialView(sceneView, layers);
    return {
      ...filtered,
      locations: filtered.locations.map((location) =>
        editLocationId === location.locationId && editPreview
          ? { ...location, position: editPreview }
          : location,
      ),
    };
  }, [editLocationId, editPreview, layers, sceneView]);

  const spatialSearchIndex = useMemo(() => {
    const bySpatialId = new Map<string, SpatialSearchResult>();
    for (const candidateMode of map.availableModes) {
      for (const location of map.views[candidateMode].locations) {
        if (!bySpatialId.has(location.spatialId)) {
          bySpatialId.set(location.spatialId, {
            kind: "location",
            location,
            mode: candidateMode,
          });
        }
      }
    }
    for (const scene of scenes) {
      for (const region of scene.regions) {
        bySpatialId.set(`region:${region.sceneId}:${region.regionId}`, {
          kind: "region",
          region,
          mode: "scene",
        });
      }
    }
    return [...bySpatialId.values()].sort((left, right) =>
      (left.kind === "location"
        ? left.location.spatialId
        : left.region.regionId
      ).localeCompare(
        right.kind === "location" ? right.location.spatialId : right.region.regionId,
      ),
    );
  }, [map, scenes]);

  const normalizedSearchQuery = searchQuery.trim().toLocaleLowerCase("zh-CN");
  const spatialSearchResults = useMemo(() => {
    if (!normalizedSearchQuery) {
      return { located: [] as SpatialSearchResult[], unlocated: [] };
    }
    const matches = (label: string, id: string | null) =>
      label.toLocaleLowerCase("zh-CN").includes(normalizedSearchQuery) ||
      Boolean(id?.toLocaleLowerCase("zh-CN").includes(normalizedSearchQuery));
    const located = spatialSearchIndex
      .filter((result) => {
        if (result.kind === "region") {
          return (
            matches(result.region.name, result.region.regionId) ||
            matches(result.region.sceneId, null)
          );
        }
        const location = result.location;
        const dimension = spatialDimensionLabel(location, scenes);
        return (
          matches(location.label, location.locationId) ||
          Boolean(dimension?.toLocaleLowerCase("zh-CN").includes(normalizedSearchQuery)) ||
          location.events.some((event) =>
            event.label.toLocaleLowerCase("zh-CN").includes(normalizedSearchQuery),
          )
        );
      })
      .slice(0, 5);
    const unlocated = map.unlocatedLocations
      .filter((location) => matches(location.label, location.locationId))
      .slice(0, 3);
    return { located, unlocated };
  }, [map.unlocatedLocations, normalizedSearchQuery, scenes, spatialSearchIndex]);

  function buildSpatialCallbacks(): SpatialRenderCallbacks {
    return {
      onActivateLocation(location) {
        const accepted = location.locationId
          ? onSelectLocation(location.locationId)
          : location.events[0]
            ? onSelectEvent(location.events[0].eventId)
            : true;
        if (!accepted) return;
        setOpenedSpatialId(location.spatialId);
        rendererRef.current?.focusLocation(location.spatialId);
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
      onSceneBackgroundError() {
        setSceneErrorSignature(sceneBackgroundSignature);
      },
    };
  }

  useEffect(() => {
    spatialSelectionRef.current = spatialSelection;
    spatialCallbacksRef.current = buildSpatialCallbacks();
    rendererRef.current?.setCallbacks(spatialCallbacksRef.current);
  });

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
    const spatialCallbacks = spatialCallbacksRef.current;
    if (!renderer || !visibleView || !spatialCallbacks) return;
    renderer.render(
      visibleView,
      spatialSelectionRef.current ?? {
        activeSpatialId: null,
        selectedEventId: null,
        selectedLocationId: null,
        selectedObjectId: null,
      },
      spatialCallbacks,
      {
        editableLocationId: editLocationId,
        layers,
        sceneBackground,
        regions: visibleView.regions ?? [],
      },
    );
    const pendingViewport = pendingViewportRef.current;
    if (pendingViewport?.mode === mode) {
      if (pendingViewport.viewport) renderer.setViewport(pendingViewport.viewport);
      else renderer.fitAll();
      renderer.invalidateSize();
      pendingViewportRef.current = null;
    }
    const pendingRegionFocus = pendingRegionFocusRef.current;
    if (pendingRegionFocus && mode === "scene") {
      renderer.focusRegion(pendingRegionFocus);
      pendingRegionFocusRef.current = null;
    }
  }, [editLocationId, layers, mode, sceneBackground, visibleView]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const pendingRegionFocus = pendingRegionFocusRef.current;
    if (!renderer || !pendingRegionFocus || mode !== "scene") return;
    renderer.focusRegion(pendingRegionFocus);
    pendingRegionFocusRef.current = null;
  }, [mode, sceneSelection]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    if (spatialSelectionRef.current) {
      renderer.updateSelection(spatialSelectionRef.current);
    }
    if (activeSpatialId) renderer.focusLocation(activeSpatialId);
  }, [activeSpatialId, mode, selectedEventId, selectedLocationId, selectedObjectId]);

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
    setSceneErrorSignature(null);
    pendingRegionFocusRef.current = null;
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
    if (layer === "locations" && layers.locations) {
      // Event markers are count badges attached to location markers, so
      // hiding locations without hiding events would leave a checked but
      // invisible layer behind.
      setLayersByMode((current) => ({
        ...current,
        [controlMode]: {
          ...current[controlMode],
          locations: false,
          events: false,
        },
      }));
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

  function selectScene(sceneId: string) {
    const scene = scenes.find((candidate) => candidate.sceneId === sceneId);
    if (!scene) return;
    setSceneSelection({
      sceneId,
      floorId: scene.floors[0]?.floorId ?? null,
    });
    setSceneErrorSignature(null);
  }

  function selectFloor(floorId: string | null) {
    if (!activeSceneId) return;
    setSceneSelection({ sceneId: activeSceneId, floorId });
    setSceneErrorSignature(null);
  }

  function clearSelection() {
    if (onClearSelection()) setOpenedSpatialId(null);
  }

  function blockSearchWhileEditing(): boolean {
    if (!editSession) return false;
    setEditSession((current) =>
      current
        ? {
            ...current,
            notice: "请先保存或取消位置预览，再定位其他地点。",
          }
        : current,
    );
    return true;
  }

  function chooseSearchResult(result: SpatialSearchResult) {
    if (blockSearchWhileEditing()) return;
    if (result.kind === "region") {
      const scene = scenes.find(
        (candidate) => candidate.sceneId === result.region.sceneId,
      );
      setRequestedMode("scene");
      setSceneSelection({
        sceneId: result.region.sceneId,
        floorId: scene?.floors[0]?.floorId ?? null,
      });
      setOpenedSpatialId(null);
      pendingRegionFocusRef.current = `${result.region.sceneId}:${result.region.regionId}`;
      setInteractionNotice(
        `已定位到区域「${result.region.name}」；区域只来自卷宗数据。`,
      );
      setHighlightedUnlocatedId(null);
      setSearchQuery("");
      setSearchActiveIndex(0);
      setSearchFocused(false);
      return;
    }
    const location = result.location;
    const accepted = location.locationId
      ? onSelectLocation(location.locationId)
      : location.events[0]
        ? onSelectEvent(location.events[0].eventId)
        : true;
    if (!accepted) return;
    if (location.position.kind === "planar" && location.position.sceneId) {
      setSceneSelection({
        sceneId: location.position.sceneId,
        floorId: location.position.floorId ?? null,
      });
    }
    setRequestedMode(result.mode);
    setOpenedSpatialId(location.spatialId);
    setInteractionNotice(null);
    setHighlightedUnlocatedId(null);
    setSearchQuery("");
    setSearchActiveIndex(0);
    setSearchFocused(false);
  }

  function chooseUnlocatedResult(locationId: string) {
    if (blockSearchWhileEditing()) return;
    setLayersByMode((current) => ({
      ...current,
      [controlMode]: { ...current[controlMode], unconfirmed: true },
    }));
    setAuditDesktopCollapsed(false);
    setAuditOpen(true);
    setHighlightedUnlocatedId(locationId);
    setSearchQuery("");
    setSearchActiveIndex(0);
    setSearchFocused(false);
  }

  function clearSearch() {
    setSearchQuery("");
    setSearchActiveIndex(0);
    setSearchFocused(false);
    setHighlightedUnlocatedId(null);
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

  const sceneBackgroundFailed =
    mode === "scene" &&
    sceneErrorSignature !== null &&
    sceneErrorSignature === sceneBackgroundSignature;
  const statusMessage =
    mode === "geographic" && tileConfiguration.kind === "error"
      ? tileConfiguration.message
      : mode === "geographic" && tileUnavailable
        ? "底图暂不可用；真实坐标点仍可核对。"
        : sceneBackgroundFailed
          ? "场景底图加载失败，已回退网格纸；地点坐标仍可核对。"
          : initializationError ?? interactionNotice;
  const effectiveReadOnlyReason =
    readOnlyReason ??
    (!onSaveSpatialPosition
      ? "此视图只读；只有当前工作稿可以编辑位置。"
      : null);
  const searchOptionCount =
    spatialSearchResults.located.length + spatialSearchResults.unlocated.length;
  const activeSearchOptionIndex = Math.min(
    Math.max(0, searchActiveIndex),
    Math.max(0, searchOptionCount - 1),
  );
  const activeSearchOptionId = searchOptionCount
    ? `spatial-search-option-${activeSearchOptionIndex}`
    : undefined;
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
          <div className={styles.searchBox} role="search">
            <input
              aria-activedescendant={activeSearchOptionId}
              aria-autocomplete="list"
              aria-controls="spatial-search-results"
              aria-label="搜索地点、事件或区域"
              autoComplete="off"
              onChange={(event) => {
                setSearchQuery(event.target.value);
                setSearchActiveIndex(0);
              }}
              onFocus={() => setSearchFocused(true)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  clearSearch();
                  return;
                }
                if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  if (!searchOptionCount) return;
                  event.preventDefault();
                  setSearchActiveIndex((current) =>
                    event.key === "ArrowDown"
                      ? Math.min(searchOptionCount - 1, current + 1)
                      : Math.max(0, current - 1),
                  );
                  return;
                }
                if (event.key !== "Enter" || !searchOptionCount) return;
                event.preventDefault();
                if (activeSearchOptionIndex < spatialSearchResults.located.length) {
                  chooseSearchResult(
                    spatialSearchResults.located[activeSearchOptionIndex],
                  );
                } else {
                  chooseUnlocatedResult(
                    spatialSearchResults.unlocated[
                      activeSearchOptionIndex - spatialSearchResults.located.length
                    ].locationId,
                  );
                }
              }}
              onBlur={() => setSearchFocused(false)}
              placeholder="搜索地点 / 事件 / 区域"
              type="search"
              value={searchQuery}
            />
            {searchQuery ? (
              <button
                aria-label="清除搜索"
                className={styles.searchClear}
                onClick={clearSearch}
                type="button"
              >
                ×
              </button>
            ) : null}
            {searchFocused && searchQuery.trim() ? (
              <div
                className={styles.searchResults}
                id="spatial-search-results"
                role="listbox"
              >
                {spatialSearchResults.located.length ||
                spatialSearchResults.unlocated.length ? (
                  <>
                    {spatialSearchResults.located.map((result, index) => {
                      const key =
                        result.kind === "region"
                          ? `region:${result.region.sceneId}:${result.region.regionId}`
                          : `${result.mode}:${result.location.spatialId}`;
                      const label =
                        result.kind === "region"
                          ? result.region.name
                          : result.location.label;
                      const dimension =
                        result.kind === "location"
                          ? spatialDimensionLabel(result.location, scenes)
                          : null;
                      const detail =
                        result.kind === "region"
                          ? `${result.region.sceneId} · 卷宗区域`
                          : `${result.location.locationId ?? "本地样例"} · ${result.location.events.length} 个事件${dimension ? ` · ${dimension}` : ""}`;
                      return (
                        <button
                          aria-selected={index === activeSearchOptionIndex}
                          data-kind={result.kind === "region" ? "scene-region" : result.mode}
                          id={`spatial-search-option-${index}`}
                          key={key}
                          onClick={() => chooseSearchResult(result)}
                          onMouseDown={(event) => event.preventDefault()}
                          role="option"
                          type="button"
                        >
                          <span>
                            {result.kind === "region"
                              ? "场景区域"
                              : modeLabels[result.mode]}
                          </span>
                          <b>{label}</b>
                          <small>{detail}</small>
                        </button>
                      );
                    })}
                    {spatialSearchResults.unlocated.map((location, index) => {
                      const optionIndex =
                        spatialSearchResults.located.length + index;
                      return (
                        <button
                          aria-selected={optionIndex === activeSearchOptionIndex}
                          data-kind="unlocated"
                          id={`spatial-search-option-${optionIndex}`}
                          key={`unlocated:${location.locationId}`}
                          onClick={() => chooseUnlocatedResult(location.locationId)}
                          onMouseDown={(event) => event.preventDefault()}
                          role="option"
                          type="button"
                        >
                          <span>未定位</span>
                          <b>{location.label}</b>
                          <small>
                            {location.locationId} · {unlocatedReasonLabels[location.reason]}
                          </small>
                        </button>
                      );
                    })}
                  </>
                ) : (
                  <p>没有匹配的地点、事件或区域。</p>
                )}
              </div>
            ) : null}
          </div>
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
        {mode === "scene" && activeScene ? (
          <div className={styles.sceneDock} aria-label="场景与楼层">
            {scenes.length > 1 ? (
              <label className={styles.sceneSelect}>
                <span>场景</span>
                <select
                  aria-label="选择场景"
                  onChange={(event) => selectScene(event.target.value)}
                  value={activeScene.sceneId}
                >
                  {scenes.map((scene) => (
                    <option key={scene.sceneId} value={scene.sceneId}>
                      {scene.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {activeScene.floors.length ? (
              <div className={styles.floorTabs} role="group" aria-label="楼层">
                <button
                  aria-pressed={activeFloorId === null}
                  onClick={() => selectFloor(null)}
                  type="button"
                >
                  全部
                </button>
                {activeScene.floors.map((floor) => (
                  <button
                    aria-pressed={activeFloorId === floor.floorId}
                    key={floor.floorId}
                    onClick={() => selectFloor(floor.floorId)}
                    type="button"
                  >
                    {floor.label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
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
            {mode === "scene" && activeScene
              ? "当前楼层没有可见地点；切换楼层，或在空间核验中开启地点与待确认位置。"
              : "当前图层组合没有可见地点；可在空间核验中开启地点或待确认位置。"}
          </div>
        ) : null}
        {statusMessage ? (
          <div className={styles.mapStatus} data-tone="warning" role="status">
            <span aria-hidden="true">!</span>
            <p>{statusMessage}</p>
          </div>
        ) : null}
        <SpatialAuditPanel
          desktopCollapsed={auditDesktopCollapsed}
          highlightedUnlocatedId={highlightedUnlocatedId}
          layers={layers}
          mobileOpen={auditOpen}
          onDesktopCollapsedChange={setAuditDesktopCollapsed}
          onMobileOpenChange={setAuditOpen}
          onOpenUnlocated={onOpenLocationDetails}
          onToggleLayer={toggleLayer}
          regions={visibleView?.regions ?? []}
          relations={visibleView?.relations ?? []}
          sceneName={activeScene?.name ?? null}
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
            <span>
              {mode === "scene" && sceneBackground ? "场景底图" : "无比例测绘底板"}
            </span>
            <small>
              {mode === "scene" && sceneBackground
                ? "平面图仅作为场景参照；地点坐标仍以卷宗为准"
                : "仅表达卷宗中的位置与拓扑，不代表真实道路或边界"}
            </small>
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
              setAuditDesktopCollapsed(false);
              setHighlightedUnlocatedId(null);
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
