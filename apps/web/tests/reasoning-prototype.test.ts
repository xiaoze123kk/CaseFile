import { describe, expect, it } from "vitest";

import { layoutReasoningPath } from "@/features/reasoning/reasoning-layout";
import {
  buildReasoningFixture,
  createDefaultReasoningState,
  getPendingReasoningChanges,
} from "@/lib/reasoning-prototype";
import { createDefaultPrototypeState } from "@/lib/prototype-model";
import {
  hydratePrototypeState,
  prototypeReducer,
} from "@/store/prototype-store";

describe("reasoning lab prototype", () => {
  it("starts empty and never generates before a manual trigger", () => {
    const initial = createDefaultPrototypeState();

    expect(initial.reasoning).toEqual(createDefaultReasoningState(18));
    expect(initial.reasoning.status).toBe("idle");
    expect(initial.reasoning.paths).toHaveLength(0);
    expect(initial.reasoning.runs).toHaveLength(0);
  });

  it("pins the current Draft revision and produces reviewable explore candidates", () => {
    const initial = createDefaultPrototypeState();
    const explore = prototypeReducer(initial, {
      type: "set-reasoning-mode",
      mode: "explore",
    });
    const running = prototypeReducer(explore, {
      type: "start-reasoning-run",
    });
    const review = prototypeReducer(running, {
      type: "complete-reasoning-run",
    });

    expect(running.reasoning.baseRevision).toBe(initial.draft.revision);
    expect(running.reasoning.status).toBe("running");
    expect(review.reasoning.status).toBe("review");
    expect(review.reasoning.view).toBe("overview");
    expect(review.reasoning.paths.map((path) => path.kind)).toEqual([
      "primary",
      "alternative",
      "alternative",
      "excluded",
    ]);
    expect(getPendingReasoningChanges(review.reasoning).length).toBeGreaterThan(
      0,
    );
    expect(
      review.reasoning.nodes.some((node) => node.kind === "gap"),
    ).toBe(true);
  });

  it("applies selected candidates in one revision and rejects the rest", () => {
    const initial = createDefaultPrototypeState();
    const running = prototypeReducer(
      prototypeReducer(initial, {
        type: "set-reasoning-mode",
        mode: "explore",
      }),
      { type: "start-reasoning-run" },
    );
    const review = prototypeReducer(running, {
      type: "complete-reasoning-run",
    });
    const selectedIds = review.reasoning.proposals
      .filter((proposal) => proposal.selected)
      .map((proposal) => proposal.id);
    const ready = prototypeReducer(review, {
      type: "apply-reasoning-proposals",
    });

    expect(ready.draft.revision).toBe(initial.draft.revision + 1);
    expect(ready.reasoning.status).toBe("ready");
    expect(ready.reasoning.baseRevision).toBe(ready.draft.revision);
    expect(ready.reasoning.outcomeRevision).toBe(ready.draft.revision);
    expect(
      ready.reasoning.proposals
        .filter((proposal) => selectedIds.includes(proposal.id))
        .every((proposal) => proposal.status === "applied"),
    ).toBe(true);
    expect(
      ready.reasoning.proposals
        .filter((proposal) => !selectedIds.includes(proposal.id))
        .every((proposal) => proposal.status === "rejected"),
    ).toBe(true);
    expect(ready.validation.status).toBe("stale");
  });

  it("keeps an old graph viewable but stale after the Draft changes", () => {
    const initial = createDefaultPrototypeState();
    const running = prototypeReducer(initial, {
      type: "start-reasoning-run",
    });
    const review = prototypeReducer(running, {
      type: "complete-reasoning-run",
    });
    const edited = prototypeReducer(review, {
      type: "update-event",
      id: "EVL-1823",
      field: "time",
      value: "18:24",
    });

    expect(edited.reasoning.status).toBe("stale");
    expect(edited.reasoning.paths).toEqual(review.reasoning.paths);
    expect(edited.reasoning.baseRevision).toBe(18);
    expect(edited.draft.revision).toBe(19);
  });

  it("cancels without producing a candidate graph", () => {
    const initial = createDefaultPrototypeState();
    const running = prototypeReducer(initial, {
      type: "start-reasoning-run",
    });
    const cancelled = prototypeReducer(running, {
      type: "cancel-reasoning-run",
    });

    expect(cancelled.reasoning.status).toBe("cancelled");
    expect(cancelled.reasoning.paths).toHaveLength(0);
    expect(cancelled.reasoning.proposals).toHaveLength(0);
    expect(cancelled.draft).toEqual(initial.draft);
  });

  it("migrates v3 LocalStorage state without changing Draft content", () => {
    const current = createDefaultPrototypeState();
    const legacy: Record<string, unknown> = {
      ...current,
      storageVersion: 3,
    };
    delete legacy.reasoning;
    const migrated = hydratePrototypeState(legacy);

    expect(migrated?.storageVersion).toBe(4);
    expect(migrated?.draft).toEqual(current.draft);
    expect(migrated?.reasoning.status).toBe("idle");
    expect(migrated?.reasoning.baseRevision).toBe(current.draft.revision);
  });

  it("lays out every graph node deterministically", () => {
    const fixture = buildReasoningFixture("explore");
    const path = fixture.paths[0];
    const nodes = fixture.nodes.filter((node) => node.pathId === path.id);
    const edges = fixture.edges.filter((edge) => edge.pathId === path.id);

    const first = layoutReasoningPath(nodes, edges);
    const second = layoutReasoningPath(nodes, edges);

    expect(Object.keys(first)).toHaveLength(nodes.length);
    expect(first).toEqual(second);
    expect(
      Object.values(first).every(
        (position) =>
          Number.isFinite(position.x) && Number.isFinite(position.y),
      ),
    ).toBe(true);
  });
});
