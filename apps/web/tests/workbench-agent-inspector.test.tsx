import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorkbenchAgentInspector } from "@/features/analyst-workbench/workbench-agent-inspector";
import type {
  AgentAuditFindingView,
  AgentMessageView,
  AgentPatchSetView,
} from "@/lib/api-client";

const patchSet: AgentPatchSetView = {
  patch_set_id: 61,
  thread_id: 8,
  source_message_id: 9,
  task_run_id: 10,
  base_draft_revision: 4,
  reason_summary: "按证据补足人物说明。",
  status: "pending",
  is_stale: false,
  applied_from_revision: null,
  applied_to_revision: null,
  undone_to_revision: null,
  operations: [
    {
      operation_id: 612,
      operation_key: "second",
      ordinal: 2,
      object_id: "object:location",
      object_type: "location",
      operation_type: "field_update",
      field_path: "/description",
      expected_object_revision: 4,
      old_value: "旧地点描述",
      new_value: "新地点描述",
      reason: "补足地点信息。",
      decision: "pending",
      reviewed_at: null,
    },
    {
      operation_id: 611,
      operation_key: "first",
      ordinal: 1,
      object_id: "object:person",
      object_type: "entity",
      operation_type: "field_update",
      field_path: "/name",
      expected_object_revision: 4,
      old_value: "旧名字",
      new_value: "新名字",
      reason: "统一人物称谓。",
      decision: "pending",
      reviewed_at: null,
    },
  ],
  validation_warning: false,
  validator_issues: [],
  created_at: "2026-08-20T10:00:00Z",
  updated_at: "2026-08-20T10:00:00Z",
};

const message: AgentMessageView = {
  message_id: 9,
  thread_id: 8,
  sequence_no: 2,
  role: "assistant",
  status: "completed",
  content: "建议补足两项信息。",
  task: null,
  referenced_object_ids: [],
  referenced_event_ids: [],
  referenced_validation_issue_ids: [],
  suggested_view: null,
  patch_set: patchSet,
  created_at: "2026-08-20T10:00:00Z",
  updated_at: "2026-08-20T10:00:00Z",
};

const finding: AgentAuditFindingView = {
  finding_id: "F1",
  kind: "contradiction",
  severity: "S2",
  title: "证词与日志时间冲突",
  statement: "两条已取证记录对同一时段给出了不同描述。",
  needs_manual_review: true,
  evidence_object_ids: ["object:person"],
  evidence_event_ids: [],
  evidence_validation_issue_ids: [],
};

function renderInspector(onApply = vi.fn()) {
  render(
    <WorkbenchAgentInspector
      busyPatchSetId={null}
      eventLabels={{}}
      findings={[]}
      focusFindingId={null}
      focusPatchSetId={61}
      issueLabels={{}}
      objectLabels={{ "object:person": "研究员", "object:location": "灯塔" }}
      onApply={onApply}
      onFocusPatch={vi.fn()}
      onLocateEvent={vi.fn()}
      onLocateIssue={vi.fn()}
      onLocateObject={vi.fn()}
      onRetry={vi.fn()}
      onUndo={vi.fn()}
      patches={[{ message, patchSet }]}
    />,
  );
  return onApply;
}

describe("workbench agent inspector", () => {
  afterEach(cleanup);

  it("uses ordinal order and waits for an author confirmation before applying", () => {
    const onApply = renderInspector();

    const entries = screen.getAllByRole("checkbox");
    expect(entries[0]).toHaveAccessibleName("选择修改 研究员 /name");
    expect(entries[1]).toHaveAccessibleName("选择修改 灯塔 /description");
    expect(screen.getByText(/目标 Draft R4/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全部采纳" }));
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认应用" }));
    expect(onApply).toHaveBeenCalledWith(patchSet, null);
  });

  it("keeps an explicit two-step confirmation for rejecting every operation", () => {
    const onApply = renderInspector();

    fireEvent.click(screen.getByRole("button", { name: "全部拒绝" }));
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认拒绝" }));
    expect(onApply).toHaveBeenCalledWith(patchSet, []);
  });

  it("distinguishes evidence from a missing server-provided impact set", () => {
    render(
      <WorkbenchAgentInspector
        busyPatchSetId={null}
        eventLabels={{}}
        findings={[{ message, finding }]}
        focusFindingId="F1"
        focusPatchSetId={null}
        issueLabels={{}}
        objectLabels={{ "object:person": "研究员" }}
        onApply={vi.fn()}
        onFocusPatch={vi.fn()}
        onLocateEvent={vi.fn()}
        onLocateIssue={vi.fn()}
        onLocateObject={vi.fn()}
        onRetry={vi.fn()}
        onUndo={vi.fn()}
        patches={[]}
      />,
    );

    expect(screen.getByText("对象 · 研究员")).toBeInTheDocument();
    expect(
      screen.getByText("服务端未提供影响集；上方仅为证据引用。"),
    ).toBeInTheDocument();
  });
});
