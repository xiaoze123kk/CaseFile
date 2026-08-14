import { describe, expect, it } from "vitest";

import type {
  ReasoningPath,
  WorkbenchReasoningGroup,
} from "@/features/analyst-workbench/analyst-fixture";
import { buildReasoningCanvas } from "@/features/analyst-workbench/workbench-reasoning-graph";
import type { WorkbenchConclusion } from "@/features/analyst-workbench/workbench-real-data-types";

const sharedHypothesisPaths: ReasoningPath[] = [
  {
    id: "path_a",
    question: "谁改写了记录？",
    evidenceIds: ["info_a"],
    steps: [{ id: "shared_step", verb: "推断", claim: "记录被改写", evidenceIds: ["info_a"] }],
    conclusion: "值班员改写了记录",
    outcome: "supported",
    hypothesisId: "hyp_operator",
  },
  {
    id: "path_b",
    question: "谁改写了记录？",
    evidenceIds: ["info_b"],
    steps: [{ id: "shared_step", verb: "核对", claim: "值班时段吻合", evidenceIds: ["info_b"] }],
    conclusion: "值班员改写了记录",
    outcome: "supported",
    hypothesisId: "hyp_operator",
  },
];

const answerConclusion: WorkbenchConclusion = {
  resolutionSpecId: "res_record",
  question: "谁改写了记录？",
  outcome: "answer",
  reviewStatus: "proposed",
  summary: "值班员改写了记录",
  values: [],
  selectedHypothesisIds: ["hyp_operator"],
  supportingReasoningPathIds: ["path_a", "path_b"],
  relatedEventIds: ["evt_record"],
  rationale: "两条独立路径指向同一解释。",
  unresolvedGaps: [],
};

