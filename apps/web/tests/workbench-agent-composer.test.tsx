import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkbenchAgentComposer } from "@/features/analyst-workbench/workbench-agent-composer";

afterEach(cleanup);

describe("WorkbenchAgentComposer", () => {
  it("keeps Chinese IME Enter from submitting, while preserving send and newline shortcuts", () => {
    const onSend = vi.fn();
    render(
      <WorkbenchAgentComposer
        busy={false}
        contextChips={["EVT-012", "时间线"]}
        disabled={false}
        draft="检查时间冲突"
        onDraftChange={vi.fn()}
        onSend={onSend}
        surface="quick"
      />,
    );

    const input = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });
    fireEvent.keyDown(input, { isComposing: true, key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(screen.getByText("EVT-012")).toBeInTheDocument();
  });

  it("uses Ctrl+Shift+Enter to continue from Quick Ask into Agent Desk", () => {
    const onContinueInDesk = vi.fn();
    const onSend = vi.fn();
    render(
      <WorkbenchAgentComposer
        busy={false}
        contextChips={[]}
        disabled={false}
        draft=""
        onContinueInDesk={onContinueInDesk}
        onDraftChange={vi.fn()}
        onSend={onSend}
        surface="quick"
      />,
    );

    fireEvent.keyDown(
      screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
      { ctrlKey: true, key: "Enter", shiftKey: true },
    );

    expect(onContinueInDesk).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText("未选择对象")).toBeInTheDocument();
  });

  it("refocuses Quick Ask when the trigger is activated again", () => {
    const props = {
      busy: false,
      contextChips: [],
      disabled: false,
      draft: "",
      onDraftChange: vi.fn(),
      onSend: vi.fn(),
      surface: "quick" as const,
    };
    const { rerender } = render(<WorkbenchAgentComposer {...props} focusRequest={1} />);
    const input = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });

    input.blur();
    expect(input).not.toHaveFocus();

    rerender(<WorkbenchAgentComposer {...props} focusRequest={2} />);
    expect(input).toHaveFocus();
  });
});
