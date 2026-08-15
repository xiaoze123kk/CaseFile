import * as L from "leaflet";

import type {
  SpatialLayerVisibility,
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
  WorkbenchSpatialPosition,
  WorkbenchSpatialRelation,
  WorkbenchSpatialView,
} from "../workbench-real-data-types";

export interface SpatialViewport {
  center: [number, number];
  zoom: number;
}

export type MapTileConfiguration =
  | {
      kind: "tiles";
      url: string;
      attribution: string;
      provider: "osm" | "custom";
    }
  | {
      kind: "error";
      message: string;
    };

export interface SpatialRenderSelection {
  selectedLocationId: string | null;
  selectedEventId: string | null;
  selectedObjectId: string | null;
  activeSpatialId: string | null;
}

export interface SpatialRenderCallbacks {
  onActivateLocation: (location: WorkbenchSpatialLocation) => void;
  onClearSelection: () => void;
  onPreviewPosition: (
    location: WorkbenchSpatialLocation,
    position: WorkbenchSpatialPosition,
  ) => void;
  onTileError: () => void;
}

export interface SceneBackgroundConfig {
  url: string;
}

export interface SpatialRenderOptions {
  editableLocationId: string | null;
  layers: SpatialLayerVisibility;
  /** Optional deployment-level floor plan shown in planar modes. */
  sceneBackground?: SceneBackgroundConfig | null;
}

export function resolveSceneBackgroundConfiguration(
  url: string | undefined,
): SceneBackgroundConfig | null {
  const normalizedUrl = url?.trim() ?? "";
  return normalizedUrl ? { url: normalizedUrl } : null;
}

export interface SpatialRenderer {
  render: (
    view: WorkbenchSpatialView,
    selection: SpatialRenderSelection,
    callbacks: SpatialRenderCallbacks,
    options: SpatialRenderOptions,
  ) => void;
  setCallbacks: (callbacks: SpatialRenderCallbacks) => void;
  updateSelection: (selection: SpatialRenderSelection) => void;
  fitAll: () => void;
  focusLocation: (spatialId: string) => void;
  zoomIn: () => void;
  zoomOut: () => void;
  getViewport: () => SpatialViewport;
  setViewport: (viewport: SpatialViewport) => void;
  invalidateSize: () => void;
  destroy: () => void;
}

const defaultOsmTileUrl = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const defaultOsmAttribution =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export function resolveMapTileConfiguration(
  tileUrl: string | undefined,
  attribution: string | undefined,
): MapTileConfiguration {
  const normalizedUrl = tileUrl?.trim() ?? "";
  const normalizedAttribution = attribution?.trim() ?? "";
  if (!normalizedUrl && !normalizedAttribution) {
    return {
      kind: "tiles",
      url: defaultOsmTileUrl,
      attribution: defaultOsmAttribution,
      provider: "osm",
    };
  }
  if (normalizedUrl && normalizedAttribution) {
    return {
      kind: "tiles",
      url: normalizedUrl,
      attribution: normalizedAttribution,
      provider: "custom",
    };
  }
  return {
    kind: "error",
    message:
      "地图瓦片配置不完整：请同时设置瓦片地址与署名，或同时留空以使用 OpenStreetMap。",
  };
}

function markerCoordinate(
  mode: WorkbenchSpatialMode,
  location: WorkbenchSpatialLocation,
): L.LatLngExpression | null {
  if (mode === "geographic" && location.position.kind === "wgs84") {
    return [location.position.latitude, location.position.longitude];
  }
  if (mode !== "geographic" && location.position.kind === "planar") {
    return [100 - location.position.y, location.position.x];
  }
  return null;
}

function positionFromCoordinate(
  mode: WorkbenchSpatialMode,
  coordinate: L.LatLng,
): WorkbenchSpatialPosition {
  return mode === "geographic"
    ? {
        kind: "wgs84",
        latitude: Math.min(90, Math.max(-90, coordinate.lat)),
        longitude: Math.min(180, Math.max(-180, coordinate.lng)),
      }
    : {
        kind: "planar",
        x: Math.min(100, Math.max(0, coordinate.lng)),
        y: Math.min(100, Math.max(0, 100 - coordinate.lat)),
      };
}

