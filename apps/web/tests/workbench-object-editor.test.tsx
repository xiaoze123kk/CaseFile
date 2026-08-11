import type { CaseFile } from "@casefile/contracts";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import restartLoopFixture from "../../../fixtures/casefiles/restart_loop.casefile.json";
import { WorkbenchObjectEditor } from "@/features/analyst-workbench/workbench-object-editor";

const document = restartLoopFixture as unknown as CaseFile;

afterEach(cleanup);

function renderEditor(
  selectedObjectId: string,
  overrides: Partial<ComponentProps<typeof WorkbenchObjectEditor>> = {},
) {
  const onSave = vi.fn().mockResolvedValue("saved");
  const onSelectObject = vi.fn();
  const onSelectRelatedEvent = vi.fn();
  const rendered = render(
    <WorkbenchObjectEditor
      document={document}
      navigationNotice={null}
      onDirtyChange={vi.fn()}
      onSave={onSave}
      onSelectObject={onSelectObject}
      onSelectRelatedEvent={onSelectRelatedEvent}
      readOnly={false}
      relatedEvents={[]}
      revision={7}
      saving={false}
      selectedObjectId={selectedObjectId}
      {...overrides}
    />,
  );
  return { ...rendered, onSave, onSelectObject, onSelectRelatedEvent };
}

describe("workbench object editor", () => {
  it("renders five object kinds as readable browse details before showing edit controls", () => {
    const selections = [
      ["ent_researcher", "林研究员"],
      ["info_restart_log", "第七次重启日志"],
      ["evt_restart_seven", "系统第七次重启"],
      ["loc_lab", "主实验室"],
      ["hyp_automatic_restart", "自动安全重启"],
    ] as const;

    for (const [selectedObjectId, title] of selections) {
      const view = renderEditor(selectedObjectId);
      expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
      expect(screen.queryByRole("textbox", { name: "名称" })).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it("shows Chinese choices but submits the original information enum value", async () => {
    const { onSave, onSelectObject } = renderEditor("info_restart_log");

    expect(screen.getByText("可靠度")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const reliability = screen.getByRole("combobox", { name: "可靠度" });
    expect(within(reliability).getByRole("option", { name: "低" })).toHaveValue("low");
    fireEvent.change(reliability, { target: { value: "low" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(onSave).toHaveBeenCalledWith(
      "info_restart_log",
      expect.objectContaining({ reliability: "low", truth_status: "canon_true" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /系统第七次重启/ }));
    expect(onSelectObject).toHaveBeenCalledWith("evt_restart_seven");
  });

  it("keeps event time changes in the timeline preview flow", () => {
    const { onSave } = renderEditor("evt_restart_seven");

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.queryByLabelText("开始日期")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("开始时间")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("时间精度")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("标题"), {
      target: { value: "系统第七次重启（修订）" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(onSave).toHaveBeenCalledWith(
      "evt_restart_seven",
      expect.not.objectContaining({ time: expect.anything() }),
    );
  });

  it("renders a candidate as readable, no-form preview", () => {
    renderEditor("ent_researcher", { readOnly: true });
    const editor = screen.getByRole("region", { name: "对象详情（只读）" });

    expect(within(editor).getByText("候选预览，只读")).toBeInTheDocument();
    expect(within(editor).queryByRole("button", { name: "编辑" })).not.toBeInTheDocument();
    expect(within(editor).queryByRole("textbox")).not.toBeInTheDocument();
  });
});
