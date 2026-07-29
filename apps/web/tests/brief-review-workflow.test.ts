import { describe, expect, it } from "vitest";

import {
  eventSummary,
  extractionMatchesBrief,
  normalizeBriefReviewContent,
} from "@/features/workflow/brief-review-workspace";
import {
  mergeTaskEvent,
  selectNewestTask,
} from "@/features/workflow/task-recovery";
import {
  buildAuthorSourceCreateBody,
  polishCandidateMatchesInput,
  polishProposalWasAdopted,
  prepareBriefForSave,
} from "@/features/workflow/intake-workspace";
import type {
  BriefAnchorExtractResult,
  BriefContent,
  BriefPolishResult,
  SourceRecordView,
  TaskEventView,
  TaskView,
} from "@/lib/api-client";

const brief: BriefContent = {
  source_record_ids: [11, 12],
  creative_intent: "  建立一份目标中立的推理卷宗。  ",
  reasoning_proposition: "  谁改变了渡轮的航线？  ",
  resolution_mode: "author_anchored",
  author_answer: "  船载 AI 主动改变了航线。  ",
  author_anchors: [
    {
      anchor_id: "anchor_task_7_01",
      statement: "船载 AI 主动改变了航线。",
    },
  ],
  boundary_text: "  不使用超自然解释。  ",
  creative_constraints: [
    {
      constraint_id: "constraint_task_7_01",
      statement: "不使用超自然解释。",
      strength: "hard",
    },
  ],
};

const inputSource: SourceRecordView = {
  source_record_id: 11,
  source_kind: "human_original",
  content_text: "渡轮每天午夜回到同一座码头。",
  content_hash: "a".repeat(64),
  parent_source_record_id: null,
  generated_by_task_run_id: null,
  created_at: "2026-07-28T08:00:00Z",
};

const polishResult: BriefPolishResult = {
  input_hash: "a".repeat(64),
  polished_text: "渡轮每到午夜，都会重新驶回同一座码头。",
  preserved_intent_summary: "保留循环回航的核心设定。",
  ambiguities: [],
  proposal_source_record: {
    ...inputSource,
    source_record_id: 12,
    source_kind: "agent_polish_proposal",
    parent_source_record_id: 11,
    generated_by_task_run_id: 21,
  },
};

const extractResult: BriefAnchorExtractResult = {
  input_hash: "b".repeat(64),
  author_anchors: [{ statement: "船载 AI 主动改变了航线。" }],
  creative_constraints: [
    {
      statement: "不使用超自然解释。",
      suggested_strength: "hard",
    },
  ],
  warnings: [],
};

function extractionTask(): TaskView {
  return {
    task_run_id: 31,
    project_id: 3,
    task_type: "brief_anchor_extract",
    status: "succeeded",
    stage: "completed",
    provider: "deepseek",
    model_id: "deepseek-chat",
    input_draft_revision: 1,
    input_brief_revision: 4,
    input_source_record_id: null,
    input_hash: extractResult.input_hash,
    attempt_count: 1,
    usage: {},
    result_snapshot_id: null,
    result: extractResult,
    error_code: null,
    failure: null,
    created_at: "2026-07-28T08:00:00Z",
    updated_at: "2026-07-28T08:00:01Z",
  };
}

function taskEvent(sequenceNo: number): TaskEventView {
  return {
    event_id: sequenceNo,
    task_run_id: 31,
    sequence_no: sequenceNo,
    event_type: sequenceNo === 2 ? "task.succeeded" : "task.running",
    stage: sequenceNo === 2 ? "completed" : "extracting",
    payload: {},
    occurred_at: "2026-07-28T08:00:00Z",
  };
}

