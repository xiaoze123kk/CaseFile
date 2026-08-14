import { describe, expect, it } from "vitest";

import {
  layoutWorkbenchCanvas,
  layoutWorkbenchMatrixCanvas,
  restoreWorkbenchCanvasLayout,
  saveWorkbenchCanvasLayout,
  workbenchCanvasLayoutStorageKey,
  type WorkbenchCanvasLayoutIdentity,
  type WorkbenchCanvasStorage,
} from "@/features/analyst-workbench/workbench-canvas-layout";

class MemoryStorage implements WorkbenchCanvasStorage {
  private readonly values = new Map<string, string>();

  get length() {
    return this.values.size;
  }

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

const relationIdentity: WorkbenchCanvasLayoutIdentity = {
  scope: "project:34:current",
  revision: "R7",
  view: "relations",
};

describe("workbench canvas layout", () => {
  it("creates deterministic semantic layouts for relationship and reasoning scenes", () => {
    const nodes = [
      { id: "evidence", width: 120, height: 60 },
      { id: "step", width: 180, height: 70 },
      { id: "hypothesis", width: 160, height: 60 },
    ];
    const edges = [
      { id: "evidence-step", source: "evidence", target: "step" },
      { id: "step-hypothesis", source: "step", target: "hypothesis" },
    ];

    const horizontal = layoutWorkbenchCanvas(nodes, edges, "LR");
    expect(layoutWorkbenchCanvas(nodes, edges, "LR")).toEqual(horizontal);
    expect(horizontal.evidence.x).toBeLessThan(horizontal.step.x);
    expect(horizontal.step.x).toBeLessThan(horizontal.hypothesis.x);

    const reasoning = layoutWorkbenchCanvas(nodes, edges, "BT");
    expect(reasoning.evidence.y).toBeGreaterThan(reasoning.step.y);
    expect(reasoning.step.y).toBeGreaterThan(reasoning.hypothesis.y);
  });

  it("restores the exact revision with its viewport", () => {
    const storage = new MemoryStorage();
    saveWorkbenchCanvasLayout(
      storage,
      relationIdentity,
      { node: { x: 240, y: 120 } },
      { x: -20, y: 14, zoom: 1.25 },
      10,
    );

    expect(
      restoreWorkbenchCanvasLayout(storage, relationIdentity, {
        node: { x: 0, y: 0 },
      }),
    ).toEqual({
      positions: { node: { x: 240, y: 120 } },
      viewport: { x: -20, y: 14, zoom: 1.25 },
      source: "current",
      warning: null,
    });
  });

  it("merges a previous revision by stable node id and auto-places new nodes", () => {
    const storage = new MemoryStorage();
    saveWorkbenchCanvasLayout(
      storage,
      relationIdentity,
      {
        removed: { x: 10, y: 10 },
        retained: { x: 320, y: 180 },
      },
      { x: 5, y: 6, zoom: 0.9 },
      20,
    );
    const nextIdentity = { ...relationIdentity, revision: "R8" };

    const restored = restoreWorkbenchCanvasLayout(storage, nextIdentity, {
      retained: { x: 0, y: 0 },
      added: { x: 80, y: 40 },
    });

    expect(restored.source).toBe("previous-revision");
    expect(restored.positions).toEqual({
      retained: { x: 320, y: 180 },
      added: { x: 80, y: 40 },
    });
    expect(restored.positions).not.toHaveProperty("removed");
  });

  it("places matrix hypotheses as columns and information as rows", () => {
    const positions = layoutWorkbenchMatrixCanvas([
      { id: "hyp_b", width: 200, height: 64, kind: "hypothesis" },
      { id: "hyp_a", width: 180, height: 64, kind: "hypothesis" },
      { id: "info_x", width: 190, height: 64, kind: "information" },
      { id: "info_y", width: 190, height: 64, kind: "information" },
    ]);

    // 假设按 id 排序排在同一行，信息按 id 排序排在同一列。
    expect(positions.hyp_a.y).toBe(positions.hyp_b.y);
    expect(positions.hyp_a.x).toBeLessThan(positions.hyp_b.x);
    expect(positions.info_x.x).toBe(positions.info_y.x);
    expect(positions.info_y.y).toBeGreaterThan(positions.info_x.y);
    // 信息行整体位于假设行下方。
    expect(positions.info_x.y).toBeGreaterThan(positions.hyp_a.y);
  });

  it("falls back safely when stored JSON is corrupt or storage rejects writes", () => {
    const storage = new MemoryStorage();
    storage.setItem(workbenchCanvasLayoutStorageKey(relationIdentity), "{bad");
    const restored = restoreWorkbenchCanvasLayout(storage, relationIdentity, {
      node: { x: 12, y: 14 },
    });

    expect(restored.source).toBe("automatic");
    expect(restored.positions.node).toEqual({ x: 12, y: 14 });
    expect(restored.warning).toMatch(/布局已损坏/);

    const rejectingStorage: WorkbenchCanvasStorage = {
      length: 0,
      getItem: () => null,
      key: () => null,
      setItem: () => {
        throw new Error("quota");
      },
    };
    expect(
      saveWorkbenchCanvasLayout(
        rejectingStorage,
        relationIdentity,
        { node: { x: 1, y: 2 } },
        { x: 0, y: 0, zoom: 1 },
      ),
    ).toMatch(/当前页面保留/);
  });
});