function nudgePosition(
  position: WorkbenchSpatialPosition,
  key: string,
  largeStep: boolean,
): WorkbenchSpatialPosition {
  if (position.kind === "wgs84") {
    const step = largeStep ? 0.001 : 0.0001;
    return {
      kind: "wgs84",
      latitude: Math.min(
        90,
        Math.max(
          -90,
          position.latitude + (key === "ArrowUp" ? step : key === "ArrowDown" ? -step : 0),
        ),
      ),
      longitude: Math.min(
        180,
        Math.max(
          -180,
          position.longitude + (key === "ArrowRight" ? step : key === "ArrowLeft" ? -step : 0),
        ),
      ),
    };
  }
  const step = largeStep ? 2 : 0.5;
  return {
    kind: "planar",
    x: Math.min(
      100,
      Math.max(0, position.x + (key === "ArrowRight" ? step : key === "ArrowLeft" ? -step : 0)),
    ),
    y: Math.min(
      100,
      Math.max(0, position.y + (key === "ArrowDown" ? step : key === "ArrowUp" ? -step : 0)),
    ),
  };
}

function markerLabel(location: WorkbenchSpatialLocation, editable: boolean): string {
  const source =
    location.source === "wgs84"
      ? "地理坐标"
      : location.source === "schematic"
        ? "场景坐标"
        : "推算位置";
  const eventCount = location.events.length
    ? `，${location.events.length} 个事件`
    : "，没有关联事件";
  return `${location.label}，${source}${eventCount}${editable ? "，位置可编辑，可拖动或使用方向键微调" : ""}`;
}

function markerHtml(
  location: WorkbenchSpatialLocation,
  showEvents: boolean,
  editable: boolean,
): HTMLElement {
  const body = document.createElement("span");
  body.className = "casefile-spatial-marker__body";

  const stamp = document.createElement("span");
  stamp.className = "casefile-spatial-marker__stamp";
  stamp.setAttribute("aria-hidden", "true");
  body.append(stamp);

  const label = document.createElement("span");
  label.className = "casefile-spatial-marker__label";
  label.textContent = location.label;
  body.append(label);

  if (showEvents && location.events.length) {
    const count = document.createElement("span");
    count.className = "casefile-spatial-marker__count";
    count.textContent = String(location.events.length);
    count.setAttribute("aria-hidden", "true");
    body.append(count);
  }
  if (editable) {
    const editFlag = document.createElement("span");
    editFlag.className = "casefile-spatial-marker__edit";
    editFlag.textContent = "编辑中";
    editFlag.setAttribute("aria-hidden", "true");
    body.append(editFlag);
  }
  return body;
}

function isSelected(
  location: WorkbenchSpatialLocation,
  selection: SpatialRenderSelection,
): boolean {
  return (
    selection.activeSpatialId === location.spatialId ||
    selection.selectedLocationId === location.locationId ||
    location.events.some((event) => event.eventId === selection.selectedEventId) ||
    Boolean(
      selection.selectedObjectId &&
        location.relatedObjectIds.includes(selection.selectedObjectId),
    )
  );
}

type SpatialLabelTier = "none" | "important" | "all";

const spatialLabelTierClasses: Record<SpatialLabelTier, string> = {
  none: "casefile-spatial-marker--labels-none",
  important: "casefile-spatial-marker--labels-important",
  all: "casefile-spatial-marker--labels-all",
};

function locationHasSpatialContent(location: WorkbenchSpatialLocation): boolean {
  return location.events.length > 0 || location.relatedObjectIds.length > 0;
}

function resolveLabelTier(
  mode: WorkbenchSpatialMode,
  zoom: number,
  location: WorkbenchSpatialLocation,
  selected: boolean,
): SpatialLabelTier {
  if (selected) return "all";
  // Geographic map: far zoom shows only locations carrying events or object
  // relations. Planar maps start at zoom 1 with the full 0..100 canvas, so
  // only zooming out beyond that hides secondary labels.
  const farThreshold = mode === "geographic" ? 8 : 1;
  if (zoom < farThreshold) {
    return locationHasSpatialContent(location) ? "important" : "none";
  }
  return "all";
}

