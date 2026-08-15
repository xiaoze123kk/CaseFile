import { describe, expect, it } from "vitest";

import { computeSpatialClusters } from "@/features/analyst-workbench/spatial-map/spatial-clustering";

describe("spatial clustering", () => {
  it("groups only markers whose screen distance is inside the radius", () => {
    const clusters = computeSpatialClusters(
      new Map([
        ["a", { x: 0, y: 0 }],
        ["b", { x: 40, y: 0 }],
        ["c", { x: 200, y: 0 }],
        ["d", { x: 0, y: 200 }],
      ]),
      { radius: 56, excludedKeys: new Set() },
    );

    expect(clusters).toEqual([{ keys: ["a", "b"], x: 20, y: 0 }]);
  });

  it("keeps selected or edited markers out of clusters", () => {
    const clusters = computeSpatialClusters(
      new Map([
        ["selected", { x: 0, y: 0 }],
        ["a", { x: 20, y: 0 }],
        ["b", { x: 40, y: 0 }],
        ["edited", { x: 100, y: 100 }],
        ["c", { x: 110, y: 100 }],
      ]),
      { radius: 56, excludedKeys: new Set(["selected", "edited"]) },
    );

    expect(clusters).toEqual([{ keys: ["a", "b"], x: 30, y: 0 }]);
  });

  it("is deterministic regardless of input insertion order", () => {
    const options = { radius: 56, excludedKeys: new Set<string>() };
    const first = computeSpatialClusters(
      new Map([
        ["z", { x: 0, y: 0 }],
        ["a", { x: 30, y: 0 }],
      ]),
      options,
    );
    const second = computeSpatialClusters(
      new Map([
        ["a", { x: 30, y: 0 }],
        ["z", { x: 0, y: 0 }],
      ]),
      options,
    );

    expect(first).toEqual(second);
  });
});
