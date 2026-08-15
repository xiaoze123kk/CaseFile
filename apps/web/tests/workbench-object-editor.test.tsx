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
  const rendered = render(
    <WorkbenchObjectEditor
      document={document}
      navigationNotice={null}
      onDirtyChange={vi.fn()}
      onSave={onSave}
      onSelectObject={onSelectObject}
      readOnly={false}
      revision={7}
      saving={false}
      selectedObjectId={selectedObjectId}
      {...overrides}
    />,
  );
  return { ...rendered, onSave, onSelectObject };
}

describe("workbench object editor", () => {
  it("renders six object kinds as readable browse details before showing edit controls", () => {
    const selections = [
      ["res_root_cause", "重启根因"],
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
    fireEvent.click(screen.getByRole("button", { name: /系统第七次重启/ }));
    expect(onSelectObject).toHaveBeenCalledWith("evt_restart_seven");

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const reliability = screen.getByRole("combobox", { name: "可靠度" });
    expect(within(reliability).getByRole("option", { name: "低" })).toHaveValue("low");
    fireEvent.change(reliability, { target: { value: "low" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(onSave).toHaveBeenCalledWith(
      "info_restart_log",
      expect.objectContaining({ reliability: "low", truth_status: "canon_true" }),
    );
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

    expect(within(editor).queryByText("候选预览，只读")).not.toBeInTheDocument();
    expect(within(editor).queryByRole("button", { name: "编辑" })).not.toBeInTheDocument();
    expect(within(editor).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("returns an edited confirmed conclusion to proposed status", () => {
    const concludedDocument = structuredClone(document);
    concludedDocument.resolution_specs[0] = {
      ...concludedDocument.resolution_specs[0],
      conclusion: {
        outcome: "answer",
        review_status: "confirmed",
        summary: "自动安全机制触发了连续重启。",
        values: [{
          slot_id: "slot_root_cause",
          value: { object_type: "claim", object_id: "claim_backup_trigger" },
        }],
        selected_hypothesis_refs: [{
          object_type: "hypothesis",
          object_id: "hyp_automatic_restart",
        }],
        supporting_reasoning_path_refs: [{
          object_type: "reasoning_path",
          object_id: "path_causal_restart",
        }],
        rationale: "日志与备份触发条件一致。",
        unresolved_gaps: [],
      },
    };
    const { onSave } = renderEditor("res_root_cause", { document: concludedDocument });

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "结论摘要" }), {
      target: { value: "自动安全机制触发了第七次重启。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(onSave).toHaveBeenCalledWith(
      "res_root_cause",
      expect.objectContaining({
        conclusion: expect.objectContaining({
          review_status: "proposed",
          summary: "自动安全机制触发了第七次重启。",
        }),
      }),
    );
  });

  it("requires evidence gaps for undetermined conclusions", () => {
    const { onSave } = renderEditor("res_root_cause");

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "结论摘要" }), {
      target: { value: "现有解释仍然并存。" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "裁决依据" }), {
      target: { value: "现有日志无法区分两个机制。" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "未解决缺口（每行一项）" }), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(screen.getByText("未定论必须说明证据缺口。")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("requires every required answer slot before saving an answer", () => {
    const { onSave } = renderEditor("res_root_cause");

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("combobox", { name: "结论类型" }), {
      target: { value: "answer" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "结论摘要" }), {
      target: { value: "自动安全机制触发了连续重启。" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "裁决依据" }), {
      target: { value: "日志与备份触发条件一致。" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "获选或并存假设 ID（每行一项）" }), {
      target: { value: "hyp_automatic_restart" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "依据路径 ID（每行一项）" }), {
      target: { value: "path_causal_restart" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(screen.getByText("请填写必填答案槽位：slot_root_cause。")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("preselects required Claim-target paths for a new conclusion", async () => {
    const claimTargetDocument = structuredClone(document);
    claimTargetDocument.reasoning_paths[0] = {
      ...claimTargetDocument.reasoning_paths[0],
      target_ref: { object_type: "claim", object_id: "claim_backup_trigger" },
    };
    const { onSave } = renderEditor("res_root_cause", { document: claimTargetDocument });

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.getByRole("textbox", { name: "获选或并存假设 ID（每行一项）" })).toHaveValue(
      "hyp_automatic_restart",
    );
    expect(screen.getByRole("textbox", { name: "依据路径 ID（每行一项）" })).toHaveValue(
      "path_causal_restart",
    );
    fireEvent.change(screen.getByRole("textbox", { name: "结论摘要" }), {
      target: { value: "现有解释仍然并存。" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "裁决依据" }), {
      target: { value: "必要路径面向当前问题要求的关键主张。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(onSave).toHaveBeenCalledWith(
      "res_root_cause",
      expect.objectContaining({
        conclusion: expect.objectContaining({
          supporting_reasoning_path_refs: [{
            object_type: "reasoning_path",
            object_id: "path_causal_restart",
          }],
        }),
      }),
    );
  });

  it("shows the server validation reason when saving fails", async () => {
    const onSave = vi.fn().mockResolvedValue({
      status: "error",
      message: "结论依据路径必须属于当前问题的必要推理链。",
    });
    renderEditor("res_root_cause", { onSave });
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "结论摘要" }), {
      target: { value: "现有解释仍然并存。" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "裁决依据" }), {
      target: { value: "现有日志不足以区分两种解释。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    expect(await screen.findByText("结论依据路径必须属于当前问题的必要推理链。")).toBeInTheDocument();
  });
});
