import type {
  TimelineEvent,
  WorkbenchSeed,
} from "../analyst-fixture";
import type { WorkbenchTimelineEvent } from "../workbench-real-data-types";

export type TimelineDisplayEvent = TimelineEvent & Partial<WorkbenchTimelineEvent>;
export type TimelineLaneMode = "events" | "people" | "locations";
export type TimelineCertaintyKind =
  | "exact"
  | "approximate"
  | "range"
  | "relative"
  | "unknown";

export interface TimelineLane {
  id: string;
  label: string;
  kind: "person" | "location";
  eventIds: string[];
}

export interface TimelineCertaintySummary {
  kind: TimelineCertaintyKind;
  label: string;
  count: number;
  axis: "axis" | "off-axis";
}

const certaintyOrder: TimelineCertaintyKind[] = [
  "exact",
  "approximate",
  "range",
  "relative",
  "unknown",
];

const certaintyLabels: Record<TimelineCertaintyKind, string> = {
  exact: "准确",
  approximate: "约略",
  range: "区间",
  relative: "相对",
  unknown: "未定",
};

export function timelineCertainty(
  event: TimelineDisplayEvent,
): TimelineCertaintyKind {
  const time = event.source?.time;
  if (time && "kind" in time) return time.kind;
  if (time?.precision === "unknown" || event.precision === "unknown") {
    return "unknown";
  }
  if (time?.precision === "approximate" || event.precision === "approximate") {
    return "approximate";
  }
  if (time?.end || (event.end && event.end !== event.start)) return "range";
  return "exact";
}

export function timelineCertaintyLabel(event: TimelineDisplayEvent) {
  return certaintyLabels[timelineCertainty(event)];
}

export function buildTimelineCertaintySummary(
  events: TimelineDisplayEvent[],
): TimelineCertaintySummary[] {
  const counts = new Map<TimelineCertaintyKind, number>();
  for (const event of events) {
    const kind = timelineCertainty(event);
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  return certaintyOrder.map((kind) => ({
    kind,
    label: certaintyLabels[kind],
    count: counts.get(kind) ?? 0,
    axis: kind === "relative" || kind === "unknown" ? "off-axis" : "axis",
  }));
}

function referencedLaneIds(
  event: TimelineDisplayEvent,
  mode: Exclude<TimelineLaneMode, "events">,
  objectKinds: Map<string, string>,
) {
  const direct =
    mode === "people"
      ? event.refs?.participantIds ?? []
      : event.refs?.locationId
        ? [event.refs.locationId]
        : [];
  if (direct.length) return direct;
  const expectedKind = mode === "people" ? "person" : "location";
  return event.relatedObjectIds.filter(
    (objectId) => objectKinds.get(objectId) === expectedKind,
  );
}

function effectiveObjectKind(object: WorkbenchSeed["caseObjects"][number]) {
  if (
    object.kind === "person" ||
    (object.kind === "entity" &&
      "subtype" in object &&
      object.subtype === "person")
  ) {
    return "person";
  }
  return object.kind;
}

export function buildTimelineLanes(
  seed: Pick<WorkbenchSeed, "caseObjects">,
  events: TimelineDisplayEvent[],
  mode: Exclude<TimelineLaneMode, "events">,
): TimelineLane[] {
  const expectedKind = mode === "people" ? "person" : "location";
  const objects = seed.caseObjects.filter(
    (object) => effectiveObjectKind(object) === expectedKind,
  );
  const objectKinds = new Map(
    seed.caseObjects.map((object) => [object.id, effectiveObjectKind(object)]),
  );
  const labels = new Map(objects.map((object) => [object.id, object.label]));
  const eventIdsByLane = new Map<string, string[]>();

  for (const event of events) {
    const laneIds = referencedLaneIds(event, mode, objectKinds);
    for (const laneId of laneIds) {
      const eventIds = eventIdsByLane.get(laneId) ?? [];
      if (!eventIds.includes(event.id)) eventIds.push(event.id);
      eventIdsByLane.set(laneId, eventIds);
    }
  }

  const eventOrder = new Map(events.map((event, index) => [event.id, index]));
  return [...eventIdsByLane]
    .map(([id, eventIds]): TimelineLane => ({
      id,
      label: labels.get(id) ?? id,
      kind: expectedKind,
      eventIds,
    }))
    .sort((left, right) => {
      const leftIndex = Math.min(
        ...left.eventIds.map((eventId) => eventOrder.get(eventId) ?? Infinity),
      );
      const rightIndex = Math.min(
        ...right.eventIds.map((eventId) => eventOrder.get(eventId) ?? Infinity),
      );
      return leftIndex - rightIndex || left.label.localeCompare(right.label, "zh-CN");
    });
}
