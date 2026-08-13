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
});
