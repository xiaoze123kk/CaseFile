import * as L from "leaflet";

import { computeSpatialClusters } from "./spatial-clustering";
import type {
  SpatialLayerVisibility,
  WorkbenchSceneRegion,
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
  onSceneBackgroundError: () => void;
}

export interface SceneBackgroundConfig {
  url: string;
  /** Accessible alternative text for the floor-plan image. */
  alt?: string;
  /** Native image dimensions, used to preserve aspect ratio in the 0..100 domain. */
  imageWidth?: number | null;
  imageHeight?: number | null;
}

export interface SpatialRenderOptions {
  editableLocationId: string | null;
  layers: SpatialLayerVisibility;
  /** Deployment or case-bound floor plan shown in planar modes. */
  sceneBackground?: SceneBackgroundConfig | null;
  /** Scene polygon regions. */
  regions?: WorkbenchSceneRegion[];
}

export function resolveSceneBackgroundConfiguration(
  url: string | undefined,
  input?: {
    alt?: string;
    imageWidth?: number | null;
    imageHeight?: number | null;
  },
): SceneBackgroundConfig | null {
  const normalizedUrl = url?.trim() ?? "";
  if (!normalizedUrl) return null;
  const config: SceneBackgroundConfig = { url: normalizedUrl };
  const alt = input?.alt?.trim();
  if (alt) config.alt = alt;
  if (input?.imageWidth) config.imageWidth = input.imageWidth;
  if (input?.imageHeight) config.imageHeight = input.imageHeight;
  return config;
}

/**
 * Stable page-level contract of a spatial renderer. `SpatialMapView` only
 * depends on these methods, so a MapLibre implementation can replace this
 * Leaflet implementation without touching view state or selection logic.
 */
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
  focusRegion: (regionKey: string) => void;
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
const spatialClusterRadius = 56;

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
  source?: WorkbenchSpatialPosition,
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
        ...(source?.kind === "planar" && source.sceneId
          ? { sceneId: source.sceneId }
          : {}),
        ...(source?.kind === "planar" && source.floorId
          ? { floorId: source.floorId }
          : {}),
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
    ...(position.sceneId ? { sceneId: position.sceneId } : {}),
    ...(position.floorId ? { floorId: position.floorId } : {}),
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

function clusterMarkerHtml(count: number): string {
  return `<span class="casefile-spatial-cluster__body"><i aria-hidden="true"></i>${count}</span>`;
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
  const farThreshold = mode === "geographic" ? 8 : 1;
  if (zoom < farThreshold) {
    return locationHasSpatialContent(location) ? "important" : "none";
  }
  return "all";
}

function sceneImageBounds(config: SceneBackgroundConfig): L.LatLngBoundsExpression {
  const width = config.imageWidth;
  const height = config.imageHeight;
  if (!width || !height || width <= 0 || height <= 0) {
    return L.latLngBounds(L.latLng(100, 0), L.latLng(0, 100));
  }
  // Keep the image fully inside the normalized 0..100 domain while
  // preserving its native aspect ratio; uncovered areas fall back to the
  // grid-paper background instead of silently stretching the plan.
  const aspect = width / height;
  if (aspect >= 1) {
    const mappedHeight = 100 / aspect;
    const top = 50 + mappedHeight / 2;
    const bottom = 50 - mappedHeight / 2;
    return L.latLngBounds(L.latLng(top, 0), L.latLng(bottom, 100));
  }
  const mappedWidth = 100 * aspect;
  const left = 50 - mappedWidth / 2;
  const right = 50 + mappedWidth / 2;
  return L.latLngBounds(L.latLng(100, left), L.latLng(0, right));
}

function relationCoordinatePoints(
  mode: WorkbenchSpatialMode,
  relation: WorkbenchSpatialRelation,
): Array<[number, number]> | null {
  if (relation.kind !== "route" || !relation.routeGeometry) return null;
  if (mode === "geographic" && relation.routeGeometry.kind === "wgs84") {
    return relation.routeGeometry.points.map((point) => [
      point.latitude,
      point.longitude,
    ]);
  }
  if (mode !== "geographic" && relation.routeGeometry.kind === "planar") {
    return relation.routeGeometry.points.map((point) => [
      100 - point.y,
      point.x,
    ]);
  }
  return null;
}

