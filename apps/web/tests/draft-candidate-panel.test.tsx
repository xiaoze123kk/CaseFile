import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskAuditDrawer } from "@/features/workflow/brief-review-workspace";
import {
  defaultDraftCandidateTaskRunId,
  DraftCandidatePanel,
  nextDraftCandidateTaskRunId,
  sortDraftCandidates,
} from "@/features/workflow/draft-candidate-panel";
import type {
  DraftCandidateView,
  TaskEventView,
  TaskView,
} from "@/lib/api-client";

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

function task(overrides: Partial<TaskView> = {}): TaskView {
  return {
    task_run_id: 41,
    project_id: 3,
    task_type: "brief_to_draft",
    status: "succeeded",
    stage: "completed",
    provider: "deepseek",
    model_id: "deepseek-v4-pro",
    input_draft_revision: 1,
    input_brief_revision: 2,
    input_source_record_id: null,
    agent_thread_id: null,
    input_message_id: null,
    output_message_id: null,
    input_hash: "a".repeat(64),
    attempt_count: 1,
    usage: {},
    result_snapshot_id: null,
    result: null,
    error_code: null,
    failure: null,
    created_at: "2026-07-30T01:00:00Z",
    updated_at: "2026-07-30T01:01:00Z",
    ...overrides,
  };
}

function taskEvent(sequenceNo = 1): TaskEventView {
  return {
    event_id: sequenceNo,
    task_run_id: 41,
    sequence_no: sequenceNo,
    event_type: "task.succeeded",
    stage: "completed",
    payload: {},
    occurred_at: "2026-07-30T01:01:00Z",
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

  it("keeps ten candidates compact, pins the current draft, and focuses new pending work", () => {
    const current = candidate(5, {
      is_current: true,
      is_adopted: true,
      can_adopt: false,
      title: "当前基准工作稿",
    });
    const history = candidate(4, {
      is_adopted: true,
      can_adopt: false,
      title: "历史采用稿",
    });
    const stale = candidate(3, {
      is_current_brief: false,
      can_adopt: false,
      title: "旧 Brief 候选",
    });
    const pending = Array.from({ length: 7 }, (_, index) =>
      candidate(index + 6),
    );
    const candidates = [stale, ...pending, history, current];
    const ordered = sortDraftCandidates(candidates);
    const onSelect = vi.fn();

    expect(ordered).toHaveLength(10);
    expect(ordered[0]?.task_run_id).toBe(current.task_run_id);
    expect(ordered[1]?.task_run_id).toBe(12);
    expect(defaultDraftCandidateTaskRunId(candidates)).toBe(12);
    expect(
      nextDraftCandidateTaskRunId(
        [...candidates, candidate(13)],
        10,
        new Set(candidates.map((item) => item.task_run_id)),
      ),
    ).toBe(13);
    expect(
      nextDraftCandidateTaskRunId(
        candidates,
        10,
        new Set(candidates.map((item) => item.task_run_id)),
      ),
    ).toBe(10);
    expect(
      nextDraftCandidateTaskRunId(
        candidates,
        null,
        new Set(candidates.map((item) => item.task_run_id)),
      ),
    ).toBeNull();

    render(
      <DraftCandidatePanel
        adopting={false}
        candidates={candidates}
        onOpenWorkbench={vi.fn()}
        onRequestAdopt={vi.fn()}
        onSelect={onSelect}
        selectedTaskRunId={12}
      />,
    );

    const summaries = screen
      .getAllByRole("button")
      .filter((button) => button.hasAttribute("aria-expanded"));
    expect(summaries).toHaveLength(10);
    expect(summaries[0]).toHaveTextContent("当前基准工作稿");
    expect(summaries[1]).toHaveTextContent("午夜回航候选 12");
    expect(
      summaries.filter(
        (button) => button.getAttribute("aria-expanded") === "true",
      ),
    ).toHaveLength(1);

    fireEvent.click(summaries[1]!);
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});

describe("Task audit drawer", () => {
  it("stays compact by default and opens automatically for failures and stream errors", async () => {
    const onReconnect = vi.fn();
    const { rerender } = render(
      <TaskAuditDrawer
        events={[taskEvent()]}
        onReconnect={onReconnect}
        streamError={null}
        task={task()}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: /可恢复审计/,
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    rerender(
      <TaskAuditDrawer
        events={[taskEvent()]}
        onReconnect={onReconnect}
        streamError={null}
        task={task({ status: "failed", stage: "failed" })}
      />,
    );
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-expanded", "true"),
    );

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    rerender(
      <TaskAuditDrawer
        events={[taskEvent()]}
        onReconnect={onReconnect}
        streamError="事件流已断开"
        task={task()}
      />,
    );
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-expanded", "true"),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "按最后序号重连" }),
    );
    expect(onReconnect).toHaveBeenCalledOnce();
  });
});
