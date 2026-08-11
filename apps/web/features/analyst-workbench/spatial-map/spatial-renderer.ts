import * as L from "leaflet";

import type {
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
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
  onTileError: () => void;
}

export interface SpatialRenderer {
  render: (
    view: WorkbenchSpatialView,
    selection: SpatialRenderSelection,
    callbacks: SpatialRenderCallbacks,
  ) => void;
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

function markerLabel(location: WorkbenchSpatialLocation): string {
  const source =
    location.source === "wgs84"
      ? "地理坐标"
      : location.source === "schematic"
        ? "场景坐标"
        : "推算位置";
  const eventCount = location.events.length
    ? `，${location.events.length} 个事件`
    : "，没有关联事件";
  return `${location.label}，${source}${eventCount}`;
}

function markerHtml(location: WorkbenchSpatialLocation): HTMLElement {
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

  if (location.events.length) {
    const count = document.createElement("span");
    count.className = "casefile-spatial-marker__count";
    count.textContent = String(location.events.length);
    count.setAttribute("aria-hidden", "true");
    body.append(count);
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
  const markerById = new Map<string, L.Marker>();
  let callbacks: SpatialRenderCallbacks | null = null;
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

  function render(
    view: WorkbenchSpatialView,
    selection: SpatialRenderSelection,
    nextCallbacks: SpatialRenderCallbacks,
  ) {
    callbacks = nextCallbacks;
    markers.clearLayers();
    markerById.clear();
    for (const location of view.locations) {
      const coordinate = markerCoordinate(input.mode, location);
      if (!coordinate) continue;
      const selected = isSelected(location, selection);
      const marker = L.marker(coordinate, {
        alt: markerLabel(location),
        bubblingMouseEvents: false,
        icon: L.divIcon({
          className: `casefile-spatial-marker casefile-spatial-marker--${location.source}${selected ? " is-selected" : ""}`,
          html: markerHtml(location),
          iconAnchor: [22, 22],
          iconSize: [220, 44],
        }),
        keyboard: true,
        riseOnHover: true,
        title: location.label,
      });
      marker.on("click", () => callbacks?.onActivateLocation(location));
      marker.on("add", () => {
        const element = marker.getElement();
        if (!element) return;
        element.setAttribute("aria-label", markerLabel(location));
        element.setAttribute("aria-pressed", String(selected));
        element.setAttribute("data-source", location.source);
        element.setAttribute("data-spatial-id", location.spatialId);
        element.setAttribute("role", "button");
        element.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          callbacks?.onActivateLocation(location);
        });
      });
      marker.addTo(markers);
      markerById.set(location.spatialId, marker);
    }
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
