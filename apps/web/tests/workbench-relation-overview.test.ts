import { describe, expect, it } from "vitest";
import type { ContextRelation, ContextRelationEndpoint, ContextRelationModel } from "@/features/analyst-workbench/workbench-relation-model";
import { buildRelationOverview } from "@/features/analyst-workbench/workbench-relation-overview";

function endpoint(id: string, objectType: string): ContextRelationEndpoint {
  return { id, objectType, label: id, kindLabel: objectType, missing: false, selectable: true };
}

function relation(id: string, fieldLabel: string, selectedType = "event", overrides: Partial<ContextRelation> = {}): ContextRelation {
  return {
    id, fieldLabel, group: "events", verb: "关联", arrow: "→", fieldPath: "/refs/0",
    subject: endpoint("selected", selectedType), object: endpoint(id, "event"),
    counterpart: endpoint(id, "event"), ...overrides,
  };
}

function model(relations: ContextRelation[]): ContextRelationModel {
  return {
    groups: [{ id: "events", title: "参与事件", relations }], incoming: [],
    totals: { all: relations.length, direct: 0, events: relations.length, information: 0, reasoning: 0, incoming: 0 },
  };
}

describe("object relation overview", () => {
  it("puts event causality and source information before participants and cognition", () => {
    const input = model([
      relation("knowledge", "认知时点"), relation("person", "参与者"),
      relation("clue", "来源事件"), relation("cause", "原因事件"),
      relation("effect", "结果事件"),
    ]);
    const overview = buildRelationOverview(input);
    expect(overview.preview.map((item) => item.relation.id)).toEqual(["cause", "effect", "clue", "person"]);
    expect(overview.remaining[0].relation.id).toBe("knowledge");
    expect(input.groups[0].relations[0].id).toBe("knowledge");
  });

  it.each([
    ["entity", "参与者", "有向关系", { fieldPath: null, group: "direct" as const }],
    ["information_unit", "已知", "来源事件", {}],
    ["location", "相邻地点", "发生地点", {}],
    ["hypothesis", "关联", "必要依据", { group: "reasoning" as const }],
    ["resolution_spec", "关联", "结论答案", { group: "reasoning" as const }],
  ])("prioritizes relevant context for %s", (type, otherField, primaryField, overrides) => {
    const other = relation("other", otherField, type, {
      counterpart: endpoint("other", type === "location" ? "location" : "entity"),
    });
    const primary = relation("primary", primaryField, type, overrides);
    expect(buildRelationOverview(model([other, primary])).preview[0].relation.id).toBe("primary");
  });

  it("uses actual subject direction for causes and effects, including reverse references", () => {
    const incoming = relation("before", "结果事件", "event", {
      subject: endpoint("before", "event"), object: endpoint("selected", "event"),
    });
    const outgoing = relation("after", "原因事件");
    const { preview } = buildRelationOverview(model([incoming, outgoing]));
    expect(preview[0]).toMatchObject({ label: "前因", description: "引发本事件" });
    expect(preview[1]).toMatchObject({ label: "后果", description: "由本事件引发" });
    expect(preview[0].flow).toMatchObject({ left: "selected", right: "before", direction: "incoming" });
    expect(preview[1].flow).toMatchObject({ left: "selected", right: "after", direction: "outgoing" });
  });

  it("expresses cognition as a dated record instead of causality", () => {
    const { preview } = buildRelationOverview(model([relation("person", "认知时点")]));
    expect(preview[0]).toMatchObject({ label: "认知记录", description: "截至本事件的认知状态" });
    expect(preview[0].flow).toMatchObject({ label: "认知时点", direction: "neutral" });
  });

  it("preserves distinct relations to the same counterpart, stable IDs and missing references", () => {
    const first = relation("first", "支持论断", "information_unit", { group: "reasoning" });
    const second = { ...first, id: "second", fieldLabel: "反驳论断" };
    const missing = relation("missing", "来源事件", "information_unit", {
      counterpart: { ...endpoint("missing", "event"), missing: true, selectable: false },
    });
    const result = buildRelationOverview(model([first, second, missing]));
    expect(result.total).toBe(3);
    expect(result.preview.map((item) => item.relation.id)).toEqual(["missing", "first"]);
    expect(result.remaining.map((item) => item.relation.id)).toEqual(["second"]);
    expect(result.preview[0].relation.counterpart.missing).toBe(true);
  });

  it("preserves explicit relationship direction without implying direction for mutual relations", () => {
    const outgoing = relation("a", "有向关系", "entity", { fieldPath: null, verb: "暗中守护" });
    const incoming = relation("b", "有向关系", "entity", {
      fieldPath: null, verb: "暗中守护", subject: endpoint("b", "entity"), object: endpoint("selected", "entity"),
    });
    const mutual = relation("c", "双向关系", "entity", { fieldPath: null, arrow: "⇄", verb: "信任" });
    const overview = buildRelationOverview(model([outgoing, incoming, mutual]));
    const descriptions = overview.preview.map((item) => item.description);
    expect(descriptions).toEqual(["指向对方 · 暗中守护", "来自对方 · 暗中守护", "双方关系 · 信任"]);
    expect(overview.preview.map((item) => item.flow.direction)).toEqual(["outgoing", "incoming", "mutual"]);
  });

  it("handles empty context", () => {
    expect(buildRelationOverview(model([]))).toEqual({ preview: [], remaining: [], total: 0 });
  });
});
