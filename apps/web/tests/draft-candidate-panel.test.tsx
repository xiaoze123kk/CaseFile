import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DraftCandidatePanel } from "@/features/workflow/draft-candidate-panel";
import type { DraftCandidateView } from "@/lib/api-client";

afterEach(cleanup);

function candidate(
  taskRunId: number,
  overrides: Partial<DraftCandidateView> = {},
): DraftCandidateView {
  return {
    task_run_id: taskRunId,
    brief_version_no: 2,
    is_current_brief: true,
    is_current: false,
    is_adopted: false,
    can_adopt: true,
    provider: "deepseek",
    model_id: "deepseek-v4-pro",
    title: `午夜回航候选 ${taskRunId}`,
    content_hash: String(taskRunId).padStart(64, "0"),
    object_counts: {
      entities: 4,
      events: 7,
      information_units: 12,
      reasoning_paths: 2,
    },
    reasoning_questions: ["是谁修改了航行记录？"],
    constraint_statements: ["因果答案必须唯一。"],
    attempt_count: 1,
    created_at: "2026-07-30T01:00:00Z",
    completed_at: "2026-07-30T01:01:00Z",
    ...overrides,
  };
}

describe("Draft candidate archive", () => {
  it("compares candidates and requests explicit adoption without mutating the current draft", () => {
    const onSelect = vi.fn();
    const onRequestAdopt = vi.fn();
    const pending = candidate(12);
    const current = candidate(9, {
      is_current: true,
      is_adopted: true,
      can_adopt: false,
      title: "已采用工作稿",
    });

    render(
      <DraftCandidatePanel
        adopting={false}
        candidates={[pending, current]}
        onOpenWorkbench={vi.fn()}
        onRequestAdopt={onRequestAdopt}
        onSelect={onSelect}
        selectedTaskRunId={pending.task_run_id}
      />,
    );

    expect(screen.getByText("02 份")).toBeInTheDocument();
    expect(screen.getByText("是谁修改了航行记录？")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "采用为当前工作稿 →" }),
    );
    expect(onRequestAdopt).toHaveBeenCalledWith(pending);
    fireEvent.click(screen.getByRole("button", { name: /已采用工作稿/ }));
    expect(onSelect).toHaveBeenCalledWith(current.task_run_id);
  });

  it("marks candidates from an older Brief as non-adoptable", () => {
    render(
      <DraftCandidatePanel
        adopting={false}
        candidates={[
          candidate(7, {
            is_current_brief: false,
            can_adopt: false,
          }),
        ]}
        onOpenWorkbench={vi.fn()}
        onRequestAdopt={vi.fn()}
        onSelect={vi.fn()}
        selectedTaskRunId={7}
      />,
    );

    expect(screen.getByText("Brief 已更新，不可采用")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "采用为当前工作稿 →" }),
    ).not.toBeInTheDocument();
  });
});
