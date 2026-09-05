import { describe, expect, it } from "vitest";
import { collaborationReducer as reduce, initialCollaborationState as initial } from "@/features/analyst-workbench/workbench-collaboration-state";
import { composerReducer, composerFocus, emptyFocus, type ComposerEntries } from "@/features/analyst-workbench/workbench-agent-context";

describe("collaboration navigation", () => {
  it.each(["workbench", "dossier"] as const)("hosts %s Agent and Patch centrally", (mode) => {
    let state = reduce(initial, { type: "open_agent", mode });
    state = reduce(state, { type: "push_detail", mode, detail: { kind: "patch", patchId: 7 } });
    expect(state.agentSurface).toBe("center");
    expect(state.inspectorOpen).toBe(true);
    expect(state.centerStack).toHaveLength(1);
    state = reduce(state, { type: "escape" });
    expect(state.centerStack).toHaveLength(0);
    expect(state.agentSurface).toBe("center");
    expect(reduce(state, { type: "escape" }).agentSurface).toBe("dock");
  });
  it.each(["analysis", "compile"] as const)("hosts %s Agent and details in sidebar", (mode) => {
    let state = reduce(initial, { type: "open_agent", mode });
    state = reduce(state, { type: "push_detail", mode, detail: { kind: "patch", patchId: 7 } });
    state = reduce(state, { type: "push_detail", mode, detail: { kind: "provenance", objectId: "gone" } });
    state = reduce(state, { type: "escape" });
    expect(state.sideStack).toEqual([{ kind: "patch", patchId: 7 }]);
    expect(state.agentSurface).toBe("side");
    expect(reduce(state, { type: "switch_side_base", base: "object" }).agentSurface).toBe("dock");
    expect(reduce(state, { type: "inspector", open: false }).agentSurface).toBe("dock");
  });
  it("preserves stable targets through selection and moves Patch on mode changes", () => {
    let state = reduce(initial, { type: "push_detail", mode: "workbench", detail: { kind: "patch", patchId: 9 } });
    state = reduce(state, { type: "push_detail", mode: "workbench", detail: { kind: "relation", relationId: "r", objectId: "a" } });
    state = reduce(state, { type: "selection", selection: { objectId: "b" } });
    expect(state.sideStack[0]).toMatchObject({ objectId: "a" });
    state = reduce(state, { type: "mode_changed", mode: "analysis" });
    expect(state.centerStack).toEqual([]);
    expect(state.sideStack.at(-1)).toEqual({ kind: "patch", patchId: 9 });
  });
});

describe("per-thread next-message context", () => {
  it("separates candidate selection, pinned tags, removals and drafts", () => {
    const candidate = { ...emptyFocus(), object_ids: ["a"], view: "relations" };
    let state: ComposerEntries = composerReducer({}, { type: "text", threadId: 1, candidate, text: "草稿" });
    state = composerReducer(state, { type: "add", threadId: 1, candidate, item: { kind: "event", id: "e", label: "事件" } });
    state = composerReducer(state, { type: "remove", threadId: 1, candidate, item: { kind: "view", id: "relations", label: "关系图" } });
    state = composerReducer(state, { type: "text", threadId: 2, candidate: emptyFocus(), text: "另一个草稿" });
    expect(state[1].text).toBe("草稿");
    const frozen = composerFocus(state[1]);
    state = composerReducer(state, { type: "candidate", threadId: 1, candidate: { ...candidate, object_ids: ["b"] } });
    expect(frozen).toEqual({ object_ids: ["a"], event_ids: ["e"], validation_issue_ids: [], view: null });
    expect(composerFocus(state[1]).object_ids).toEqual(["b"]);
    expect(state[2].text).toBe("另一个草稿");
    state = composerReducer(state, { type: "sent", threadId: 1, candidate, text: "草稿" });
    expect(state[1].text).toBe("");
    expect(composerFocus(state[1]).event_ids).toEqual(["e"]);
  });
  it("does not erase text typed while a send is in flight", () => {
    const candidate = emptyFocus();
    const state = composerReducer({}, { type: "text", threadId: 1, candidate, text: "新草稿" });
    expect(composerReducer(state, { type: "sent", threadId: 1, candidate, text: "旧草稿" })[1].text).toBe("新草稿");
  });
});
