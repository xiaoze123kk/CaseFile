import type { CaseFile } from "@casefile/contracts";
import { describe, expect, it } from "vitest";

import restartLoopFixture from "../../../fixtures/casefiles/restart_loop.casefile.json";
import {
  buildReferenceIndex,
  collectDocumentReferenceEdges,
  parseObjectRef,
} from "@/features/analyst-workbench/workbench-reference-index";

const document = restartLoopFixture as unknown as CaseFile;

describe("workbench reference index", () => {
  it("parses object refs and traverses nested CaseFile fields", () => {
    expect(parseObjectRef({ object_type: "event", object_id: "evt_1" })).toEqual({
      object_type: "event",
      object_id: "evt_1",
    });
    expect(parseObjectRef({ object_type: "event" })).toBeNull();

    const edges = collectDocumentReferenceEdges(document);
    expect(edges.length).toBeGreaterThan(0);
    expect(edges.some((edge) =>
      edge.source_object_id === "evt_restart_seven" &&
      edge.target_object_id === "ent_researcher" &&
      edge.field_path.includes("observed_by_refs"),
    )).toBe(true);
    expect(edges.some((edge) =>
      edge.source_object_id === "info_restart_log" &&
      edge.target_object_id === "evt_restart_seven" &&
      edge.field_label === "来源事件",
    )).toBe(true);
  });

  it("registers every field that points at an object in the reverse index", () => {
    const index = buildReferenceIndex(document);
    const researcher = index.get("ent_researcher") ?? [];

    expect(researcher).toHaveLength(3);
    expect(researcher.map((edge) => edge.field_label).sort()).toEqual([
      "关系起点",
      "可获得者",
      "观察者",
    ]);
    expect(researcher.every((edge) =>
      edge.field_path.startsWith("/"),
    )).toBe(true);
  });

  it("tracks source fragments and reasoning edges without requiring a backend", () => {
    const index = buildReferenceIndex(document);
    const sourceFragments = index.get("src_restart_log") ?? [];
    const eventIncoming = index.get("evt_restart_seven") ?? [];

    expect(sourceFragments.length).toBeGreaterThan(0);
    expect(sourceFragments.every((edge) => edge.field_label === "来源")).toBe(true);
    expect(eventIncoming.some((edge) =>
      edge.source_object_type === "entity" &&
      edge.field_path.includes("as_of_event_ref"),
    )).toBe(true);
    expect(eventIncoming.some((edge) =>
      edge.source_object_type === "information_unit" &&
      edge.field_path.includes("source_event_ref"),
    )).toBe(true);
  });
});
