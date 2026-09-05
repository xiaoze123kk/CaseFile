import type { CaseFileDocument } from "@/lib/api-client";
import type { WorkbenchTimelineEvent } from "../workbench-real-data-types";
import { parseWallClock } from "../timeline/timeline-time";

export interface SpatialInvestigation {
  people: Array<{ id: string; label: string }>;
  events: WorkbenchTimelineEvent[];
  locations: Array<{ id: string; label: string; parentId: string | null }>;
  passages: Array<{ from: string; to: string; minutes: number }>;
}

export interface SpatialJourney {
  id: string;
  personId: string;
  from: WorkbenchTimelineEvent;
  to: WorkbenchTimelineEvent;
  available: number | null;
  required: number | null;
  status: "conflict" | "missing-travel" | "uncertain-time" | "recorded";
}

export function buildSpatialInvestigation(
  document: CaseFileDocument,
  events: WorkbenchTimelineEvent[],
): SpatialInvestigation {
  return {
    people: document.entities.filter((entity) => entity.entity_type === "person")
      .map((entity) => ({ id: entity.id, label: entity.name })),
    events,
    locations: document.locations.map((location) => ({
      id: location.id, label: location.name,
      parentId: typeof location.parent_ref?.object_id === "string" ? location.parent_ref.object_id : null,
    })),
    passages: document.locations.flatMap((location) => location.travel_times.flatMap((travel) =>
      typeof travel.to_ref.object_id === "string" ? [{
        from: location.id, to: travel.to_ref.object_id, minutes: travel.minutes,
      }] : [])),
  };
}

/** An author-facing comparison, not a replacement for server validation. */
export function spatialJourneys(model: SpatialInvestigation): SpatialJourney[] {
  const result: SpatialJourney[] = [];
  const eventById = new Map(model.events.map((event) => [event.id, event]));
  function certain(event: WorkbenchTimelineEvent, seen = new Set<string>()): boolean {
    const time = event.source?.time;
    if (!time || !("kind" in time) || seen.has(event.id)) return false;
    if (time.kind === "exact" || time.kind === "range") return true;
    if (time.kind !== "relative" || event.timeProjection !== "relative-resolved") return false;
    const anchorId = time.anchor_event_ref?.object_id;
    const anchor = typeof anchorId === "string" ? eventById.get(anchorId) : undefined;
    seen.add(event.id);
    return Boolean(anchor && certain(anchor, seen));
  }
  for (const person of model.people) {
    // The timeline projection already orders resolved times and retains unknown records.
    const events = model.events.filter((event) => event.refs.participantIds.includes(person.id));
    for (let index = 1; index < events.length; index += 1) {
      const from = events[index - 1];
      const to = events[index];
      const left = from.refs.locationId;
      const right = to.refs.locationId;
      if (!left || !right || left === right) continue;
      if (!model.locations.some((location) => location.id === left)
        || !model.locations.some((location) => location.id === right)) continue;
      if (model.locations.some((location) =>
        (location.id === left && location.parentId === right)
        || (location.id === right && location.parentId === left))) continue;
      const required = model.passages.find((passage) => passage.from === left && passage.to === right)?.minutes ?? null;
      // Reuse resolved timeline bounds; relative chains must end in a certain anchor.
      const leftEnd = from.end ?? from.start;
      const rightStart = to.start;
      const leftValue = leftEnd ? parseWallClock(leftEnd) : null;
      const rightValue = rightStart ? parseWallClock(rightStart) : null;
      const gap = certain(from) && certain(to) && leftValue !== null && rightValue !== null
        ? (rightValue - leftValue) / 60_000 : NaN;
      const available = Number.isFinite(gap) && gap > 0 ? gap : null;
      result.push({
        id: `${person.id}:${from.id}:${to.id}`, personId: person.id, from, to, available, required,
        status: available === null ? "uncertain-time" : required === null ? "missing-travel"
          : available < required ? "conflict" : "recorded",
      });
    }
  }
  return result;
}
