import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkbenchSidebar } from "@/features/analyst-workbench/workbench-sidebar";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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

it("shows text labels so sidebar destinations are recognizable", () => {
  const input = props();
  const { rerender } = render(<WorkbenchSidebar {...input} />);
  const object = screen.getByRole("tab", { name: "对象详情" });
  const companion = screen.getByRole("tab", { name: "协作者" });
  expect(object).toHaveAttribute("title", "对象详情");
  expect(companion).toHaveAttribute("title", "协作者");
  expect(object).toHaveAttribute("aria-selected", "true");
  expect(companion).toHaveAttribute("aria-selected", "false");
  expect(object).toHaveTextContent("对象详情");
  expect(companion).toHaveTextContent("协作者");
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

it("animates object changes without remounting content and respects reduced motion", () => {
  const input = { ...props(), objectContent: <input aria-label="编辑中的内容" defaultValue="原内容" /> };
  const { rerender } = render(<WorkbenchSidebar {...input} objectKey="first" />);
  const editor = screen.getByRole("textbox", { name: "编辑中的内容" });
  fireEvent.change(editor, { target: { value: "未保存的内容" } });
  const cancel = vi.fn();
  const animate = vi.fn(() => ({ cancel }) as unknown as Animation);
  editor.parentElement!.animate = animate;
  rerender(<WorkbenchSidebar {...input} objectKey="second" />);
  expect(animate).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "编辑中的内容" })).toBe(editor);
  expect(editor).toHaveValue("未保存的内容");
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  rerender(<WorkbenchSidebar {...input} objectKey="third" />);
  expect(cancel).toHaveBeenCalledOnce();
  expect(animate).toHaveBeenCalledOnce();
});

it("uses the dossier icon for the workbench heading and preserves navigation", () => {
  const input = props();
  render(<WorkbenchSidebar {...input} mode="workbench" />);
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  const headingIcon = screen.getByRole("img", { name: "对象详情" });
  expect(headingIcon).toHaveAttribute("title", "对象详情");
  expect(headingIcon).toHaveTextContent("对象详情");
  expect(headingIcon.firstElementChild).toHaveAttribute("data-page", "object");
  expect(screen.getByText("对象详情")).toBeInTheDocument();
  expect(screen.getByText("档案正文")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "收起对象上下文" }));
  expect(input.onClose).toHaveBeenCalledOnce();
});
