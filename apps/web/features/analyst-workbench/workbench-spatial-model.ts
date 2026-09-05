import type { ObjectRef } from "@casefile/contracts";
import { buildSpatialInvestigation } from "./spatial-map/spatial-investigation-model";

import type { CaseFileDocument } from "@/lib/api-client";

import type { WorkbenchSeed } from "./analyst-fixture";
import { creatorLabel } from "./workbench-presenters";
import type {
  WorkbenchMapModel,
  SpatialLayerVisibility,
  WorkbenchRouteGeometry,
  WorkbenchSceneFloor,
  WorkbenchSceneRegion,
  WorkbenchSpatialEvent,
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
  WorkbenchSpatialPosition,
  WorkbenchSpatialRelation,
  WorkbenchSpatialScene,
  WorkbenchSpatialView,
  WorkbenchTimelineEvent,
  WorkbenchUnlocatedReason,
} from "./workbench-real-data-types";

type ContractLocation = CaseFileDocument["locations"][number];

type ContractTravelTime = ContractLocation["travel_times"][number];

type ContractScene = NonNullable<CaseFileDocument["spatial_scenes"]>[number];

type ContractSpatialPosition =
  | {
      kind: "schematic";
      x: number;
      y: number;
      sceneId: string | null;
      floorId: string | null;
    }
  | { kind: "wgs84"; latitude: number; longitude: number };

interface PlanarPosition {
  x: number;
  y: number;
  inferred: boolean;
}

const spatialModeOrder: WorkbenchSpatialMode[] = [
  "geographic",
  "scene",
  "topology",
];

interface SpatialModelCacheEntry {
  key: string;
  map: WorkbenchMapModel;
}

const spatialModelCache = new WeakMap<CaseFileDocument, SpatialModelCacheEntry>();

