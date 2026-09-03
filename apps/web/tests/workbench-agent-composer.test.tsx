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
        surface="desk"
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

  it("renders the compact dock composer without extra controls", () => {
    render(
      <WorkbenchAgentComposer
        busy={false}
        contextChips={[]}
        disabled={false}
        draft=""
        onDraftChange={vi.fn()}
        onSend={vi.fn()}
        surface="dock"
      />,
    );

    expect(
      screen.getByPlaceholderText("写下你的疑问，让卷宗循着线索回答……"),
    ).toBeInTheDocument();
    expect(screen.queryByText("当前上下文")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  });

  it("refocuses the dock composer when its shortcut is activated again", () => {
    const props = {
      busy: false,
      contextChips: [],
      disabled: false,
      draft: "",
      onDraftChange: vi.fn(),
      onSend: vi.fn(),
      surface: "dock" as const,
    };
    const { rerender } = render(<WorkbenchAgentComposer {...props} focusRequest={1} />);
    const input = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });

    input.blur();
    expect(input).not.toHaveFocus();

    rerender(<WorkbenchAgentComposer {...props} focusRequest={2} />);
    expect(input).toHaveFocus();
  });
});
