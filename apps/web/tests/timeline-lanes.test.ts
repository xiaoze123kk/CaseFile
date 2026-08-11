import { describe, expect, it } from "vitest";

import {
  defaultWorkbenchSeed,
  type WorkbenchSeed,
} from "@/features/analyst-workbench/analyst-fixture";
import {
  buildTimelineCertaintySummary,
  buildTimelineLanes,
  timelineCertainty,
  type TimelineDisplayEvent,
} from "@/features/analyst-workbench/timeline/timeline-lanes";

function seed(): WorkbenchSeed {
  return JSON.parse(JSON.stringify(defaultWorkbenchSeed)) as WorkbenchSeed;
}

describe("timeline lane and certainty models", () => {
  it("groups fixture events into stable person and location lanes", () => {
    const current = seed();
    const events = current.timelineEvents as TimelineDisplayEvent[];

    const people = buildTimelineLanes(current, events, "people");
    const locations = buildTimelineLanes(current, events, "locations");

    expect(people.map((lane) => lane.label)).toEqual(["唐默", "林岚", "秦彻"]);
    expect(people[0].eventIds).toEqual(["EV-1800", "EV-1825"]);
    expect(locations.map((lane) => lane.id)).toEqual(["LOC-003", "LOC-007"]);
  });

  it("prefers normalized references from a real Current Draft event", () => {
    const current = seed();
    current.caseObjects = current.caseObjects.map((object) =>
      object.id === "PER-001"
        ? ({ ...object, kind: "entity", subtype: "person" } as never)
        : object,
    );
    const event = {
      ...current.timelineEvents[0],
      relatedObjectIds: [],
      refs: {
        participantIds: ["PER-001"],
        locationId: "LOC-007",
        causeIds: [],
        effectIds: [],
        observerIds: [],
        sourceIds: [],
      },
    } as TimelineDisplayEvent;

    expect(buildTimelineLanes(current, [event], "people")[0]).toMatchObject({
      id: "PER-001",
      label: "秦彻",
    });
    expect(buildTimelineLanes(current, [event], "locations")[0].id).toBe("LOC-007");
  });

  it("distinguishes all five time semantics and keeps relative/unknown off-axis", () => {
    const current = seed();
    const base = current.timelineEvents[0];
    const temporal = [
      { kind: "exact", value: "2042-06-01T20:00", precision: "minute" },
      { kind: "approximate", value: "2042-06-01T20:05", precision: "minute" },
      {
        kind: "range",
        start: "2042-06-01T20:10",
        end: "2042-06-01T20:15",
        precision: "minute",
      },
      {
        kind: "relative",
        anchor_event_ref: { object_type: "event", object_id: "evt_anchor" },
        relation: "after",
        offset_minutes: 5,
      },
      { kind: "unknown" },
    ] as const;
    const events = temporal.map(
      (time, index) =>
        ({
          ...base,
          id: `evt_${index}`,
          source: { time },
        }) as TimelineDisplayEvent,
    );

    expect(events.map(timelineCertainty)).toEqual([
      "exact",
      "approximate",
      "range",
      "relative",
      "unknown",
    ]);
    expect(buildTimelineCertaintySummary(events)).toEqual([
      { kind: "exact", label: "准确", count: 1, axis: "axis" },
      { kind: "approximate", label: "约略", count: 1, axis: "axis" },
      { kind: "range", label: "区间", count: 1, axis: "axis" },
      { kind: "relative", label: "相对", count: 1, axis: "off-axis" },
      { kind: "unknown", label: "未定", count: 1, axis: "off-axis" },
    ]);
  });
});
