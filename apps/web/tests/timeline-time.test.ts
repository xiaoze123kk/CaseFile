import { describe, expect, it } from "vitest";

import {
  formatWallClock,
  parseWallClock,
  shiftTemporalPosition,
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
