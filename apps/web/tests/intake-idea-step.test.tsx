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
  introduced_details: ["新增厨房无人注意这一环境细节。"],
  polish_mode: "narrative_enhance",
  proposal_source_record: {
    ...originalSource,
    source_record_id: 12,
    source_kind: "agent_polish_proposal",
    content_text: "妹妹趁厨房无人注意，悄悄拿走了一块蛋糕。",
    generated_by_task_run_id: 88,
  },
};

function renderComparison(result = polishResult) {
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
      onPolishModeChange={vi.fn()}
      onSourceChange={vi.fn()}
      onStartNewCase={vi.fn()}
      polishCandidateStale={false}
      polishDraft={result.polished_text}
      polishMode={result.polish_mode ?? "rewrite"}
      polishResult={result}
      polishReviewOpen
      polishTask={
        {
          task_run_id: 88,
          provider: "deepseek",
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
    expect(screen.getByLabelText("Agent 修改差异")).toBeInTheDocument();
    expect(screen.getByText(/DeepSeek 已审阅 · 叙事增强 · 修改/u)).toBeInTheDocument();
    expect(screen.getByText("新增细节审阅")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("编辑 Agent 润色工作稿"), {
      target: { value: "作者继续调整后的润色稿。" },
    });
    expect(onPolishDraftChange).toHaveBeenCalledWith(
      "作者继续调整后的润色稿。",
    );
  });

  it("explains when the Agent reviewed the source without changing it", () => {
    const unchanged: BriefPolishResult = {
      ...polishResult,
      polished_text: originalSource.content_text,
      introduced_details: [],
      polish_mode: "proofread",
      proposal_source_record: {
        ...polishResult.proposal_source_record,
        content_text: originalSource.content_text,
      },
    };

    renderComparison(unchanged);

    expect(
      screen.getByText("Agent 已完成审阅：原文表达清晰，本次未建议文字调整。"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 修改差异")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "原文无需替换" })).toBeDisabled();
  });

  it("offers three auditable polish strengths before starting", () => {
    const onPolishModeChange = vi.fn();
    render(
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
        onPolishDraftChange={vi.fn()}
        onPolishModeChange={onPolishModeChange}
        onSourceChange={vi.fn()}
        onStartNewCase={vi.fn()}
        polishCandidateStale={false}
        polishDraft=""
        polishMode="rewrite"
        polishResult={null}
        polishReviewOpen={false}
        polishTask={null}
        providerReady
        savedSource={originalSource}
        sourceText={originalSource.content_text}
      />,
    );

    expect(screen.getByRole("radio", { name: /轻度校对/u })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /表达优化/u })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /叙事增强/u }));
    expect(onPolishModeChange).toHaveBeenCalledWith("narrative_enhance");
  });
});
