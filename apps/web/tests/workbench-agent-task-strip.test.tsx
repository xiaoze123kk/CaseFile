import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PublicAgentRun } from "@casefile/contracts";

import { WorkbenchAgentTaskStrip } from "@/features/analyst-workbench/workbench-agent-task-strip";

function run(overrides: Partial<PublicAgentRun> = {}): PublicAgentRun {
  return {
    run_id: 10,
    status: "running",
    activity: "reading",
    cancellable: true,
    failure: null,
    ...overrides,
  };
}

describe("WorkbenchAgentTaskStrip", () => {
  it("uses public activity and context state without token or provider metadata", () => {
    const onCancel = vi.fn();
    render(
      <WorkbenchAgentTaskStrip
        contextState="near_limit"
        onCancel={onCancel}
        run={run()}
      />,
    );

    expect(screen.getByText("卷宗统筹 · 正在阅读卷宗")).toBeInTheDocument();
    expect(screen.getByText("对话内容较长，正在控制上下文")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止回复" }));
    expect(onCancel).toHaveBeenCalledOnce();
    expect(screen.queryByText(/token|provider|prompt/i)).not.toBeInTheDocument();
  });

  it("shows public verification summaries and terminal failure messages", () => {
    const { rerender } = render(
      <WorkbenchAgentTaskStrip
        run={run({ activity: "checking" })}
        verificationProgress={{ status: "blocked", summary: "发现需要作者审阅的影响" }}
      />,
    );
    expect(screen.getByText("卷宗统筹 · 发现需要作者审阅的影响")).toBeInTheDocument();

    rerender(
      <WorkbenchAgentTaskStrip
        run={run({
          status: "failed",
          activity: null,
          cancellable: false,
          failure: {
            category: "request_failed",
            message: "这次回复未能通过公开输出检查。",
            retryable: true,
          },
        })}
      />,
    );
    expect(screen.getByText("回复未完成")).toBeInTheDocument();
    expect(screen.getByText("回复摘要")).toBeInTheDocument();
  });
});