function spatialModelInputKey(
  caseFile: CaseFileDocument,
  timelineEvents: WorkbenchTimelineEvent[],
): string {
  return JSON.stringify({
    locations: caseFile.locations.map((location) => ({
      id: location.id,
      name: location.name,
      description: location.description,
      parent_ref: location.parent_ref,
      adjacency_refs: location.adjacency_refs,
      travel_times: location.travel_times,
      spatial_position: location.spatial_position,
    })),
    spatial_scenes: caseFile.spatial_scenes ?? [],
    entities: caseFile.entities,
    eventRecords: timelineEvents,
    events: timelineEvents.map((event) => ({
      id: event.id,
      label: event.label,
      time: event.time,
      locationId: event.refs.locationId,
      relatedObjectIds: event.relatedObjectIds,
    })),
  });
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readReferenceId(reference: ObjectRef | null | undefined): string | null {
  return reference && typeof reference.object_id === "string"
    ? reference.object_id
    : null;
}

function unlocatedReason(
  location: ContractLocation,
  locationIds: Set<string>,
  brokenSceneReferenceIds: Set<string>,
): WorkbenchUnlocatedReason {
  if (brokenSceneReferenceIds.has(location.id)) return "dangling_scene_reference";
  const references = [
    location.parent_ref,
    ...location.adjacency_refs,
    ...location.travel_times.map((travelTime) => travelTime.to_ref),
  ];
  const hasDanglingReference = references.some((reference) => {
    const referenceId = readReferenceId(reference);
    return (
      referenceId !== null &&
      referenceId !== location.id &&
      !locationIds.has(referenceId)
    );
  });
  return hasDanglingReference ? "dangling_topology" : "no_coordinates";
}

function readSceneReference(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function readSpatialPosition(
  location: ContractLocation,
): ContractSpatialPosition | null {
  const position = location.spatial_position;
  if (!position) return null;
  if (position.coordinate_system === "schematic") {
    const x = finiteNumber(position.x);
    const y = finiteNumber(position.y);
    return x !== null && y !== null && x >= 0 && x <= 100 && y >= 0 && y <= 100
      ? {
          kind: "schematic",
          x,
          y,
          sceneId: readSceneReference(position.scene_id),
          floorId: readSceneReference(position.floor_id),
        }
      : null;
  }
  const latitude = finiteNumber(position.latitude);
  const longitude = finiteNumber(position.longitude);
  return latitude !== null &&
    longitude !== null &&
    latitude >= -90 &&
    latitude <= 90 &&
    longitude >= -180 &&
    longitude <= 180
    ? { kind: "wgs84", latitude, longitude }
    : null;
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function positiveFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function nonEmptyUrl(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readRouteGeometry(travelTime: ContractTravelTime): WorkbenchRouteGeometry | null {
  const geometry = travelTime.route_geometry;
  if (!geometry) return null;
  if (geometry.coordinate_system === "wgs84") {
    const points = geometry.points
      .map((point) => ({
        latitude: finiteNumber(point.latitude),
        longitude: finiteNumber(point.longitude),
      }))
      .filter(
        (point): point is { latitude: number; longitude: number } =>
          point.latitude !== null && point.longitude !== null,
      )
      .filter(
        (point) =>
          point.latitude >= -90 &&
          point.latitude <= 90 &&
          point.longitude >= -180 &&
          point.longitude <= 180,
      );
    return points.length >= 2 ? { kind: "wgs84", points } : null;
  }
  const points = geometry.points
    .map((point) => ({ x: finiteNumber(point.x), y: finiteNumber(point.y) }))
    .filter(
      (point): point is { x: number; y: number } =>
        point.x !== null && point.y !== null,
    )
    .filter(
      (point) => point.x >= 0 && point.x <= 100 && point.y >= 0 && point.y <= 100,
    );
  return points.length >= 2 ? { kind: "planar", points } : null;
}

function readSceneFloor(
  floor: NonNullable<ContractScene["floors"]>[number],
): WorkbenchSceneFloor {
  return {
    floorId: floor.floor_id,
    label: floor.label,
    backgroundImageUrl: nonEmptyUrl(floor.background_image_url),
    imageWidth: positiveFiniteNumber(floor.image_width),
    imageHeight: positiveFiniteNumber(floor.image_height),
  };
}

function readSceneRegion(
  sceneId: string,
  region: NonNullable<ContractScene["regions"]>[number],
): WorkbenchSceneRegion | null {
  const geometry = region.geometry
    .map((point) => ({ x: finiteNumber(point.x), y: finiteNumber(point.y) }))
    .filter(
      (point): point is { x: number; y: number } =>
        point.x !== null && point.y !== null,
    )
    .filter(
      (point) => point.x >= 0 && point.x <= 100 && point.y >= 0 && point.y <= 100,
    );
  return geometry.length >= 3
    ? { regionId: region.region_id, sceneId, name: region.name, geometry }
    : null;
}

function readSpatialScenes(
  caseFile: CaseFileDocument,
): WorkbenchSpatialScene[] {
  return (caseFile.spatial_scenes ?? []).map((scene) => ({
    sceneId: scene.scene_id,
    name: scene.name,
    backgroundImageUrl: nonEmptyUrl(scene.background_image_url),
    imageWidth: positiveFiniteNumber(scene.image_width),
    imageHeight: positiveFiniteNumber(scene.image_height),
    floors: (scene.floors ?? []).map(readSceneFloor),
    regions: (scene.regions ?? []).flatMap((region) => {
      const parsed = readSceneRegion(scene.scene_id, region);
      return parsed ? [parsed] : [];
    }),
  }));
}

function initialGrid(ids: string[]): Map<string, { x: number; y: number }> {
  const sorted = [...ids].sort();
  const columns = Math.max(1, Math.ceil(Math.sqrt(sorted.length || 1)));
  const rows = Math.max(1, Math.ceil(sorted.length / columns));
  return new Map(
    sorted.map((id, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      return [
        id,
        {
          x: columns === 1 ? 50 : 12 + (column / (columns - 1)) * 76,
          y: rows === 1 ? 50 : 12 + (row / (rows - 1)) * 76,
        },
      ];
    }),
  );
}

function buildNeighborWeights(
  locations: ContractLocation[],
): Map<string, Map<string, number>> {
  const candidateIds = new Set(locations.map((location) => location.id));
  const neighborWeights = new Map<string, Map<string, number>>();
  const connect = (left: string, right: string | null, weight: number) => {
    if (!right || left === right || !candidateIds.has(left) || !candidateIds.has(right)) {
      return;
    }
    const add = (from: string, to: string) => {
      const neighbors = neighborWeights.get(from) ?? new Map<string, number>();
      neighbors.set(to, Math.max(neighbors.get(to) ?? 0, weight));
      neighborWeights.set(from, neighbors);
    };
    add(left, right);
    add(right, left);
  };

  for (const location of locations) {
    connect(location.id, readReferenceId(location.parent_ref), 3);
    for (const reference of location.adjacency_refs) {
      connect(location.id, readReferenceId(reference), 2);
    }
    for (const travelTime of location.travel_times) {
      connect(
        location.id,
        readReferenceId(travelTime.to_ref),
        clamp(120 / Math.max(1, travelTime.minutes), 0.25, 4),
      );
    }
  }
  return neighborWeights;
}

function buildTopologyPositions(
  locations: ContractLocation[],
  explicitById: Map<string, { x: number; y: number }>,
  inferredIds: string[],
  neighborWeights: Map<string, Map<string, number>>,
): Map<string, PlanarPosition> {
  const ids = locations.map((location) => location.id);
  const initial = initialGrid(ids);
  const positions = new Map<string, PlanarPosition>();
  for (const id of ids) {
    const explicit = explicitById.get(id);
    positions.set(
      id,
      explicit
        ? { ...explicit, inferred: false }
        : { ...(initial.get(id) ?? { x: 50, y: 50 }), inferred: true },
    );
  }

  for (let iteration = 0; iteration < 24; iteration += 1) {
    const next = new Map(positions);
    let largestShift = 0;
    for (const id of [...inferredIds].sort()) {
      const neighbors = neighborWeights.get(id);
      if (!neighbors?.size) continue;
      let totalWeight = 0;
      let weightedX = 0;
      let weightedY = 0;
      for (const [neighborId, weight] of [...neighbors.entries()].sort()) {
        const neighbor = positions.get(neighborId);
        if (!neighbor) continue;
        totalWeight += weight;
        weightedX += neighbor.x * weight;
        weightedY += neighbor.y * weight;
      }
      if (!totalWeight) continue;
      const anchor = initial.get(id) ?? { x: 50, y: 50 };
      const angle = ((hashString(id) % 360) * Math.PI) / 180;
      const targetX = weightedX / totalWeight + Math.cos(angle) * 4;
      const targetY = weightedY / totalWeight + Math.sin(angle) * 4;
      const current = positions.get(id);
      if (current) {
        largestShift = Math.max(
          largestShift,
          Math.abs(targetX * 0.76 + anchor.x * 0.24 - current.x),
          Math.abs(targetY * 0.76 + anchor.y * 0.24 - current.y),
        );
      }
      next.set(id, {
        x: clamp(targetX * 0.76 + anchor.x * 0.24, 3, 97),
        y: clamp(targetY * 0.76 + anchor.y * 0.24, 3, 97),
        inferred: true,
      });
    }
    for (const [id, position] of next) positions.set(id, position);
    if (largestShift < 0.01) break;
  }
  return positions;
}

function eventsByLocation(
  timelineEvents: WorkbenchTimelineEvent[],
): Map<string, WorkbenchSpatialEvent[]> {
  const result = new Map<string, WorkbenchSpatialEvent[]>();
  for (const event of timelineEvents) {
    const locationId = event.refs.locationId;
    if (!locationId) continue;
    const events = result.get(locationId) ?? [];
    events.push({
      eventId: event.id,
      label: event.label,
      time: event.time,
      relatedObjectIds: event.relatedObjectIds,
    });
    result.set(locationId, events);
  }
  return result;
}

function locationRelatedObjectIds(
  locationId: string,
  events: WorkbenchSpatialEvent[],
): string[] {
  const eventIds = new Set(events.map((event) => event.eventId));
  return [
    ...new Set(
      events.flatMap((event) => event.relatedObjectIds).filter(
        (id) => id !== locationId && !eventIds.has(id),
      ),
    ),
  ].sort();
}

function makeSpatialLocation(input: {
  location: ContractLocation;
  locationIndex: number;
  source: WorkbenchSpatialLocation["source"];
  position: WorkbenchSpatialPosition;
  events: WorkbenchSpatialEvent[];
}): WorkbenchSpatialLocation {
  return {
    spatialId: input.location.id,
    locationId: input.location.id,
    label: creatorLabel(input.location.name, {
      kind: "location",
      index: input.locationIndex,
      description: input.location.description,
    }),
    source: input.source,
    position: input.position,
    events: input.events,
    relatedObjectIds: locationRelatedObjectIds(input.location.id, input.events),
  };
}

function emptyView(mode: WorkbenchSpatialMode) {
  return {
    mode,
    locations: [],
    relations: [],
  } satisfies WorkbenchMapModel["views"][WorkbenchSpatialMode];
}

function buildSpatialRelations(
  locations: ContractLocation[],
): WorkbenchSpatialRelation[] {
  const locationIds = new Set(locations.map((location) => location.id));
  const relations = new Map<string, WorkbenchSpatialRelation>();

  for (const location of [...locations].sort((left, right) =>
    left.id.localeCompare(right.id),
  )) {
    for (const reference of location.adjacency_refs) {
      const adjacentId = readReferenceId(reference);
      if (!adjacentId || adjacentId === location.id || !locationIds.has(adjacentId)) {
        continue;
      }
      const [fromLocationId, toLocationId] = [location.id, adjacentId].sort();
      const relationId = `adjacency:${fromLocationId}:${toLocationId}`;
      relations.set(relationId, {
        relationId,
        kind: "adjacency",
        fromLocationId,
        toLocationId,
        direction: "undirected",
        label: "相邻",
        minutes: null,
        routeGeometry: null,
      });
    }
    for (const travelTime of location.travel_times) {
      const targetId = readReferenceId(travelTime.to_ref);
      if (!targetId || targetId === location.id || !locationIds.has(targetId)) {
        continue;
      }
      const relationId = `travel:${location.id}:${targetId}:${travelTime.minutes}`;
      relations.set(relationId, {
        relationId,
        kind: "travel",
        fromLocationId: location.id,
        toLocationId: targetId,
        direction: "directed",
        label: `${travelTime.minutes} 分钟`,
        minutes: travelTime.minutes,
        routeGeometry: readRouteGeometry(travelTime),
      });
    }
  }
  return [...relations.values()].sort((left, right) =>
    left.relationId.localeCompare(right.relationId),
  );
}

function routeGeometryMatchesMode(
  geometry: WorkbenchRouteGeometry,
  mode: WorkbenchSpatialMode,
): boolean {
  return mode === "geographic"
    ? geometry.kind === "wgs84"
    : geometry.kind === "planar";
}

function relationsForLocations(
  relations: WorkbenchSpatialRelation[],
  locations: WorkbenchSpatialLocation[],
  mode: WorkbenchSpatialMode,
): WorkbenchSpatialRelation[] {
  const visibleIds = new Set(
    locations.flatMap((location) =>
      location.locationId ? [location.locationId] : [],
    ),
  );
  return relations
    .filter(
      (relation) =>
        visibleIds.has(relation.fromLocationId) &&
        visibleIds.has(relation.toLocationId),
    )
    .map((relation) =>
      relation.kind === "travel" &&
      relation.routeGeometry &&
      routeGeometryMatchesMode(relation.routeGeometry, mode)
        ? { ...relation, kind: "route" as const }
        : relation,
    );
}

export const defaultSpatialLayerVisibility: SpatialLayerVisibility = {
  locations: true,
  events: true,
  relations: true,
  regions: false,
  unconfirmed: false,
};

export function filterWorkbenchSpatialView(
  view: WorkbenchSpatialView,
  layers: SpatialLayerVisibility,
): WorkbenchSpatialView {
  const locations = view.locations.filter((location) =>
    location.source === "inferred" ? layers.unconfirmed : layers.locations,
  );
  return {
    ...view,
    locations,
    relations: layers.relations
      ? relationsForLocations(view.relations, locations, view.mode)
      : [],
    regions: layers.regions ? view.regions : [],
  };
}

export function buildWorkbenchSpatialModel(
  caseFile: CaseFileDocument,
  timelineEvents: WorkbenchTimelineEvent[],
): WorkbenchMapModel {
  const key = spatialModelInputKey(caseFile, timelineEvents);
  const cached = spatialModelCache.get(caseFile);
  if (cached?.key === key) return cached.map;
  const map = buildWorkbenchSpatialModelUncached(caseFile, timelineEvents);
  spatialModelCache.set(caseFile, { key, map });
  return map;
}

function buildWorkbenchSpatialModelUncached(
  caseFile: CaseFileDocument,
  timelineEvents: WorkbenchTimelineEvent[],
): WorkbenchMapModel {
  const locationIndexById = new Map(
    caseFile.locations.map((location, index) => [location.id, index]),
  );
  const positions = new Map(
    caseFile.locations.map((location) => [location.id, readSpatialPosition(location)]),
  );
  const locationIds = new Set(locationIndexById.keys());
  const spatialRelations = buildSpatialRelations(caseFile.locations);
  const eventMap = eventsByLocation(timelineEvents);
  const scenes = readSpatialScenes(caseFile);
  const sceneIds = new Set(scenes.map((scene) => scene.sceneId));
  const floorIdsByScene = new Map(
    scenes.map((scene) => [
      scene.sceneId,
      new Set(scene.floors.map((floor) => floor.floorId)),
    ]),
  );
  const brokenSceneReferenceIds = new Set<string>();
  for (const [locationId, position] of positions) {
    if (position?.kind !== "schematic") continue;
    const hasBrokenScene =
      Boolean(position.sceneId && !sceneIds.has(position.sceneId)) ||
      Boolean(
        position.floorId &&
          (!position.sceneId ||
            !floorIdsByScene.get(position.sceneId)?.has(position.floorId)),
      );
    if (hasBrokenScene) brokenSceneReferenceIds.add(locationId);
  }

  const geographicLocations = caseFile.locations
    .flatMap((location, locationIndex) => {
      const position = positions.get(location.id);
      return position?.kind === "wgs84"
        ? [
            makeSpatialLocation({
              location,
              locationIndex,
              source: "wgs84",
              position: {
                kind: "wgs84",
                latitude: position.latitude,
                longitude: position.longitude,
              },
              events: eventMap.get(location.id) ?? [],
            }),
          ]
        : [];
    })
    .sort((left, right) => left.spatialId.localeCompare(right.spatialId));

  const planarLocations = caseFile.locations.filter(
    (location) => positions.get(location.id)?.kind !== "wgs84",
  );
  const explicitById = new Map(
    planarLocations.flatMap((location) => {
      const position = positions.get(location.id);
      return position?.kind === "schematic"
        ? [[location.id, { x: position.x, y: position.y }] as const]
        : [];
    }),
  );
  const neighborWeights = buildNeighborWeights(planarLocations);
  const inferredIds = planarLocations
    .filter(
      (location) =>
        !explicitById.has(location.id) &&
        Boolean(neighborWeights.get(location.id)?.size),
    )
    .map((location) => location.id)
    .sort();
  const inferredIdSet = new Set(inferredIds);
  const unlocatedLocationIds = [
    ...new Set(
      [
        ...planarLocations
          .filter(
            (location) =>
              !explicitById.has(location.id) && !inferredIdSet.has(location.id),
          )
          .map((location) => location.id),
        ...brokenSceneReferenceIds,
      ].sort(),
    ),
  ];

  const sceneLocations = planarLocations
    .flatMap((location) => {
      if (brokenSceneReferenceIds.has(location.id)) return [];
      const position = positions.get(location.id);
      if (position?.kind !== "schematic") return [];
      return [
        makeSpatialLocation({
          location,
          locationIndex: locationIndexById.get(location.id) ?? 0,
          source: "schematic",
          position: {
            kind: "planar",
            x: position.x,
            y: position.y,
            ...(position.sceneId ? { sceneId: position.sceneId } : {}),
            ...(position.floorId ? { floorId: position.floorId } : {}),
          },
          events: eventMap.get(location.id) ?? [],
        }),
      ];
    })
    .sort((left, right) => left.spatialId.localeCompare(right.spatialId));

  const topologyCandidates = planarLocations.filter(
    (location) => explicitById.has(location.id) || inferredIdSet.has(location.id),
  );
  const topologyPositions = buildTopologyPositions(
    topologyCandidates,
    explicitById,
    inferredIds,
    neighborWeights,
  );
  const topologyLocations = topologyCandidates
    .flatMap((location) => {
      const position = topologyPositions.get(location.id);
      if (!position) return [];
      return [
        makeSpatialLocation({
          location,
          locationIndex: locationIndexById.get(location.id) ?? 0,
          source: position.inferred ? "inferred" : "schematic",
          position: { kind: "planar", x: position.x, y: position.y },
          events: eventMap.get(location.id) ?? [],
        }),
      ];
    })
    .sort((left, right) => left.spatialId.localeCompare(right.spatialId));

  const allRegions = scenes.flatMap((scene) => scene.regions);
  const views: WorkbenchMapModel["views"] = {
    geographic: {
      mode: "geographic",
      locations: geographicLocations,
      relations: relationsForLocations(
        spatialRelations,
        geographicLocations,
        "geographic",
      ),
    },
    scene: {
      mode: "scene",
      locations: sceneLocations,
      relations: relationsForLocations(spatialRelations, sceneLocations, "scene"),
      regions: allRegions,
    },
    topology: {
      mode: "topology",
      locations: inferredIds.length ? topologyLocations : [],
      relations: inferredIds.length
        ? relationsForLocations(spatialRelations, topologyLocations, "topology")
        : [],
    },
  };
  const availableModes = spatialModeOrder.filter(
    (mode) => views[mode].locations.length > 0,
  );
  const locatedEventCount = [...eventMap.values()].reduce(
    (count, events) => count + events.length,
    0,
  );
  const unlocatedLocationIdSet = new Set(unlocatedLocationIds);
  return {
    investigation: buildSpatialInvestigation(caseFile, timelineEvents),
    availableModes,
    defaultMode: availableModes[0] ?? null,
    views,
    scenes,
    unlocatedLocationIds,
    unlocatedLocations: caseFile.locations
      .filter((location) => unlocatedLocationIdSet.has(location.id))
      .map((location) => ({
        locationId: location.id,
        label: creatorLabel(location.name, {
          kind: "location",
          index: locationIndexById.get(location.id) ?? 0,
          description: location.description,
        }),
        reason: unlocatedReason(location, locationIds, brokenSceneReferenceIds),
      }))
      .sort((left, right) => left.locationId.localeCompare(right.locationId)),
    counts: {
      locations: caseFile.locations.length,
      events: locatedEventCount,
      geographic: geographicLocations.length,
      scene: sceneLocations.length,
      inferred: inferredIds.length,
      unlocated: unlocatedLocationIds.length,
      scenes: scenes.length,
    },
  };
}

export function buildFixtureSpatialModel(seed: WorkbenchSeed): WorkbenchMapModel {
  const timelineById = new Map(seed.timelineEvents.map((event) => [event.id, event]));
  const locationsByPoint = new Map<string, WorkbenchSpatialLocation>();
  const pointKey = (x: number, y: number) => `${x}:${y}`;

  seed.mapLabels.forEach((label, index) => {
    locationsByPoint.set(pointKey(label.x, label.y), {
      spatialId: `fixture-label-${index}`,
      locationId: null,
      label: label.label,
      source: "schematic",
      position: { kind: "planar", x: label.x, y: label.y },
      events: [],
      relatedObjectIds: [],
    });
  });
  seed.mapMarkers.forEach((marker, index) => {
    const key = pointKey(marker.x, marker.y);
    const event = timelineById.get(marker.eventId);
    const existing = locationsByPoint.get(key);
    const spatialEvent: WorkbenchSpatialEvent = {
      eventId: marker.eventId,
      label: marker.label,
      time: event?.time ?? "时间未定",
      relatedObjectIds: event?.relatedObjectIds ?? [],
    };
    if (existing) {
      existing.events.push(spatialEvent);
      existing.relatedObjectIds = [
        ...new Set([...existing.relatedObjectIds, ...spatialEvent.relatedObjectIds]),
      ];
      return;
    }
    locationsByPoint.set(key, {
      spatialId: `fixture-marker-${index}`,
      locationId: null,
      label: event?.location ?? marker.label,
      source: "schematic",
      position: { kind: "planar", x: marker.x, y: marker.y },
      events: [spatialEvent],
      relatedObjectIds: spatialEvent.relatedObjectIds,
    });
  });

  const locations = [...locationsByPoint.values()];
  const views: WorkbenchMapModel["views"] = {
    geographic: emptyView("geographic"),
    scene: { mode: "scene", locations, relations: [] },
    topology: emptyView("topology"),
  };
  return {
    availableModes: locations.length ? ["scene"] : [],
    defaultMode: locations.length ? "scene" : null,
    views,
    unlocatedLocationIds: [],
    unlocatedLocations: [],
    counts: {
      locations: locations.length,
      events: seed.mapMarkers.length,
      geographic: 0,
      scene: locations.length,
      inferred: 0,
      unlocated: 0,
    },
  };
}
