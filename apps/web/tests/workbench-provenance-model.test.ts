import type { CaseFile } from "@casefile/contracts";
import { describe, expect, it } from "vitest";

import restartLoopFixture from "../../../fixtures/casefiles/restart_loop.casefile.json";
import {
  buildContextProvenanceModel,
  findExactSpan,
} from "@/features/analyst-workbench/workbench-provenance-model";
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
        trace_id: "source_records:69",
        source_table: "source_records",
        source_record_id: 69,
        source_kind: "human_original",
        content_text: "你的最初想法：\n林研究员 在实验室记录：系统第七次重启。\n她需要找到根因。",
        content_hash: "a".repeat(64),
        parent_source_record_id: null,
        generated_by_task_run_id: null,
        created_by_user_id: 1,
        created_at: "2026-08-14T12:00:00Z",
      },
      {
        trace_id: "source_records:70",
        source_table: "source_records",
        source_record_id: 70,
        source_kind: "agent_polish_proposal",
        content_text: "润色后：林研究员 在实验室记录：系统第七次重启。 根因仍未确认。",
        content_hash: "b".repeat(64),
        parent_source_record_id: 69,
        generated_by_task_run_id: 49,
        created_by_user_id: 1,
        created_at: "2026-08-14T12:01:00Z",
      },
      {
        trace_id: "source_records:71",
        source_table: "source_records",
        source_record_id: 71,
        source_kind: "human_revision",
        content_text: "修订稿：林研究员 在实验室记录：系统第七次重启。",
        content_hash: "c".repeat(64),
        parent_source_record_id: 70,
        generated_by_task_run_id: null,
        created_by_user_id: 1,
        created_at: "2026-08-14T12:02:00Z",
      },
    ],
    contract_source_refs: [
      { source_fragment_id: "src_restart_log", paths: ["/events/0/source_refs/0"] },
    ],
    audit_entries: [],
  };
}

describe("workbench provenance model", () => {
  it("builds a parent/task derivation chain in author language", () => {
    const model = buildContextProvenanceModel(document, "evt_restart_seven", makeContext());

    expect(model.derivations.map((item) => item.label)).toEqual([
      "你的原稿 ①",
      "Agent 建议 ②",
      "你的修订 ③",
    ]);
    expect(model.derivations[1].originNote).toContain("承接 你的原稿 ①");
    expect(model.derivations[1].originNote).toContain("Agent 任务 #49");
    expect(model.derivations[2].originNote).toContain("承接 Agent 建议 ②");
  });

  it("cites important fields only on exact text spans inside source records", () => {
    const model = buildContextProvenanceModel(document, "evt_restart_seven", makeContext());

    const title = model.citations.find((citation) => citation.fieldLabel === "标题");
    expect(title).toMatchObject({ fieldValue: "系统第七次重启" });
    expect(title?.matches).toHaveLength(3);
    expect(title?.matches[0]).toMatchObject({
      sourceRecordId: 69,
      sourceLabel: "你的原稿 ①",
      kindLabel: "作者原稿",
    });
    expect(title?.matches[0].span).toMatchObject({
      paragraphNo: 2,
      match: "系统第七次重启",
    });

    const entity = buildContextProvenanceModel(document, "ent_researcher", makeContext());
    const name = entity.citations.find((citation) => citation.fieldLabel === "名称");
    expect(name?.fieldValue).toBe("林研究员");
    expect(name?.matches[0].span.paragraphNo).toBe(2);
  });

  it("preserves declared source fragments with contract paths and no guesses", () => {
    const model = buildContextProvenanceModel(document, "evt_restart_seven", makeContext());

    expect(model.fragments).toEqual([
      { fragmentId: "src_restart_log", paths: ["/events/0/source_refs/0"] },
    ]);
    expect(model.totals).toMatchObject({
      citedFields: 1,
      citations: 3,
      fragments: 1,
    });
    expect(model.derivations.every((item) => !item.source.trace_id.includes("guess"))).toBe(true);

    const empty = buildContextProvenanceModel(document, "evt_restart_seven", null);
    expect(empty.citations).toEqual([]);
    expect(empty.derivations).toEqual([]);
    expect(empty.fragments).toEqual([
      { fragmentId: "src_restart_log", paths: [] },
    ]);
  });

  it("normalizes whitespace and only reports spans for verbatim text", () => {
    expect(findExactSpan("第一行\n第二行 系统第七次重启 结束", "系统第七次重启")).toMatchObject({
      paragraphNo: 2,
      match: "系统第七次重启",
    });
    expect(findExactSpan("没有对应正文", "系统第七次重启")).toBeNull();
    expect(findExactSpan("没有对应正文", "短词")).toBeNull();
  });
});
