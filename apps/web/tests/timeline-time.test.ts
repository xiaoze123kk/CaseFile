import { describe, expect, it } from "vitest";

import {
  formatAxisTime,
  formatWallClock,
  parseWallClock,
  shiftTemporalPosition,
  timelineClock,
  timelineEventTime,
  timelineEventTimeRange,
} from "@/features/analyst-workbench/timeline/timeline-time";

describe("timeline wall-clock helpers", () => {
  it("encodes fictional wall-clock values without browser timezone conversion", () => {
    expect(parseWallClock("2042-06-01T20:15")).toBe(
      Date.UTC(2042, 5, 1, 20, 15),
    );
    expect(formatWallClock(Date.UTC(2042, 5, 1, 20, 15), "minute")).toBe(
      "2042-06-01T20:15",
    );
    expect(parseWallClock("2042-06-01T20:15+08:00")).toBeNull();
    expect(parseWallClock("2042-02-30T20:15")).toBeNull();
  });

  it("keeps timeline labels at minute precision without exposing timezone or seconds", () => {
    expect(timelineClock("2042-06-01T09:00:00+08:00")).toBe("09:00");
    expect(timelineClock("约 2042-06-01T10:15:41.125+08:00")).toBe("约 10:15");
    expect(
      timelineClock("2042-06-01T14:00:00+08:00 – 2042-06-01T18:30:00+08:00"),
    ).toBe("14:00–18:30");
  });

  it("separates proportional-axis ticks from complete event readings", () => {
    const midnight = Date.UTC(2042, 5, 1);
    expect(formatAxisTime(midnight, "date")).toBe("06-01");
    expect(formatAxisTime(midnight, "date-time")).toBe("06-01 00:00");
    expect(formatAxisTime(midnight, "time")).toBe("00:00");

    expect(timelineEventTime("2042-06-01T09:00:00+08:00")).toBe(
      "06-01 09:00",
    );
    expect(timelineEventTime("约 2042-06-01T10:15:41.125+08:00")).toBe(
      "约 06-01 10:15",
    );
    expect(
      timelineEventTime(
        "2042-06-01T14:00:00+08:00 – 2042-06-01T18:30:00+08:00",
      ),
    ).toBe("06-01 14:00–18:30");
    expect(
      timelineEventTime(
        "2042-06-01T23:00:00+08:00 – 2042-06-02T01:30:00+08:00",
      ),
    ).toBe("06-01 23:00–06-02 01:30");
    expect(timelineEventTime("时间未定")).toBe("时间未定");
  });

  it("formats projected relative bounds as a point or an uncertainty range", () => {
    expect(
      timelineEventTimeRange("2042-06-01T20:15:00", null),
    ).toBe("06-01 20:15");
    expect(
      timelineEventTimeRange("2042-06-01T20:15:00", "2042-06-01T20:15:00"),
    ).toBe("06-01 20:15");
    expect(
      timelineEventTimeRange("2042-06-01T20:15:00", "2042-06-01T20:25:00"),
    ).toBe("06-01 20:15–20:25");
    expect(
      timelineEventTimeRange("2042-06-01T23:15:00", "2042-06-02T00:05:00"),
    ).toBe("06-01 23:15–06-02 00:05");
  });

  it("moves points and whole ranges while preserving precision and duration", () => {
    expect(
      shiftTemporalPosition(
        { kind: "exact", value: "2042-06-01T20:15", precision: "minute" },
        Date.UTC(2042, 5, 1, 20, 22, 41),
      ),
    ).toEqual({
      kind: "exact",
      value: "2042-06-01T20:22",
      precision: "minute",
    });
    expect(
      shiftTemporalPosition(
        {
          kind: "range",
          start: "2042-06-01T20:00",
          end: "2042-06-01T20:03",
          precision: "minute",
        },
        Date.UTC(2042, 5, 1, 20, 10),
      ),
    ).toEqual({
      kind: "range",
      start: "2042-06-01T20:10",
      end: "2042-06-01T20:13",
      precision: "minute",
    });
  });
});
