import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TaskView } from "@/lib/api-client";
import { WorkbenchAgentTaskStrip } from "@/features/analyst-workbench/workbench-agent-task-strip";

function task(status: TaskView["status"], stage: string): TaskView {
  return {
    task_run_id: 10,
    project_id: 1,
    task_type: "casefile_chat",
    status,
    stage,
    provider: "deepseek",
    model_id: "chat",
    input_draft_revision: 3,
    input_brief_revision: null,
    input_source_record_id: null,
    input_brief_intake_id: null,
    input_brief_intake_revision: null,
    base_brief_intake_candidate_id: null,
    agent_thread_id: 1,
    input_message_id: 2,
    output_message_id: 3,
    input_hash: "hash",
    candidate_strategy: null,
    attempt_count: 1,
    usage: {},
    result_snapshot_id: null,
    result: null,
    error_code: null,
    failure: null,
    component_steps: [],
    created_at: null,
    updated_at: null,
  };
}

describe("WorkbenchAgentTaskStrip", () => {
  it("reports the actual running stage and exposes cancellation", () => {
    render(
      <WorkbenchAgentTaskStrip
        contextOccupancy={{ usedTokens: 120, budgetTokens: 1000 }}
        onCancel={vi.fn()}
        task={task("running", "reading_case")}
      />,
    );

    expect(screen.getByText("Agent 正在回复 · reading_case")).toBeInTheDocument();
    expect(screen.getByText("上下文 120/1000 tokens")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    expect(screen.queryByText(/预计|剩余约/)).not.toBeInTheDocument();
  });

  it("uses a real terminal status and a collapsible completion summary", () => {
    render(<WorkbenchAgentTaskStrip task={task("succeeded", "queued")} />);

    expect(screen.getByText("任务已完成")).toBeInTheDocument();
    expect(screen.getByText("执行摘要")).toBeInTheDocument();
  });

});
