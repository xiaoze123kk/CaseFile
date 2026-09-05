import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { WorkbenchAgentPortal } from "@/features/analyst-workbench/workbench-agent-portal";

afterEach(() => { cleanup(); document.querySelectorAll("[data-test-host]").forEach((host) => host.remove()); });
it("keeps textarea identity, focus, selection and composition across docking slots", () => {
  const center = document.createElement("div");
  const side = document.createElement("div");
  center.dataset.testHost = "center"; side.dataset.testHost = "side";
  document.body.append(center, side);
  const content = <textarea aria-label="draft" defaultValue="未发送草稿" />;
  const { rerender } = render(<WorkbenchAgentPortal host={center}>{content}</WorkbenchAgentPortal>);
  const input = screen.getByRole("textbox") as HTMLTextAreaElement;
  input.focus(); input.setSelectionRange(2, 4);
  fireEvent.compositionStart(input);
  rerender(<WorkbenchAgentPortal host={side}>{content}</WorkbenchAgentPortal>);
  expect(center.contains(input)).toBe(true);
  fireEvent.compositionEnd(input);
  expect(side.contains(input)).toBe(true);
  expect(screen.getByRole("textbox")).toBe(input);
  expect(input).toHaveFocus();
  expect(input.selectionStart).toBe(2);
  expect(input.value).toBe("未发送草稿");
});
