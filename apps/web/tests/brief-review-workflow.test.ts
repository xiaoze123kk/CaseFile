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
  discardCandidateTarget,
  missingCandidateHardFields,
  seedManualCandidate,
} from "@/features/workflow/intake-model";
import type {
  BriefAnchorExtractResult,
  BriefIntakeCandidateView,
  BriefContent,
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

function intakeCandidate(
  candidateId: number,
  overrides: Partial<BriefIntakeCandidateView> = {},
): BriefIntakeCandidateView {
  const content = seedManualCandidate("渡轮每到午夜都会驶回同一座码头。");
  content.reasoning_goal = "查明是谁改变了渡轮的航线。";
  content.field_sources.reasoning_goal = "user_confirmed";
  return {
    candidate_id: candidateId,
    parent_candidate_id: null,
    generated_by_task_run_id: null,
    origin: "manual_edit",
    basis_input_hash: "a".repeat(64),
    content_hash: String(candidateId).padStart(64, "0"),
    content,
    is_current: false,
    is_adopted: false,
    is_saved: false,
    is_stale: false,
    can_activate: true,
    saved_at: null,
    created_at: "2026-08-04T08:00:00Z",
    ...overrides,
  };
}

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
    input_brief_intake_id: null,
    input_brief_intake_revision: null,
    base_brief_intake_candidate_id: null,
    agent_thread_id: null,
    input_message_id: null,
    output_message_id: null,
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

  it("seeds a manual candidate from the first non-empty source line", () => {
    const seeded = seedManualCandidate(
      "\n  渡轮每到午夜都会驶回同一座码头。  \n第二段补充背景。",
    );

    expect(seeded.concept).toBe("渡轮每到午夜都会驶回同一座码头。");
    expect(seeded.field_sources.concept).toBe("user_original");
    expect(seeded.core_selling_points).toEqual([]);
    expect(missingCandidateHardFields(seeded)).toEqual(["推理目标"]);
  });

  it("carries explicitly deferred optional questions into a manual candidate", () => {
    const seeded = seedManualCandidate("午夜渡轮不断回航。", [
      {
        question_key: "question_timeframe",
        ordinal: 1,
        prompt: "故事发生在哪个时代？",
        impact: "决定调查工具与通讯限制。",
        required: false,
        suggestions: [],
        answer_status: "pending",
        answer_text: null,
        answer_source: "unresolved",
      },
      {
        question_key: "question_stake",
        ordinal: 2,
        prompt: "主角为何追查？",
        impact: "决定人物动机。",
        required: true,
        suggestions: [],
        answer_status: "user_answered",
        answer_text: "为了寻找失踪的亲人。",
        answer_source: "user_confirmed",
      },
    ]);

    expect(seeded.pending_decisions).toEqual([
      {
        decision_key: "decision_timeframe",
        prompt: "故事发生在哪个时代？",
        impact: "决定调查工具与通讯限制。",
        source: "unresolved",
      },
    ]);
  });

  it("blocks only missing concept, reasoning goal, and an anchored author answer", () => {
    const seeded = seedManualCandidate("");

    expect(missingCandidateHardFields(seeded)).toEqual([
      "一句话概念",
      "推理目标",
    ]);

    seeded.concept = "午夜渡轮不断回航。";
    seeded.reasoning_goal = "查明谁修改了航线。";
    seeded.resolution_mode = "author_anchored";
    expect(missingCandidateHardFields(seeded)).toEqual(["作者底牌"]);

    seeded.author_answer = "船载 AI 主动改写了航行计划。";
    expect(missingCandidateHardFields(seeded)).toEqual([]);
  });

  it("discards edits to the saved candidate first and otherwise to the parent", () => {
    const parent = intakeCandidate(1);
    const saved = intakeCandidate(2, { is_saved: true });
    const current = intakeCandidate(3, {
      parent_candidate_id: parent.candidate_id,
      is_current: true,
    });

    expect(discardCandidateTarget([current, saved, parent], current)).toBe(
      saved,
    );
    expect(
      discardCandidateTarget(
        [current, { ...saved, is_stale: true }, parent],
        current,
      ),
    ).toBe(parent);
    expect(discardCandidateTarget([current], current)).toBeNull();
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
