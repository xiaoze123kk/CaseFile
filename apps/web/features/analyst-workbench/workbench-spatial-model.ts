import type { ObjectRef } from "@casefile/contracts";

import type { CaseFileDocument } from "@/lib/api-client";

import type { WorkbenchSeed } from "./analyst-fixture";
import type {
  WorkbenchMapModel,
  WorkbenchSpatialEvent,
  WorkbenchSpatialLocation,
  WorkbenchSpatialMode,
  WorkbenchSpatialPosition,
  WorkbenchTimelineEvent,
} from "./workbench-real-data-types";

type ContractLocation = CaseFileDocument["locations"][number];

type ContractSpatialPosition =
  | { kind: "schematic"; x: number; y: number }
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

function readSpatialPosition(
  location: ContractLocation,
): ContractSpatialPosition | null {
  const position = location.spatial_position;
  if (!position) return null;
  if (position.coordinate_system === "schematic") {
    const x = finiteNumber(position.x);
    const y = finiteNumber(position.y);
    return x !== null && y !== null && x >= 0 && x <= 100 && y >= 0 && y <= 100
      ? { kind: "schematic", x, y }
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
      next.set(id, {
        x: clamp(targetX * 0.76 + anchor.x * 0.24, 3, 97),
        y: clamp(targetY * 0.76 + anchor.y * 0.24, 3, 97),
        inferred: true,
      });
    }
    for (const [id, position] of next) positions.set(id, position);
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
  source: WorkbenchSpatialLocation["source"];
  position: WorkbenchSpatialPosition;
  events: WorkbenchSpatialEvent[];
}): WorkbenchSpatialLocation {
  return {
    spatialId: input.location.id,
    locationId: input.location.id,
    label: input.location.name,
    source: input.source,
    position: input.position,
    events: input.events,
    relatedObjectIds: locationRelatedObjectIds(input.location.id, input.events),
  };
}

function emptyView(mode: WorkbenchSpatialMode) {
  return { mode, locations: [] } satisfies WorkbenchMapModel["views"][WorkbenchSpatialMode];
}

export function buildWorkbenchSpatialModel(
  caseFile: CaseFileDocument,
  timelineEvents: WorkbenchTimelineEvent[],
): WorkbenchMapModel {
  const positions = new Map(
    caseFile.locations.map((location) => [location.id, readSpatialPosition(location)]),
  );
  const eventMap = eventsByLocation(timelineEvents);
  const geographicLocations = caseFile.locations
    .flatMap((location) => {
      const position = positions.get(location.id);
      return position?.kind === "wgs84"
        ? [
            makeSpatialLocation({
              location,
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
      (location) => !explicitById.has(location.id) && Boolean(neighborWeights.get(location.id)?.size),
    )
    .map((location) => location.id)
    .sort();
  const inferredIdSet = new Set(inferredIds);
  const unlocatedLocationIds = planarLocations
    .filter((location) => !explicitById.has(location.id) && !inferredIdSet.has(location.id))
    .map((location) => location.id)
    .sort();

  const sceneLocations = planarLocations
    .flatMap((location) => {
      const position = explicitById.get(location.id);
      return position
        ? [
            makeSpatialLocation({
              location,
              source: "schematic",
              position: { kind: "planar", ...position },
              events: eventMap.get(location.id) ?? [],
            }),
          ]
        : [];
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
          source: position.inferred ? "inferred" : "schematic",
          position: { kind: "planar", x: position.x, y: position.y },
          events: eventMap.get(location.id) ?? [],
        }),
      ];
    })
    .sort((left, right) => left.spatialId.localeCompare(right.spatialId));

  const views: WorkbenchMapModel["views"] = {
    geographic: { mode: "geographic", locations: geographicLocations },
    scene: { mode: "scene", locations: sceneLocations },
    topology: {
      mode: "topology",
      locations: inferredIds.length ? topologyLocations : [],
    },
  };
  const availableModes = spatialModeOrder.filter(
    (mode) => views[mode].locations.length > 0,
  );
  const locatedEventCount = [...eventMap.values()].reduce(
    (count, events) => count + events.length,
    0,
  );
  return {
    availableModes,
    defaultMode: availableModes[0] ?? null,
    views,
    unlocatedLocationIds,
    counts: {
      locations: caseFile.locations.length,
      events: locatedEventCount,
      geographic: geographicLocations.length,
      scene: sceneLocations.length,
      inferred: inferredIds.length,
      unlocated: unlocatedLocationIds.length,
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
    scene: { mode: "scene", locations },
    topology: emptyView("topology"),
  };
  return {
    availableModes: locations.length ? ["scene"] : [],
    defaultMode: locations.length ? "scene" : null,
    views,
    unlocatedLocationIds: [],
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
