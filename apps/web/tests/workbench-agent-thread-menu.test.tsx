import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentThreadView } from "@/lib/api-client";
import { WorkbenchAgentThreadMenu } from "@/features/analyst-workbench/workbench-agent-thread-menu";

afterEach(cleanup);

function thread(overrides: Partial<AgentThreadView> = {}): AgentThreadView {
  return {
    thread_id: 1,
    title: "时间线冲突排查",
    title_source: "auto",
    is_pinned: false,
    status: "active",
    last_message_at: "2026-08-20T08:00:00Z",
    created_at: "2026-08-20T08:00:00Z",
    updated_at: "2026-08-20T08:00:00Z",
    ...overrides,
  };
}

describe("WorkbenchAgentThreadMenu", () => {
  it("clamps the floating panel at the viewport edge and closes after creating a thread", async () => {
    const first = thread();
    const onCreate = vi.fn().mockResolvedValue(first);
    render(<WorkbenchAgentThreadMenu
      threads={[first]} selectedThreadId={first.thread_id}
      onCreate={onCreate} onSelect={vi.fn()}
      onRename={vi.fn().mockResolvedValue(undefined)}
      onSearch={vi.fn().mockResolvedValue(undefined)}
      onSetPinned={vi.fn().mockResolvedValue(undefined)}
      onSetArchived={vi.fn().mockResolvedValue(undefined)}
    />);
    const trigger = screen.getByRole("button", { name: /时间线冲突排查/ });
    vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
      x: window.innerWidth - 200, y: 60, left: window.innerWidth - 200,
      right: window.innerWidth, top: 60, bottom: 100, width: 200, height: 40,
      toJSON: () => ({}),
    });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog")).toHaveStyle({ left: `${window.innerWidth - 400}px`, top: "108px" });
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(onCreate).toHaveBeenCalledOnce();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("supports combobox keyboard selection and restores focus after Escape", async () => {
    const first = thread();
    const second = thread({ thread_id: 2, title: "证据链复核" });
    const onSelect = vi.fn();

    const { container } = render(
      <WorkbenchAgentThreadMenu
        onCreate={vi.fn().mockResolvedValue(second)}
        onRename={vi.fn().mockResolvedValue(undefined)}
        onSearch={vi.fn().mockResolvedValue(undefined)}
        onSelect={onSelect}
        onSetArchived={vi.fn().mockResolvedValue(undefined)}
        onSetPinned={vi.fn().mockResolvedValue(undefined)}
        selectedThreadId={first.thread_id}
        threads={[first, second]}
      />,
    );

    const trigger = screen.getByRole("button", { name: /时间线冲突排查/ });
    fireEvent.click(trigger);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(screen.getByRole("dialog")).toHaveAttribute("id", trigger.getAttribute("aria-controls"));
    const search = screen.getByPlaceholderText("搜索对话…");
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });

    expect(onSelect).toHaveBeenCalledWith(second);
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByPlaceholderText("搜索对话…"), {
      key: "Escape",
    });
    await waitFor(() => expect(trigger).toHaveFocus());
    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "关闭对话菜单" }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("exposes thread actions and archived filtering through the existing callbacks", async () => {
    const first = thread({ is_pinned: true });
    const archived = thread({ thread_id: 3, title: "旧调查", status: "archived", is_pinned: true });
    const onRename = vi.fn().mockResolvedValue(undefined);
    const onSetPinned = vi.fn().mockResolvedValue(undefined);
    const onSetArchived = vi.fn().mockResolvedValue(undefined);
    const onSearch = vi.fn().mockResolvedValue(undefined);

    render(
      <WorkbenchAgentThreadMenu
        onCreate={vi.fn().mockResolvedValue(first)}
        onRename={onRename}
        onSearch={onSearch}
        onSelect={vi.fn()}
        onSetArchived={onSetArchived}
        onSetPinned={onSetPinned}
        selectedThreadId={first.thread_id}
        threads={[first, archived]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /时间线冲突排查/ }));
    expect(screen.getByRole("group", { name: "置顶" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("显示已归档"));
    expect((await screen.findAllByText("旧调查")).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("option")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByRole("textbox", { name: "对话标题" }), {
      target: { value: "时间线新标题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(onRename).toHaveBeenCalledWith(first, "时间线新标题"),
    );

    fireEvent.click(screen.getByRole("button", { name: "取消置顶" }));
    await waitFor(() => expect(onSetPinned).toHaveBeenCalledWith(first, false));
    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() => expect(onSetArchived).toHaveBeenCalledWith(first, true));
    expect(onSearch).toHaveBeenCalled();
  });

  it("keeps Enter navigation inside the search combobox", () => {
    const first = thread();

    render(
      <WorkbenchAgentThreadMenu
        onCreate={vi.fn().mockResolvedValue(first)}
        onRename={vi.fn().mockResolvedValue(undefined)}
        onSearch={vi.fn().mockResolvedValue(undefined)}
        onSelect={vi.fn()}
        onSetArchived={vi.fn().mockResolvedValue(undefined)}
        onSetPinned={vi.fn().mockResolvedValue(undefined)}
        selectedThreadId={first.thread_id}
        threads={[first]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /时间线冲突排查/ }));
    fireEvent.keyDown(screen.getByRole("button", { name: "重命名" }), {
      key: "Enter",
    });

    expect(screen.getByPlaceholderText("搜索对话…")).toBeInTheDocument();
  });
});
