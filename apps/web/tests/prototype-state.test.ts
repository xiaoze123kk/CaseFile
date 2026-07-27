import { describe, expect, it } from "vitest";

import {
  canCompilePrototype,
  createDefaultPrototypeState,
  hasBlockingIssue,
} from "@/lib/prototype-model";
import { prototypeReducer } from "@/store/prototype-store";

describe("prototype state flow", () => {
  it("keeps the original idea when adopting an Agent suggestion", () => {
    const initial = createDefaultPrototypeState();
    const withNewOriginal = prototypeReducer(initial, {
      type: "set-idea-original",
      value: "用户写下的原始创意",
    });
    const suggested = prototypeReducer(withNewOriginal, {
      type: "generate-suggestion",
    });
    const adopted = prototypeReducer(suggested, {
      type: "adopt-suggestion",
    });

    expect(adopted.idea.original).toBe("用户写下的原始创意");
    expect(adopted.idea.working).toBe(initial.idea.suggestion);
    expect(adopted.idea.suggestionStatus).toBe("adopted");
  });

  it("marks validation stale whenever an event is edited", () => {
    const initial = createDefaultPrototypeState();
    const edited = prototypeReducer(initial, {
      type: "update-event",
      id: "EVL-1823",
      field: "time",
      value: "18:24",
    });

    expect(edited.draft.revision).toBe(19);
    expect(edited.draft.lastSavedAt).toBe("待保存");
    expect(edited.validation.status).toBe("stale");
    expect(edited.compiler.status).toBe("blocked");
  });

  it("requires an approved patch and an explicit rerun before compilation", () => {
    const initial = createDefaultPrototypeState();
    expect(hasBlockingIssue(initial)).toBe(true);
    expect(canCompilePrototype(initial)).toBe(false);

    const patched = prototypeReducer(initial, { type: "apply-patch" });
    expect(patched.validation.status).toBe("stale");
    expect(
      patched.validation.issues.find((issue) => issue.id === "VAL-KNOW-001")
        ?.status,
    ).toBe("pending-revalidation");
    expect(canCompilePrototype(patched)).toBe(false);

    const running = prototypeReducer(patched, { type: "start-validation" });
    expect(running.validation.status).toBe("running");

    const validated = prototypeReducer(running, {
      type: "complete-validation",
    });
    expect(validated.validation.status).toBe("fresh");
    expect(
      validated.validation.issues.find(
        (issue) => issue.id === "VAL-KNOW-001",
      )?.status,
    ).toBe("resolved");
    expect(hasBlockingIssue(validated)).toBe(false);
    expect(canCompilePrototype(validated)).toBe(true);
    expect(validated.compiler.status).toBe("idle");

    const building = prototypeReducer(validated, { type: "start-compile" });
    expect(building.compiler.status).toBe("building");

    const completed = prototypeReducer(building, {
      type: "complete-compile",
    });
    expect(completed.compiler.status).toBe("completed");
  });
});
