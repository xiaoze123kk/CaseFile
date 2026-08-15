import { describe, expect, it } from "vitest";

import {
  createObjectNavigationHistory,
  moveObjectHistoryBack,
  moveObjectHistoryForward,
  objectFocusBackTarget,
  objectFocusForwardTarget,
  recordObjectFocus,
} from "@/features/analyst-workbench/workbench-navigation-history";

describe("workbench navigation history", () => {
  it("moves back and forward across recorded object focus frames", () => {
    let history = createObjectNavigationHistory({ objectId: "obj-a", view: "timeline" });
    history = recordObjectFocus(history, { objectId: "obj-b", view: "relations" });
    history = recordObjectFocus(history, { objectId: "obj-c", view: "timeline" });

    expect(objectFocusBackTarget(history)).toEqual({ objectId: "obj-b", view: "relations" });
    expect(objectFocusForwardTarget(history)).toBeNull();

    history = moveObjectHistoryBack(history);
    expect(objectFocusForwardTarget(history)).toEqual({ objectId: "obj-c", view: "timeline" });
    history = moveObjectHistoryBack(history);
    expect(objectFocusBackTarget(history)).toBeNull();

    history = moveObjectHistoryForward(history);
    expect(objectFocusForwardTarget(history)).toEqual({ objectId: "obj-c", view: "timeline" });
  });

  it("truncates the forward branch when a new focus is recorded after going back", () => {
    let history = createObjectNavigationHistory({ objectId: "obj-a", view: "timeline" });
    history = recordObjectFocus(history, { objectId: "obj-b", view: "relations" });
    history = recordObjectFocus(history, { objectId: "obj-c", view: "timeline" });
    history = moveObjectHistoryBack(history);
    history = recordObjectFocus(history, { objectId: "obj-d", view: "evidence" });

    expect(history.frames.map((frame) => frame.objectId)).toEqual([
      "obj-a",
      "obj-b",
      "obj-d",
    ]);
    expect(objectFocusForwardTarget(history)).toBeNull();
  });

  it("ignores repeated selection of the currently focused object", () => {
    let history = createObjectNavigationHistory({ objectId: "obj-a", view: "timeline" });
    history = recordObjectFocus(history, { objectId: "obj-b", view: "relations" });
    history = recordObjectFocus(history, { objectId: "obj-b", view: "timeline" });

    expect(history.frames).toEqual([
      { objectId: "obj-a", view: "timeline" },
      { objectId: "obj-b", view: "relations" },
    ]);
  });

  it("caps long sessions by dropping the oldest frames", () => {
    let history = createObjectNavigationHistory({ objectId: "obj-0", view: "timeline" }, 3);
    for (const objectId of ["obj-1", "obj-2", "obj-3", "obj-4"]) {
      history = recordObjectFocus(history, { objectId, view: "timeline" });
    }

    expect(history.frames.map((frame) => frame.objectId)).toEqual([
      "obj-2",
      "obj-3",
      "obj-4",
    ]);
    expect(history.cursor).toBe(2);
    expect(objectFocusBackTarget(history)?.objectId).toBe("obj-3");
  });
});
