import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { KnowledgeStateList } from "@/features/analyst-workbench/workbench-knowledge-state";
import type { DetailReference } from "@/features/analyst-workbench/workbench-object-detail-model";

afterEach(cleanup);
const reference: DetailReference = { id: "info_private", label: "一封旧信", kindLabel: "信息", missing: false, selectable: true };

it("counts each category and preserves real reference navigation without invented timing", () => {
  const onSelectObject = vi.fn();
  const { container } = render(<KnowledgeStateList states={[{
    asOf: { ...reference, id: "evt_night", label: "夜晚重逢", kindLabel: "事件" },
    known: [reference], believes: [{ ...reference, id: "claim_return", label: "他会回来", kindLabel: "论断", selectable: false }], falseBeliefs: [],
  }]} onSelectObject={onSelectObject} />);
  expect(screen.getByRole("listitem", { name: "已确认 1 项" })).toBeInTheDocument();
  expect(screen.getByRole("listitem", { name: "推测 1 项" })).toBeInTheDocument();
  expect(screen.getByRole("listitem", { name: "误判 0 项" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看信息“一封旧信”" }));
  expect(onSelectObject).toHaveBeenLastCalledWith("info_private");
  fireEvent.click(screen.getByRole("button", { name: "跳转查看截至事件“夜晚重逢”" }));
  expect(onSelectObject).toHaveBeenLastCalledWith("evt_night");
  expect(within(screen.getByRole("region", { name: "推测" })).queryByRole("button")).not.toBeInTheDocument();
  expect(screen.getByText("暂无错误认知")).toBeInTheDocument();
  expect(container.textContent).not.toMatch(/info_private|evt_night|掌握于/);
});

it("keeps missing references and the story start readable without clickable controls", () => {
  render(<KnowledgeStateList states={[{
    asOf: { ...reference, id: "", label: "卷宗起点", selectable: false },
    known: [{ ...reference, label: "已缺失的信息", missing: true }], believes: [], falseBeliefs: [],
  }]} onSelectObject={vi.fn()} />);
  expect(screen.getByText("卷宗起点")).toBeInTheDocument();
  expect(screen.getByText("已缺失的信息")).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