function markerSignature(
  location: WorkbenchSpatialLocation,
  editable: boolean,
  showEvents: boolean,
): string {
  return JSON.stringify({
    spatialId: location.spatialId,
    label: location.label,
    position: location.position,
    eventIds: location.events.map((event) => event.eventId),
    editable,
    showEvents,
  });
}

function relationSignature(relation: WorkbenchSpatialRelation): string {
  return JSON.stringify({
    relationId: relation.relationId,
    kind: relation.kind,
    fromLocationId: relation.fromLocationId,
    toLocationId: relation.toLocationId,
    label: relation.label,
    routeGeometry: relation.routeGeometry,
  });
}

function regionSignature(region: WorkbenchSceneRegion): string {
  return JSON.stringify({
    key: `${region.sceneId}:${region.regionId}`,
    name: region.name,
    geometry: region.geometry,
  });
}

export function createSpatialRenderer(input: {
  container: HTMLElement;
  mode: WorkbenchSpatialMode;
  tileConfiguration: MapTileConfiguration;
}): SpatialRenderer {
  const prefersReducedMotion =
    typeof window !== "undefined" &&
    Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  const map = L.map(input.container, {
    attributionControl: input.mode === "geographic",
    crs: input.mode === "geographic" ? L.CRS.EPSG3857 : L.CRS.Simple,
    fadeAnimation: !prefersReducedMotion,
    keyboard: true,
    markerZoomAnimation: !prefersReducedMotion,
    minZoom: input.mode === "geographic" ? 2 : -2,
    maxZoom: input.mode === "geographic" ? 19 : 5,
    preferCanvas: false,
    scrollWheelZoom: true,
    touchZoom: true,
    worldCopyJump: input.mode === "geographic",
    zoomAnimation: !prefersReducedMotion,
    zoomControl: false,
  });
  const markers = L.layerGroup().addTo(map);
  const clusterGroup = L.layerGroup().addTo(map);
  const regionGroup = L.layerGroup().addTo(map);
  const relations = L.layerGroup().addTo(map);
  const markerById = new Map<string, L.Marker>();
  const markerSignatures = new Map<string, string>();
  const locationBySpatialId = new Map<string, WorkbenchSpatialLocation>();
  const relationLineById = new Map<
    string,
    {
      line: L.Polyline;
      relation: WorkbenchSpatialRelation;
      start: L.LatLng;
      end: L.LatLng;
    }
  >();
  const relationArrowById = new Map<
    string,
    { arrow: L.Marker; from: L.LatLng; to: L.LatLng }
  >();
  const relationSignatures = new Map<string, string>();
  const regionPolygonById = new Map<string, { polygon: L.Polygon; region: WorkbenchSceneRegion }>();
  const regionSignatures = new Map<string, string>();
  const clusterMarkers: L.Marker[] = [];
  const renderedMarkerIds = new Set<string>();
  let callbacks: SpatialRenderCallbacks | null = null;
  let currentSelection: SpatialRenderSelection | null = null;
  let currentEditableLocationId: string | null = null;
  let currentShowEvents = true;
  let sceneImage: L.ImageOverlay | null = null;
  let sceneImageSignature: string | null = null;
  let sceneErrorUrl: string | null = null;
  let tileErrorReported = false;
  let decoratedRelationIdSignature = "";

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
      target.closest(".casefile-spatial-marker, .casefile-spatial-cluster")
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

  function relationTooltipLabel(relation: WorkbenchSpatialRelation): string {
    if (relation.kind === "adjacency") return relation.label;
    return `→ ${relation.label}${relation.kind === "route" ? "（卷宗 geometry）" : ""}`;
  }

  function bindRelationTooltip(
    line: L.Polyline,
    relation: WorkbenchSpatialRelation,
    permanent: boolean,
  ) {
    line.unbindTooltip();
    line.bindTooltip(relationTooltipLabel(relation), {
      className: `casefile-spatial-relation-label casefile-spatial-relation-label--${relation.kind}`,
      direction: "center",
      interactive: false,
      permanent,
    });
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

  function updateRelationTooltips() {
    const signature = [...relationLineById.values()]
      .map(({ relation }) => {
        const active = isRelationActive(relation, currentSelection);
        return `${relation.relationId}:${active ? "1" : "0"}`;
      })
      .sort()
      .join("|");
    if (signature === decoratedRelationIdSignature) return;
    decoratedRelationIdSignature = signature;
    for (const { line, relation } of relationLineById.values()) {
      bindRelationTooltip(
        line,
        relation,
        isRelationActive(relation, currentSelection),
      );
    }
  }

  function updateRelationArrowAngles() {
    for (const { arrow, from, to } of relationArrowById.values()) {
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

  function updateRelationDecorations() {
    updateRelationTooltips();
    updateRelationArrowAngles();
  }

  function refreshClusters() {
    for (const clusterMarker of clusterMarkers) clusterMarker.remove();
    clusterMarkers.length = 0;

    const points = new Map<string, { x: number; y: number }>();
    const excludedKeys = new Set<string>();
    for (const [spatialId, marker] of markerById) {
      if (!renderedMarkerIds.has(spatialId)) continue;
      const location = locationBySpatialId.get(spatialId);
      const selected = location
        ? isSelected(location, currentSelection ?? emptySelection)
        : false;
      const editable = Boolean(
        location?.locationId &&
          currentEditableLocationId === location.locationId,
      );
      if (selected || editable) {
        excludedKeys.add(spatialId);
        continue;
      }
      const point = map.latLngToContainerPoint(marker.getLatLng());
      points.set(spatialId, { x: point.x, y: point.y });
    }

    const clusters = computeSpatialClusters(points, {
      radius: spatialClusterRadius,
      excludedKeys,
    });
    const clusteredIds = new Set(clusters.flatMap((cluster) => cluster.keys));

    for (const [spatialId, marker] of markerById) {
      if (!renderedMarkerIds.has(spatialId)) continue;
      if (clusteredIds.has(spatialId)) {
        if (marker.getElement()) marker.remove();
      } else if (!marker.getElement()) {
        marker.addTo(markers);
      }
    }

    for (const cluster of clusters) {
      const coordinate = map.containerPointToLatLng([cluster.x, cluster.y]);
      const count = cluster.keys.length;
      const clusterMarker = L.marker(coordinate, {
        alt: `${count} 个地点`,
        bubblingMouseEvents: false,
        interactive: true,
        keyboard: true,
        riseOnHover: true,
        title: `${count} 个地点`,
        icon: L.divIcon({
          className: "casefile-spatial-cluster",
          html: clusterMarkerHtml(count),
          iconAnchor: [22, 22],
          iconSize: [44, 44],
        }),
      });
      clusterMarker.on("click", () => {
        map.zoomIn();
      });
      clusterMarker.on("add", () => {
        const element = clusterMarker.getElement();
        if (!element) return;
        element.setAttribute("aria-label", `${count} 个地点，点击放大一级`);
        element.setAttribute("data-cluster-count", String(count));
        element.setAttribute("role", "button");
        element.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          map.zoomIn();
        });
      });
      clusterMarker.addTo(clusterGroup);
      clusterMarkers.push(clusterMarker);
    }
  }

  function syncSceneBackground(config: SceneBackgroundConfig | null | undefined) {
    if (input.mode === "geographic" || !config?.url) {
      if (sceneImage) {
        sceneImage.remove();
        sceneImage = null;
        sceneImageSignature = null;
        sceneErrorUrl = null;
      }
      return;
    }
    const signature = JSON.stringify({
      url: config.url,
      alt: config.alt ?? "",
      imageWidth: config.imageWidth ?? null,
      imageHeight: config.imageHeight ?? null,
    });
    if (sceneImage && sceneImageSignature === signature) return;

    if (sceneImage) sceneImage.remove();
    const image = L.imageOverlay(config.url, sceneImageBounds(config), {
      alt: config.alt ?? "",
      className: "casefile-spatial-scene-image",
      interactive: false,
      opacity: 0.92,
      pane: "tilePane",
    });
    image.on("error", () => {
      if (sceneErrorUrl === config.url) return;
      sceneErrorUrl = config.url;
      callbacks?.onSceneBackgroundError();
    });
    image.addTo(map);
    sceneImage = image;
    sceneImageSignature = signature;
  }

  function syncRegions(regionsInput: WorkbenchSceneRegion[]) {
    const desiredIds = new Set<string>();
    for (const region of regionsInput) {
      const regionId = `${region.sceneId}:${region.regionId}`;
      desiredIds.add(regionId);
      const signature = regionSignature(region);
      if (regionSignatures.get(regionId) === signature) continue;

      const previous = regionPolygonById.get(regionId);
      if (previous) previous.polygon.remove();

      const coordinates = region.geometry.map(
        (point): L.LatLngExpression => [100 - point.y, point.x],
      );
      const polygon = L.polygon(coordinates, {
        bubblingMouseEvents: false,
        className: "casefile-spatial-region",
        color: "#2f5d62",
        fillColor: "#2f5d62",
        fillOpacity: 0.14,
        interactive: true,
        opacity: 0.8,
        weight: 1.5,
      });
      polygon.bindTooltip(region.name, {
        className: "casefile-spatial-region-label",
        direction: "center",
        opacity: 0.96,
      });
      polygon.on("click", (event) => {
        if (event.originalEvent instanceof Event) {
          L.DomEvent.stopPropagation(event.originalEvent);
        }
      });
      polygon.addTo(regionGroup);
      regionPolygonById.set(regionId, { polygon, region });
      regionSignatures.set(regionId, signature);
    }
    for (const [regionId, { polygon }] of regionPolygonById) {
      if (desiredIds.has(regionId)) continue;
      polygon.remove();
      regionPolygonById.delete(regionId);
      regionSignatures.delete(regionId);
    }
  }

  function createRelationLine(
    relation: WorkbenchSpatialRelation,
    from: WorkbenchSpatialLocation,
    to: WorkbenchSpatialLocation,
  ) {
    const fromCoordinate = markerCoordinate(input.mode, from);
    const toCoordinate = markerCoordinate(input.mode, to);
    if (!fromCoordinate || !toCoordinate) return null;
    const geometryPoints = relationCoordinatePoints(input.mode, relation);
    const coordinates: L.LatLngExpression[] = geometryPoints ?? [
      fromCoordinate,
      toCoordinate,
    ];
    const line = L.polyline(coordinates, {
      bubblingMouseEvents: true,
      className: `casefile-spatial-relation casefile-spatial-relation--${relation.kind}`,
      color:
        relation.kind === "travel"
          ? "#a27321"
          : relation.kind === "route"
            ? "#1f5c3d"
            : "#2f5d62",
      dashArray: relation.kind === "travel" ? "7 7" : undefined,
      interactive: true,
      opacity: relation.kind === "route" ? 0.95 : 0.86,
      weight: relation.kind === "route" ? 3 : relation.kind === "travel" ? 2 : 2.5,
    });
    line.addTo(relations);
    const start = L.latLng(coordinates[0]);
    const end = L.latLng(coordinates[coordinates.length - 1]);
    relationLineById.set(relation.relationId, { line, relation, start, end });
    if (relation.kind === "travel" || relation.kind === "route") {
      const midpoint = L.latLng(
        (start.lat + end.lat) / 2,
        (start.lng + end.lng) / 2,
      );
      const arrow = L.marker(midpoint, {
        bubblingMouseEvents: false,
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: `casefile-spatial-relation-arrow casefile-spatial-relation-arrow--${relation.kind}`,
          html: '<span aria-hidden="true"></span>',
          iconAnchor: [7, 7],
          iconSize: [14, 14],
        }),
      });
      arrow.addTo(relations);
      relationArrowById.set(relation.relationId, { arrow, from: start, to: end });
    }
    return line;
  }

  function syncRelations(relationsInput: WorkbenchSpatialRelation[]) {
    const locationById = new Map(
      [...locationBySpatialId.values()].flatMap((location) =>
        location.locationId ? [[location.locationId, location] as const] : [],
      ),
    );
    const desiredIds = new Set<string>();
    for (const relation of relationsInput) {
      desiredIds.add(relation.relationId);
      const signature = relationSignature(relation);
      if (relationSignatures.get(relation.relationId) === signature) continue;
      const previousLine = relationLineById.get(relation.relationId);
      if (previousLine) previousLine.line.remove();
      const previousArrow = relationArrowById.get(relation.relationId);
      if (previousArrow) previousArrow.arrow.remove();
      const from = locationById.get(relation.fromLocationId);
      const to = locationById.get(relation.toLocationId);
      if (from && to) {
        createRelationLine(relation, from, to);
        relationSignatures.set(relation.relationId, signature);
      }
    }
    for (const [relationId, { line }] of relationLineById) {
      if (desiredIds.has(relationId)) continue;
      line.remove();
      relationLineById.delete(relationId);
      relationSignatures.delete(relationId);
    }
    for (const [relationId, { arrow }] of relationArrowById) {
      if (desiredIds.has(relationId)) continue;
      arrow.remove();
      relationArrowById.delete(relationId);
    }
  }

  function createLocationMarker(
    location: WorkbenchSpatialLocation,
    selection: SpatialRenderSelection,
    editable: boolean,
  ) {
    const coordinate = markerCoordinate(input.mode, location);
    if (!coordinate) return null;
    const selected = isSelected(location, selection);
    const labelTier = resolveLabelTier(
      input.mode,
      map.getZoom(),
      location,
      selected,
    );
    const marker = L.marker(coordinate, {
      alt: markerLabel(location, editable),
      bubblingMouseEvents: false,
      draggable: editable,
      icon: L.divIcon({
        className: `casefile-spatial-marker casefile-spatial-marker--${location.source}${selected ? " is-selected" : ""}${editable ? " is-editable" : ""} ${spatialLabelTierClasses[labelTier]}`,
        html: markerHtml(location, currentShowEvents, editable),
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
        positionFromCoordinate(input.mode, marker.getLatLng(), location.position),
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
    return marker;
  }

  function syncLocations(
    locationsInput: WorkbenchSpatialLocation[],
    selection: SpatialRenderSelection,
    options: SpatialRenderOptions,
  ) {
    currentEditableLocationId = options.editableLocationId;
    currentShowEvents = options.layers.events;
    const desiredIds = new Set<string>();
    for (const location of locationsInput) {
      desiredIds.add(location.spatialId);
      const editable =
        Boolean(location.locationId) &&
        options.editableLocationId === location.locationId;
      const signature = markerSignature(
        location,
        editable,
        options.layers.events,
      );
      if (markerSignatures.get(location.spatialId) === signature) {
        renderedMarkerIds.add(location.spatialId);
        continue;
      }
      const existing = markerById.get(location.spatialId);
      if (existing) existing.remove();
      const marker = createLocationMarker(location, selection, editable);
      if (!marker) continue;
      marker.addTo(markers);
      markerById.set(location.spatialId, marker);
      markerSignatures.set(location.spatialId, signature);
      locationBySpatialId.set(location.spatialId, location);
      renderedMarkerIds.add(location.spatialId);
    }
    for (const [spatialId, marker] of markerById) {
      if (desiredIds.has(spatialId)) continue;
      marker.remove();
      markerById.delete(spatialId);
      markerSignatures.delete(spatialId);
      locationBySpatialId.delete(spatialId);
      renderedMarkerIds.delete(spatialId);
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
    updateRelationArrowAngles();
    refreshClusters();
  });

  function render(
    view: WorkbenchSpatialView,
    selection: SpatialRenderSelection,
    nextCallbacks: SpatialRenderCallbacks,
    options: SpatialRenderOptions,
  ) {
    callbacks = nextCallbacks;
    currentSelection = selection;
    syncSceneBackground(options.sceneBackground);
    syncRegions(options.regions ?? view.regions ?? []);
    syncLocations(view.locations, selection, options);
    syncRelations(view.relations);
    decoratedRelationIdSignature = "";
    updateRelationDecorations();
    refreshClusters();
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
      updateRelationTooltips();
      refreshClusters();
    },
    fitAll,
    focusLocation(spatialId) {
      const marker = markerById.get(spatialId);
      if (!marker) return;
      refreshClusters();
      map.panTo(marker.getLatLng(), { animate: !prefersReducedMotion });
    },
    focusRegion(regionKey) {
      const entry = regionPolygonById.get(regionKey);
      if (!entry) return;
      map.fitBounds(entry.polygon.getBounds(), {
        animate: false,
        padding: [64, 64],
      });
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