describe("reasoning conclusion canvas", () => {
  it("keeps shared hypotheses and conclusions unique without merging same-named steps", () => {
    const paths = sharedHypothesisPaths.map((path) => ({
      ...path,
      resolutionSpecId: "res_record",
      targetLabel: "值班员改写记录",
    }));
    const scene = buildReasoningCanvas(paths, [answerConclusion]);

    expect(scene.nodes.filter((node) => node.kind === "hypothesis")).toHaveLength(1);
    expect(scene.nodes.filter((node) => node.kind === "conclusion")).toHaveLength(1);
    expect(scene.nodes.filter((node) => node.kind === "reason")).toHaveLength(2);
    expect(scene.edges.filter((edge) => edge.kind === "resolution")).toHaveLength(1);
    expect(scene.nodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: "hypothesis", caption: "进入当前结论" }),
        expect.objectContaining({ kind: "conclusion", label: "值班员改写了记录" }),
      ]),
    );
  });

  it("renders an explicit unresolved endpoint even when no path exists", () => {
    const groups: WorkbenchReasoningGroup[] = [{
      resolutionSpecId: "res_empty",
      question: "失踪者去了哪里？",
      hypotheses: [],
      information: [],
      assessments: [],
    }];

    const scene = buildReasoningCanvas([], [], groups);

    expect(scene.nodes).toEqual([
      expect.objectContaining({
        id: "resolution-conclusion:res_empty",
        kind: "conclusion",
        caption: "尚未形成结论",
        label: "尚未形成结论",
        objectId: "res_empty",
      }),
    ]);
    expect(scene.edges).toEqual([]);
  });

  it("derives the process graph from reasoning groups when no path exists", () => {
    const groups: WorkbenchReasoningGroup[] = [{
      resolutionSpecId: "res_guard",
      question: "谁改写了记录？",
      hypotheses: [
        { id: "hyp_guard", title: "值班员", outcome: "supported" },
        { id: "hyp_other", title: "外部人员", outcome: "eliminated" },
      ],
      information: [{ id: "info_gate", title: "门禁记录", reliability: "confirmed" }],
      assessments: [
        {
          hypothesisId: "hyp_guard",
          informationId: "info_gate",
          effect: "supports",
          strength: "strong",
          rationale: "时段吻合。",
        },
        {
          hypothesisId: "hyp_other",
          informationId: "info_gate",
          effect: "contradicts",
          strength: "moderate",
          rationale: "权限不符。",
        },
      ],
      conclusion: {
        resolutionSpecId: "res_guard",
        question: "谁改写了记录？",
        outcome: "answer",
        reviewStatus: "proposed",
        summary: "值班员改写了记录",
        values: [],
        selectedHypothesisIds: ["hyp_guard"],
        supportingReasoningPathIds: [],
        relatedEventIds: [],
        rationale: "",
        unresolvedGaps: [],
      },
    }];

    const scene = buildReasoningCanvas([], [], groups);

    expect(scene.nodes.map((node) => node.kind).sort()).toEqual([
      "conclusion",
      "evidence",
      "hypothesis",
      "hypothesis",
    ]);
    expect(scene.nodes.find((node) => node.id === "info_gate")).toMatchObject({
      kind: "evidence",
      caption: expect.stringContaining("信息"),
      objectId: "info_gate",
    });
    expect(scene.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        kind: "supports",
        source: "info_gate",
        target: "hypothesis-hyp_guard",
      }),
      expect.objectContaining({
        kind: "contradicts",
        source: "info_gate",
        target: "hypothesis-hyp_other",
      }),
      expect.objectContaining({
        kind: "resolution",
        source: "hypothesis-hyp_guard",
        target: "resolution-conclusion:res_guard",
      }),
    ]));
    expect(
      scene.edges.filter((edge) => edge.source === "hypothesis-hyp_other"),
    ).toEqual([]);
  });

  it("derives an argumentation sketch from the relationship graph subset", () => {
    const scene = buildReasoningCanvas([], [], [], {
      nodes: [
        { objectId: "hyp_x", kind: "hypothesis", label: "假设X", directoryObjectId: "hyp_x" },
        { objectId: "info_y", kind: "information_unit", label: "信息Y", directoryObjectId: "info_y" },
        { objectId: "claim_z", kind: "claim", label: "主张Z", directoryObjectId: "claim_z" },
        {
          objectId: "resolution-conclusion:res_c",
          kind: "resolution_spec",
          label: "结论C",
          directoryObjectId: "res_c",
        },
      ],
      edges: [
        { id: "e1", from: "info_y", to: "claim_z", kind: "information_support", label: "支持" },
        { id: "e2", from: "claim_z", to: "hyp_x", kind: "hypothesis_requirement", label: "必要依据" },
        {
          id: "e3",
          from: "hyp_x",
          to: "resolution-conclusion:res_c",
          kind: "hypothesis_conclusion",
          label: "进入当前结论",
        },
      ],
    });

    expect(scene.nodes).toHaveLength(4);
    expect(scene.nodes.find((node) => node.id === "hyp_x")).toMatchObject({
      kind: "hypothesis",
      objectId: "hyp_x",
    });
    expect(scene.nodes.find((node) => node.id === "info_y")).toMatchObject({
      kind: "evidence",
      objectId: "info_y",
    });
    expect(scene.nodes.find((node) => node.id === "claim_z")).toMatchObject({
      kind: "reason",
      objectId: "claim_z",
    });
    expect(
      scene.nodes.find((node) => node.id === "resolution-conclusion:res_c"),
    ).toMatchObject({ kind: "conclusion", objectId: "res_c" });
    expect(scene.edges).toHaveLength(3);
    expect(scene.edges.find((edge) => edge.id === "e1")).toMatchObject({
      kind: "supports",
    });
    expect(scene.edges.find((edge) => edge.id === "e2")).toMatchObject({
      kind: "evidence",
    });
    expect(scene.edges.find((edge) => edge.id === "e3")).toMatchObject({
      kind: "resolution",
    });
  });

  it("returns an empty scene when no reasoning data exists anywhere", () => {
    expect(buildReasoningCanvas([], [], [])).toEqual({ nodes: [], edges: [] });
  });
});