export function createSpatialRenderer(input: {
  container: HTMLElement;
  mode: WorkbenchSpatialMode;
  tileConfiguration: MapTileConfiguration;
}): SpatialRenderer {
  const map = L.map(input.container, {
    attributionControl: input.mode === "geographic",
    crs: input.mode === "geographic" ? L.CRS.EPSG3857 : L.CRS.Simple,
    keyboard: true,
    minZoom: input.mode === "geographic" ? 2 : -2,
    maxZoom: input.mode === "geographic" ? 19 : 5,
    preferCanvas: false,
    scrollWheelZoom: true,
    touchZoom: true,
    worldCopyJump: input.mode === "geographic",
    zoomControl: false,
  });
  const markers = L.layerGroup().addTo(map);
  const relations = L.layerGroup().addTo(map);
  const markerById = new Map<string, L.Marker>();
  const locationBySpatialId = new Map<string, WorkbenchSpatialLocation>();
  const relationLineById = new Map<
    string,
    { line: L.Polyline; relation: WorkbenchSpatialRelation }
  >();
  const relationArrows: Array<{
    arrow: L.Marker;
    from: L.LatLng;
    to: L.LatLng;
  }> = [];
  let callbacks: SpatialRenderCallbacks | null = null;
  let currentSelection: SpatialRenderSelection | null = null;
  let sceneImage: L.ImageOverlay | null = null;
  let tileErrorReported = false;

  if (input.mode === "geographic") {
    if (input.tileConfiguration.kind === "tiles") {
      L.tileLayer(input.tileConfiguration.url, {
        attribution: input.tileConfiguration.attribution,
        crossOrigin: true,
        maxZoom: 19,
      })
        .on("tileerror", () => {
          if (tileErrorReported) return;
          tileErrorReported = true;
          callbacks?.onTileError();
        })
        .addTo(map);
    }
    L.control
      .scale({ imperial: false, maxWidth: 180, position: "bottomleft" })
      .addTo(map);
    map.setView([30, 105], 3);
  } else {
    map.setMaxBounds(
      L.latLngBounds(
        L.latLng(-25, -25),
        L.latLng(125, 125),
      ),
    );
    map.setView([50, 50], 1);
  }

  map.on("click", (event) => {
    const target = event.originalEvent?.target;
    if (
      target instanceof Element &&
      target.closest(".casefile-spatial-marker")
    ) {
      return;
    }
    callbacks?.onClearSelection();
  });

  function activeLocationId(
    selection: SpatialRenderSelection | null,
  ): string | null {
    return selection?.selectedLocationId ?? selection?.activeSpatialId ?? null;
  }

  function isRelationActive(
    relation: WorkbenchSpatialRelation,
    selection: SpatialRenderSelection | null,
  ): boolean {
    const locationId = activeLocationId(selection);
    return Boolean(
      locationId &&
        (relation.fromLocationId === locationId ||
          relation.toLocationId === locationId),
    );
  }

  function bindRelationTooltip(
    line: L.Polyline,
    relation: WorkbenchSpatialRelation,
    permanent: boolean,
  ) {
    line.unbindTooltip();
    line.bindTooltip(
      relation.kind === "travel" ? `→ ${relation.label}` : relation.label,
      {
        className: `casefile-spatial-relation-label casefile-spatial-relation-label--${relation.kind}`,
        direction: "center",
        interactive: false,
        permanent,
      },
    );
  }

  function applyMarkerState(
    marker: L.Marker,
    location: WorkbenchSpatialLocation,
    selection: SpatialRenderSelection,
  ) {
    const element = marker.getElement();
    if (!element) return;
    const selected = isSelected(location, selection);
    element.classList.toggle("is-selected", selected);
    element.setAttribute("aria-pressed", String(selected));
    const labelTier = resolveLabelTier(
      input.mode,
      map.getZoom(),
      location,
      selected,
    );
    element.classList.remove(
      spatialLabelTierClasses.none,
      spatialLabelTierClasses.important,
      spatialLabelTierClasses.all,
    );
    element.classList.add(spatialLabelTierClasses[labelTier]);
  }

  function updateRelationDecorations() {
    for (const { line, relation } of relationLineById.values()) {
      bindRelationTooltip(
        line,
        relation,
        isRelationActive(relation, currentSelection),
      );
    }
    for (const { arrow, from, to } of relationArrows) {
      const arrowElement = arrow.getElement()?.firstElementChild;
      if (!(arrowElement instanceof HTMLElement)) continue;
      const fromPoint = map.project(from, map.getZoom());
      const toPoint = map.project(to, map.getZoom());
      const angle =
        (Math.atan2(toPoint.y - fromPoint.y, toPoint.x - fromPoint.x) * 180) /
        Math.PI;
      arrowElement.style.transform = `rotate(${angle}deg)`;
    }
  }

  const emptySelection: SpatialRenderSelection = {
    activeSpatialId: null,
    selectedEventId: null,
    selectedLocationId: null,
    selectedObjectId: null,
  };

  map.on("zoomend", () => {
    for (const [spatialId, marker] of markerById) {
      const location = locationBySpatialId.get(spatialId);
      if (location) {
        applyMarkerState(marker, location, currentSelection ?? emptySelection);
      }
    }
    updateRelationDecorations();
  });
  map.on("moveend", updateRelationDecorations);

  function render(
    view: WorkbenchSpatialView,
    selection: SpatialRenderSelection,
    nextCallbacks: SpatialRenderCallbacks,
    options: SpatialRenderOptions,
  ) {
    callbacks = nextCallbacks;
    currentSelection = selection;
    markers.clearLayers();
    relations.clearLayers();
    markerById.clear();
    locationBySpatialId.clear();
    relationLineById.clear();
    relationArrows.length = 0;

    if (input.mode !== "geographic" && options.sceneBackground?.url) {
      // CRS.Simple planar maps use a 0..100 casefile domain. The image
      // top-left is anchored at (x=0, y=100) and its bottom-right at
      // (x=100, y=0), matching the planar marker coordinate mapping.
      const sceneBounds = L.latLngBounds(
        L.latLng(100, 0),
        L.latLng(0, 100),
      );
      if (!sceneImage) {
        sceneImage = L.imageOverlay(options.sceneBackground.url, sceneBounds, {
          className: "casefile-spatial-scene-image",
          interactive: false,
          opacity: 0.92,
          pane: "tilePane",
        }).addTo(map);
      } else {
        sceneImage.setUrl(options.sceneBackground.url);
        sceneImage.setBounds(sceneBounds);
      }
    } else if (sceneImage) {
      sceneImage.remove();
      sceneImage = null;
    }

    const locationById = new Map(
      view.locations.flatMap((location) =>
        location.locationId ? [[location.locationId, location] as const] : [],
      ),
    );
    for (const relation of view.relations) {
      const from = locationById.get(relation.fromLocationId);
      const to = locationById.get(relation.toLocationId);
      if (!from || !to) continue;
      const fromCoordinate = markerCoordinate(input.mode, from);
      const toCoordinate = markerCoordinate(input.mode, to);
      if (!fromCoordinate || !toCoordinate) continue;
      const fromPoint = L.latLng(fromCoordinate);
      const toPoint = L.latLng(toCoordinate);
      const line = L.polyline([fromPoint, toPoint], {
        // Interactive lines are required for hover-revealed tooltips; click
        // events still bubble to the map and clear the current selection.
        bubblingMouseEvents: true,
        className: `casefile-spatial-relation casefile-spatial-relation--${relation.kind}`,
        color: relation.kind === "travel" ? "#a27321" : "#2f5d62",
        dashArray: relation.kind === "travel" ? "7 7" : undefined,
        interactive: true,
        opacity: 0.86,
        weight: relation.kind === "travel" ? 2 : 2.5,
      });
      line.addTo(relations);
      relationLineById.set(relation.relationId, { line, relation });
      if (relation.kind === "travel") {
        const arrow = L.marker(
          L.latLng(
            (fromPoint.lat + toPoint.lat) / 2,
            (fromPoint.lng + toPoint.lng) / 2,
          ),
          {
            bubblingMouseEvents: false,
            interactive: false,
            keyboard: false,
            icon: L.divIcon({
              className: "casefile-spatial-relation-arrow",
              html: '<span aria-hidden="true"></span>',
              iconAnchor: [7, 7],
              iconSize: [14, 14],
            }),
          },
        );
        arrow.addTo(relations);
        relationArrows.push({ arrow, from: fromPoint, to: toPoint });
      }
    }

    for (const location of view.locations) {
      const coordinate = markerCoordinate(input.mode, location);
      if (!coordinate) continue;
      const selected = isSelected(location, selection);
      const labelTier = resolveLabelTier(
        input.mode,
        map.getZoom(),
        location,
        selected,
      );
      const editable =
        Boolean(location.locationId) &&
        options.editableLocationId === location.locationId;
      const marker = L.marker(coordinate, {
        alt: markerLabel(location, editable),
        bubblingMouseEvents: false,
        draggable: editable,
        icon: L.divIcon({
          className: `casefile-spatial-marker casefile-spatial-marker--${location.source}${selected ? " is-selected" : ""}${editable ? " is-editable" : ""} ${spatialLabelTierClasses[labelTier]}`,
          html: markerHtml(location, options.layers.events, editable),
          iconAnchor: [22, 22],
          iconSize: [220, 44],
        }),
        keyboard: true,
        riseOnHover: true,
        title: location.label,
      });
      marker.on("click", () => callbacks?.onActivateLocation(location));
      marker.on("dragend", () => {
        if (!editable) return;
        callbacks?.onPreviewPosition(
          location,
          positionFromCoordinate(input.mode, marker.getLatLng()),
        );
      });
      marker.on("add", () => {
        const element = marker.getElement();
        if (!element) return;
        element.setAttribute("aria-label", markerLabel(location, editable));
        element.setAttribute("aria-pressed", String(selected));
        element.setAttribute("aria-grabbed", String(editable));
        element.setAttribute("data-source", location.source);
        element.setAttribute("data-spatial-id", location.spatialId);
        element.setAttribute("role", "button");
        element.addEventListener("keydown", (event) => {
          if (
            editable &&
            ["ArrowUp", "ArrowRight", "ArrowDown", "ArrowLeft"].includes(event.key)
          ) {
            event.preventDefault();
            event.stopPropagation();
            callbacks?.onPreviewPosition(
              location,
              nudgePosition(location.position, event.key, event.shiftKey),
            );
            return;
          }
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          callbacks?.onActivateLocation(location);
        });
      });
      marker.addTo(markers);
      markerById.set(location.spatialId, marker);
      locationBySpatialId.set(location.spatialId, location);
    }

    updateRelationDecorations();
  }

  function fitAll() {
    const points = [...markerById.values()].map((marker) => marker.getLatLng());
    if (!points.length) return;
    if (points.length === 1) {
      map.setView(points[0], input.mode === "geographic" ? 15 : 2);
      return;
    }
    map.fitBounds(L.latLngBounds(points), {
      animate: false,
      maxZoom: input.mode === "geographic" ? 15 : 3,
      padding: [64, 64],
    });
  }

  return {
    render,
    setCallbacks(nextCallbacks) {
      callbacks = nextCallbacks;
    },
    updateSelection(selection) {
      currentSelection = selection;
      for (const [spatialId, marker] of markerById) {
        const location = locationBySpatialId.get(spatialId);
        if (location) applyMarkerState(marker, location, selection);
      }
      updateRelationDecorations();
    },
    fitAll,
    focusLocation(spatialId) {
      const marker = markerById.get(spatialId);
      if (!marker) return;
      map.panTo(marker.getLatLng(), { animate: false });
    },
    zoomIn() {
      map.zoomIn();
    },
    zoomOut() {
      map.zoomOut();
    },
    getViewport() {
      const center = map.getCenter();
      return { center: [center.lat, center.lng], zoom: map.getZoom() };
    },
    setViewport(viewport) {
      map.setView(viewport.center, viewport.zoom, { animate: false });
    },
    invalidateSize() {
      map.invalidateSize({ animate: false, pan: false });
    },
    destroy() {
      map.remove();
    },
  };
}
