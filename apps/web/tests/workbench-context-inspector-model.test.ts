import type { CaseFile } from "@casefile/contracts";
import { describe, expect, it } from "vitest";

import restartLoopFixture from "../../../fixtures/casefiles/restart_loop.casefile.json";
import {
  buildContextInspectorModel,
  sourceExcerpt,
  sourceKindLabel,
} from "@/features/analyst-workbench/workbench-context-inspector-model";
import type { WorkbenchContextView } from "@/lib/api-client";

const document = restartLoopFixture as unknown as CaseFile;

function makeContext(): WorkbenchContextView {
  return {
    project_id: 1,
    draft_id: 2,
    draft_revision: 7,
    validation: {
      status: "passed",
      validator: "casefile.contracts.validate_casefile",
      schema_version: "2.0",
      issue_count: 0,
      issues: [],
      reason: null,
    },
    sources: [
      {
        trace_id: "source_records:12",
        source_table: "source_records",
        source_record_id: 12,
        source_kind: "human_original",
        content_text: "  作者写下的最初想法，\n需要保留为这一段的来源依据。  ",
        content_hash: "a".repeat(64),
        parent_source_record_id: null,
        generated_by_task_run_id: null,
        created_by_user_id: 1,
        created_at: "2026-08-07T12:00:00Z",
      },
    ],
    contract_source_refs: [],
    audit_entries: [
      {
        entry_id: "draft_operations:31",
        source_table: "draft_operations",
        record_id: 31,
        occurred_at: "2026-08-07T12:05:00Z",
        actor: { kind: "user", user_id: 1, ref: null },
        action: "agent_adopt_brief_candidate",
        target_type: "draft",
        target_id: 9,
        trace_id: null,
        details: {
          object_id: null,
          field_path: "",
          base_revision: 6,
          result_revision: 7,
        },
      },
    ],
  };
}

describe("workbench context inspector model", () => {
  it("localizes source kinds and normalizes excerpts without exposing hashes", () => {
    expect(sourceKindLabel("human_original")).toBe("作者原稿");
    expect(sourceKindLabel("human_revision")).toBe("作者修订");
    expect(sourceKindLabel("agent_polish_proposal")).toBe("Agent 建议");
    expect(sourceExcerpt("  第一行\n第二行  ")).toBe("第一行 第二行");
    expect(sourceExcerpt("")).toBe("来源正文待补充。");
    expect(sourceExcerpt("长".repeat(200))).toHaveLength(141);
  });

  it("builds object identity and display-ready provenance for a real CaseFile object", () => {
    const model = buildContextInspectorModel(
      document,
      "ent_researcher",
      makeContext(),
    );

    expect(model.identity).toMatchObject({
      id: "ent_researcher",
      kindLabel: "实体",
      subtypeLabel: "人物",
      confidence: 1,
      revision: 1,
    });
    expect(model.identity?.title).toBeTruthy();
    expect(model.counts).toMatchObject({
      sources: 1,
      changes: 1,
    });
    expect(model.counts.associations).toBeGreaterThan(0);
    expect(model.counts.relations).toBeGreaterThan(0);
    expect(model.counts.incoming).toBeGreaterThan(0);

    const events = model.relations.groups.find((group) => group.id === "events");
    expect(events?.relations.some((relation) =>
      relation.verb === "发现" &&
      relation.subject.id === "ent_researcher" &&
      relation.object.id === "evt_restart_seven",
    )).toBe(true);

    const information = model.relations.groups.find(
      (group) => group.id === "information",
    );
    expect(information?.relations.some((relation) =>
      relation.verb === "知道" &&
      relation.subject.id === "ent_researcher" &&
      relation.object.id === "info_restart_log",
    )).toBe(true);

    const direct = model.relations.groups.find((group) => group.id === "direct");
    expect(direct?.relations.some((relation) =>
      relation.verb === "研究员维护备用系统" &&
      relation.subject.id === "ent_researcher" &&
      relation.object.id === "ent_backup_system",
    )).toBe(true);

    expect(model.relations.incoming).toHaveLength(3);
    expect(model.relations.incoming.some((reference) =>
      reference.sourceObjectId === "evt_restart_seven" &&
      reference.fieldLabel === "观察者" &&
      reference.fieldPath.includes("observed_by_refs"),
    )).toBe(true);
    expect(model.relations.incoming.some((reference) =>
      reference.sourceObjectId === "rel_researcher_controls_backup" &&
      reference.fieldLabel === "关系起点",
    )).toBe(true);

    expect(model.sourceEvidence).toHaveLength(1);
    expect(model.sourceEvidence[0]).toMatchObject({
      kind: "human_original",
      kindLabel: "作者原稿",
      excerpt: "作者写下的最初想法， 需要保留为这一段的来源依据。",
    });
    expect(JSON.stringify(model.sourceEvidence[0])).not.toContain("content_hash");

    expect(model.recentChanges).toHaveLength(1);
    expect(model.recentChanges[0]).toMatchObject({
      actorLabel: "你 · #1",
      actionLabel: "采用 Draft 候选",
      detail: "Draft · / · R6 → R7",
    });
  });

  it("returns an empty-but-shaped model when nothing is selected", () => {
    const model = buildContextInspectorModel(document, null, null);

    expect(model.identity).toBeNull();
    expect(model.sourceEvidence).toEqual([]);
    expect(model.recentChanges).toEqual([]);
    expect(model.relations).toEqual({
      groups: [],
      incoming: [],
      totals: {
        all: 0,
        direct: 0,
        events: 0,
        information: 0,
        reasoning: 0,
        incoming: 0,
      },
    });
    expect(model.counts).toEqual({
      associations: 0,
      relations: 0,
      incoming: 0,
      sources: 0,
      changes: 0,
    });
  });
});
