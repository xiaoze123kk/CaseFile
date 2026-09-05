import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { spatialJourneys, type SpatialInvestigation } from "@/features/analyst-workbench/spatial-map/spatial-investigation-model";
import { SpatialActivityStrip, SpatialInvestigationPanel } from "@/features/analyst-workbench/spatial-map/spatial-investigation-panel";
import type { WorkbenchTimelineEvent } from "@/features/analyst-workbench/workbench-real-data-types";

function event(id: string, locationId: string, minute: string, kind = "exact"): WorkbenchTimelineEvent {
  const time = `2026-09-05T21:${minute}:00`;
  return {
    id, label: id, time, location: locationId, summary: "", relatedObjectIds: [], issueIds: [],
    start: time, end: null, precision: "minute", truthStatus: "confirmed", sortKey: time, timeProjection: "absolute",
    refs: { participantIds: ["person"], locationId, causeIds: [], effectIds: [], observerIds: [], sourceIds: [] },
    source: { time: { kind, value: time, precision: "minute" } } as WorkbenchTimelineEvent["source"],
  };
}
function model(): SpatialInvestigation {
  return {
    people: [{ id: "person", label: "林舟" }, { id: "other", label: "夏岚" }],
    locations: [{ id: "小屋", label: "小屋", parentId: null }, { id: "观星台", label: "观星台", parentId: null }],
    passages: [{ from: "小屋", to: "观星台", minutes: 25 }],
    events: [event("离开小屋", "小屋", "00"), event("出现在观星台", "观星台", "10")],
  };
}
afterEach(cleanup);
describe("spatial investigation", () => {
  it("compares certain event gaps with explicit directional travel times", () => {
    expect(spatialJourneys(model())[0]).toMatchObject({ available: 10, required: 25, status: "conflict" });
    const data = model(); data.events[1].start = "2026-09-05T21:30:00";
    expect(spatialJourneys(data)[0].status).toBe("recorded");
    data.passages[0] = { from: "观星台", to: "小屋", minutes: 25 };
    expect(spatialJourneys(data)[0]).toMatchObject({ status: "missing-travel", required: null });
  });
  it("does not infer certainty from approximate, relative, unknown or overlapping times", () => {
    for (const kind of ["approximate", "relative", "unknown"]) {
      const data = model(); data.events[0] = event("离开小屋", "小屋", "00", kind);
      expect(spatialJourneys(data)[0].status).toBe("uncertain-time");
    }
    const data = model(); data.events[0].end = data.events[1].start;
    expect(spatialJourneys(data)[0].status).toBe("uncertain-time");
    data.events[0].end = "invalid";
    expect(spatialJourneys(data)[0].available).toBeNull();
  });
  it("uses event end times and skips parent-child transitions and broken references", () => {
    const data = model(); data.events[0].end = "2026-09-05T21:05:00";
    expect(spatialJourneys(data)[0].available).toBe(5);
    data.locations[1].parentId = "小屋";
    expect(spatialJourneys(data)).toEqual([]);
    data.locations = [];
    expect(spatialJourneys(data)).toEqual([]);
  });
  it("reuses resolved relative times only when their anchor is certain", () => {
    const data = model();
    data.events[1].timeProjection = "relative-resolved";
    data.events[1].source = { ...data.events[1].source!, time: { kind: "relative", anchor_event_ref: { object_type: "event", object_id: data.events[0].id }, relation: "after", offset_minutes: 10 } } as WorkbenchTimelineEvent["source"];
    expect(spatialJourneys(data)[0].status).toBe("conflict");
    data.events[0].source = { time: { kind: "approximate", value: data.events[0].start, precision: "minute" } } as WorkbenchTimelineEvent["source"];
    expect(spatialJourneys(data)[0].status).toBe("uncertain-time");
    data.events[0].source = data.events[1].source;
    data.events[0].timeProjection = "relative-resolved";
    expect(spatialJourneys(data)[0].status).toBe("uncertain-time");
  });
  it("opens a concrete journey, its two events and departure location settings", () => {
    const data = model(); const journeys = spatialJourneys(data); const onJourney = vi.fn();
    const onEvent = vi.fn(); const onLocation = vi.fn(); const onPerson = vi.fn();
    render(<SpatialInvestigationPanel model={data} journeys={journeys} personId="" activeJourneyId={null}
      onJourney={onJourney} onEvent={onEvent} onLocation={onLocation} onPerson={onPerson} />);
    fireEvent.click(screen.getByRole("button", { name: /通行时间不足/ }));
    expect(onJourney).toHaveBeenCalledWith(journeys[0]);
    fireEvent.change(screen.getByLabelText("筛选人物行踪"), { target: { value: "other" } });
    expect(onPerson).toHaveBeenCalledWith("other");
    cleanup();
    render(<SpatialActivityStrip model={data} personId="person" selectedEventId="离开小屋" journey={journeys[0]} onEvent={onEvent} onLocation={onLocation} />);
    fireEvent.click(screen.getByRole("button", { name: /出现在观星台/ }));
    expect(onEvent).toHaveBeenCalledWith("出现在观星台");
    fireEvent.click(screen.getByRole("button", { name: "查看出发地通行设定" }));
    expect(onLocation).toHaveBeenCalledWith("小屋");
    expect(screen.getByText(/记录之间位置未知/)).toBeInTheDocument();
  });
  it("exposes missing event locations even when nothing can be drawn", () => {
    const data = model(); data.locations = []; data.events[0].refs.locationId = null;
    const onEvent = vi.fn();
    render(<SpatialInvestigationPanel model={data} journeys={[]} personId="" activeJourneyId={null}
      onJourney={vi.fn()} onEvent={onEvent} onLocation={vi.fn()} onPerson={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /事件未指定地点/ }));
    expect(onEvent).toHaveBeenCalledWith("离开小屋");
    expect(screen.getByRole("button", { name: /事件地点引用失效/ })).toBeInTheDocument();
  });
});
