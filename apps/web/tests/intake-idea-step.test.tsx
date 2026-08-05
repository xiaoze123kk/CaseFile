import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntakeIdeaStep } from "@/features/workflow/intake-idea-step";
import type {
  BriefPolishResult,
  SourceRecordView,
  TaskView,
} from "@/lib/api-client";

afterEach(cleanup);

const originalSource: SourceRecordView = {
  source_record_id: 11,
  source_kind: "human_original",
  content_text: "妹妹偷了一个蛋糕吃。",
  content_hash: "a".repeat(64),
  parent_source_record_id: null,
  generated_by_task_run_id: null,
  created_at: "2026-08-05T00:00:00Z",
};

const polishResult: BriefPolishResult = {
  input_hash: "b".repeat(64),
  polished_text: "妹妹趁厨房无人注意，悄悄拿走了一块蛋糕。",
  preserved_intent_summary: "保留妹妹偷吃蛋糕这一核心情节。",
  ambiguities: ["蛋糕属于谁仍未说明。"],
  proposal_source_record: {
    ...originalSource,
    source_record_id: 12,
    source_kind: "agent_polish_proposal",
    content_text: "妹妹趁厨房无人注意，悄悄拿走了一块蛋糕。",
    generated_by_task_run_id: 88,
  },
};

function renderComparison() {
  const onPolishDraftChange = vi.fn();
  const view = render(
    <IntakeIdeaStep
      busy={false}
      closed={false}
      error={null}
      onAdoptPolish={vi.fn()}
      onClosePolish={vi.fn()}
      onContinue={vi.fn()}
      onOpenBrief={vi.fn()}
      onOpenPolish={vi.fn()}
      onOpenSettings={vi.fn()}
      onPolish={vi.fn()}
      onPolishDraftChange={onPolishDraftChange}
      onSourceChange={vi.fn()}
      onStartNewCase={vi.fn()}
      polishCandidateStale={false}
      polishDraft={polishResult.polished_text}
      polishResult={polishResult}
      polishReviewOpen
      polishTask={
        {
          task_run_id: 88,
          status: "succeeded",
        } as TaskView
      }
      providerReady
      savedSource={originalSource}
      sourceText={originalSource.content_text}
    />,
  );
  return { ...view, onPolishDraftChange };
}

describe("Intake Agent polish comparison", () => {
  it("places the read-only original and editable polish draft side by side", () => {
    const { container, onPolishDraftChange } = renderComparison();

    expect(container.querySelector('[data-comparing="true"]')).not.toBeNull();
    expect(screen.getByLabelText("Agent 润色左右对照")).toBeInTheDocument();
    expect(screen.getByLabelText("当前作者原稿")).toHaveValue(
      originalSource.content_text,
    );
    expect(screen.getByLabelText("当前作者原稿")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("编辑 Agent 润色工作稿")).toHaveValue(
      polishResult.polished_text,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("编辑 Agent 润色工作稿"), {
      target: { value: "作者继续调整后的润色稿。" },
    });
    expect(onPolishDraftChange).toHaveBeenCalledWith(
      "作者继续调整后的润色稿。",
    );
  });
});
