import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkbenchSidebar } from "@/features/analyst-workbench/workbench-sidebar";

afterEach(cleanup);

function props() {
  return {
    mode: "analysis" as const,
    base: "object" as const,
    open: true,
    agentVisible: false,
    hasDetail: false,
    objectContent: <p>档案正文</p>,
    agentHostRef: vi.fn(),
    detailHostRef: vi.fn(),
    onBaseChange: vi.fn(),
    onClose: vi.fn(),
    history: { backLabel: "", forwardLabel: "", canBack: false, canForward: false, back: vi.fn(), forward: vi.fn() },
  };
}

it("uses labelled icon-only tabs without exposing Agent as visible copy", () => {
  const input = props();
  const { rerender } = render(<WorkbenchSidebar {...input} />);
  const object = screen.getByRole("tab", { name: "对象详情" });
  const companion = screen.getByRole("tab", { name: "协作者" });
  expect(object).toHaveAttribute("title", "对象详情");
  expect(companion).toHaveAttribute("title", "协作者");
  expect(object).toHaveAttribute("aria-selected", "true");
  expect(companion).toHaveAttribute("aria-selected", "false");
  expect(object.textContent).toBe("");
  expect(companion.textContent).toBe("");
  expect(object.firstElementChild).toHaveAttribute("aria-hidden", "true");
  fireEvent.click(companion);
  expect(input.onBaseChange).toHaveBeenCalledWith("agent");
  rerender(<WorkbenchSidebar {...input} base="agent" agentVisible />);
  expect(companion).toHaveAttribute("aria-selected", "true");
  fireEvent.click(object);
  expect(input.onBaseChange).toHaveBeenCalledWith("object");
});

it("also presents the icon switcher in compile mode", () => {
  render(<WorkbenchSidebar {...props()} mode="compile" />);
  expect(screen.getByRole("tab", { name: "协作者" })).toBeInTheDocument();
});

it("uses the dossier icon for the workbench heading and preserves navigation", () => {
  const input = props();
  render(<WorkbenchSidebar {...input} mode="workbench" />);
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  const headingIcon = screen.getByRole("img", { name: "对象详情" });
  expect(headingIcon).toHaveAttribute("title", "对象详情");
  expect(headingIcon.textContent).toBe("");
  expect(headingIcon.firstElementChild).toHaveAttribute("data-page", "object");
  expect(screen.queryByText("对象详情")).not.toBeInTheDocument();
  expect(screen.getByText("档案正文")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "收起对象上下文" }));
  expect(input.onClose).toHaveBeenCalledOnce();
});