describe("Brief review workflow", () => {
  it("summarizes repair rounds and sanitized field-level failures", () => {
    const summary = eventSummary({
      ...taskEvent(3),
      event_type: "validation.failed",
      stage: "validating",
      payload: {
        repair_no: 1,
        issue_count: 2,
        issues: [
          {
            code: "missing",
            path: "/events/0/time",
            message: "缺少必填字段",
          },
          {
            code: "schema_invalid",
            path: "/claims/0",
            message: "字段不符合 CaseFile 结构约束",
          },
        ],
      },
    });

    expect(summary).toContain("修复轮次：1");
    expect(summary).toContain("问题：2");
    expect(summary).toContain("/events/0/time 缺少必填字段（另有 1 项）");
  });

  it("preserves confirmed atomics when the author answer and boundary are unchanged", () => {
    const prepared = prepareBriefForSave(
      brief,
      {
        ...brief,
        creative_intent: brief.creative_intent.trim(),
        reasoning_proposition: brief.reasoning_proposition.trim(),
        author_answer: brief.author_answer?.trim() ?? null,
        boundary_text: brief.boundary_text?.trim() ?? null,
      },
      [11, 12],
    );

    expect(prepared.extractionInputChanged).toBe(false);
    expect(prepared.content.author_anchors).toEqual(brief.author_anchors);
    expect(prepared.content.creative_constraints).toEqual(
      brief.creative_constraints,
    );
  });

  it("invalidates old atomics when either extraction input changes", () => {
    const prepared = prepareBriefForSave(
      {
        ...brief,
        author_answer: "船长主动改变了航线。",
      },
      brief,
      [11, 12],
    );

    expect(prepared.extractionInputChanged).toBe(true);
    expect(prepared.content.author_anchors).toEqual([]);
    expect(prepared.content.creative_constraints).toEqual([]);
  });

  it("accepts a polish candidate only for the exact persisted input source and hash", () => {
    const taskInput = {
      input_source_record_id: inputSource.source_record_id,
      input_hash: polishResult.input_hash,
    };

    expect(
      polishCandidateMatchesInput(
        taskInput,
        polishResult,
        inputSource,
        inputSource.content_text,
      ),
    ).toBe(true);
    expect(
      polishCandidateMatchesInput(
        taskInput,
        polishResult,
        inputSource,
        `${inputSource.content_text} 新增内容`,
      ),
    ).toBe(false);
    expect(
      polishCandidateMatchesInput(
        { ...taskInput, input_hash: "c".repeat(64) },
        polishResult,
        inputSource,
        inputSource.content_text,
      ),
    ).toBe(false);
  });

  it("keeps the first author source original and records every later adoption as a revision", () => {
    expect(
      buildAuthorSourceCreateBody("原始创意", null),
    ).toEqual({
      source_kind: "human_original",
      content_text: "原始创意",
    });
    expect(
      buildAuthorSourceCreateBody(
        polishResult.polished_text,
        polishResult.proposal_source_record,
      ),
    ).toEqual({
      source_kind: "human_revision",
      content_text: polishResult.polished_text,
      parent_source_record_id:
        polishResult.proposal_source_record.source_record_id,
    });
  });

  it("does not reopen a polish proposal after a durable human revision adopts it", () => {
    const adoptedRevision: SourceRecordView = {
      ...polishResult.proposal_source_record,
      source_record_id: 13,
      source_kind: "human_revision",
      content_text: polishResult.polished_text,
      parent_source_record_id:
        polishResult.proposal_source_record.source_record_id,
      generated_by_task_run_id: null,
    };

    expect(
      polishProposalWasAdopted(polishResult, [
        inputSource,
        polishResult.proposal_source_record,
        adoptedRevision,
      ]),
    ).toBe(true);
    expect(
      polishProposalWasAdopted(polishResult, [
        inputSource,
        polishResult.proposal_source_record,
      ]),
    ).toBe(false);
  });

  it("normalizes only author-approved atomics and keeps constraint strength explicit", () => {
    const normalized = normalizeBriefReviewContent(
      brief,
      [
        ...brief.author_anchors,
        {
          anchor_id: "anchor_manual_1",
          statement: "   ",
        },
      ],
      [
        {
          constraint_id: "constraint_manual_1",
          statement: "整体语气保持克制。",
          strength: "soft",
        },
      ],
    );

    expect(normalized.creative_intent).toBe(
      "建立一份目标中立的推理卷宗。",
    );
    expect(normalized.author_anchors).toHaveLength(1);
    expect(normalized.creative_constraints).toEqual([
      {
        constraint_id: "constraint_manual_1",
        statement: "整体语气保持克制。",
        strength: "soft",
      },
    ]);
    expect(normalized).not.toHaveProperty("player_goal");
  });

  it("rejects stale extraction candidates and replays task events without duplicates", () => {
    const task = extractionTask();

    expect(extractionMatchesBrief(task, extractResult, 4, false)).toBe(
      true,
    );
    expect(extractionMatchesBrief(task, extractResult, 5, false)).toBe(
      false,
    );
    expect(extractionMatchesBrief(task, extractResult, 4, true)).toBe(
      false,
    );

    const first = mergeTaskEvent([], taskEvent(2));
    const ordered = mergeTaskEvent(first, taskEvent(1));
    const duplicate = mergeTaskEvent(ordered, taskEvent(2));
    expect(duplicate.map((event) => event.sequence_no)).toEqual([1, 2]);
  });

  it("recovers the latest persisted task when the local pointer is missing or stale", () => {
    const latest = extractionTask();
    const stalePointer = {
      ...latest,
      task_run_id: latest.task_run_id - 1,
    };

    expect(selectNewestTask(latest, null)?.task_run_id).toBe(
      latest.task_run_id,
    );
    expect(selectNewestTask(latest, stalePointer)?.task_run_id).toBe(
      latest.task_run_id,
    );
  });
});
