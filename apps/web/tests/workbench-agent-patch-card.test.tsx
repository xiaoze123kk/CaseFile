import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PublicDisplayValue, PublicPatchChange, PublicPatchSet } from "@casefile/contracts";
import { AgentPatchCard, groupPatchChanges, patchChangeExplanation } from "@/features/analyst-workbench/workbench-agent-patch-card";
import { AgentPatchReview } from "@/features/analyst-workbench/workbench-agent-inspector";

const target = { target_id: "person:su", name: "苏念", type_label: "人物" };
const update: PublicPatchChange = { change_id: 1, kind: "update", relationship: "requested", target, field_label: "已知信息",
  before: { kind: "text", text: "已知重逢的细节" }, after: { kind: "text", text: "仅知道时间表被改动" }, explanation: "角色只能知道当时可接触的信息。" };
const create: PublicPatchChange = { change_id: 2, kind: "create", relationship: "requested", target: { target_id: null, name: "现场记录", type_label: "信息" }, after: { kind: "text", text: "时间表边缘的涂改痕迹" }, explanation: "补充现场可观察的线索。" };
const deletion: PublicPatchChange = { change_id: 3, kind: "delete", relationship: "consistency_support", target: { target_id: "relation:old", name: "旧关联", type_label: "关系" }, before: { kind: "reference", text: "引用尚未发生的重逢" }, explanation: "删除提前引用。" };
const patch: PublicPatchSet = { patch_id: 8, title: "修正认知时间线", summary: "调整角色所知范围，并补充现场记录。", status: "pending", review_rule: "atomic", base_revision: 2,
  impact: { summary: "涉及三项修改，包含删除。", affected_change_count: 3, has_deletions: true }, changes: [update, create, deletion], actions: { can_simulate: true, can_undo: false, can_redo: false } };

afterEach(cleanup);

describe("public patch dossier card", () => {
  it("does not present cached legacy placeholder prose as a concrete reason", () => {
    expect(patchChangeExplanation("这是你要求调整的卷宗内容。")).toContain("未提供具体原因");
    expect(patchChangeExplanation(update.explanation)).toBe(update.explanation);
  });
  it("shows create, update and delete comparisons, provenance and true scope", () => {
    const locate = vi.fn();
    render(<AgentPatchCard patchSet={patch} onLocateObject={locate} />);
    expect(screen.getByRole("heading", { name: "修正认知时间线" })).toBeInTheDocument();
    for (const text of ["3 项修改", "新增 1 项", "调整 1 项", "删除 1 项", "尚无此对象", "移除此对象", "为保持一致性同步调整"]) expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.queryByText("不改变人物动机")).not.toBeInTheDocument();
    expect(screen.queryByText(/person:su|relation:old/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "在工作台定位：现场记录" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "在工作台定位：苏念" }));
    expect(locate).toHaveBeenCalledWith("person:su");
  });

  it("groups repeated fields by identity, but not same-named or anonymous objects", () => {
    const groups = groupPatchChanges([update, { ...update, change_id: 4 }, { ...update, change_id: 5, target: { ...target, target_id: "other" } }, create, { ...create, change_id: 6 }]);
    expect(groups.map((group) => group.changes.length)).toEqual([2, 1, 1, 1]);
  });

  it.each(["empty", "text", "number", "boolean", "time_range", "reference", "list"] as const)("preserves public %s values without parsing or dropping content", (kind) => {
    const value: PublicDisplayValue = { kind, text: "第一行\n第二行（完整公开值）" };
    render(<AgentPatchCard patchSet={{ ...patch, changes: [{ ...update, after: value }] }} />);
    expect(screen.getByText("第一行 第二行（完整公开值）")).toHaveTextContent(value.text.replace("\n", " "));
  });

  it("makes every item in a large patch accessible without silently truncating the batch", () => {
    const changes = Array.from({ length: 200 }, (_, index) => ({ ...update, change_id: index + 1, field_label: `字段 ${index + 1}` }));
    render(<AgentPatchCard patchSet={{ ...patch, changes }} />);
    expect(screen.queryByText("字段 200")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /展开全部 200 项修改/ }));
    expect(screen.getByText("字段 200")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "收起修改清单" }));
    expect(screen.queryByText("字段 200")).not.toBeInTheDocument();
  });

  it.each(["stale", "applied", "undone", "rejected"] as const)("never offers apply for %s proposals", (status) => {
    render(<AgentPatchReview patchSet={{ ...patch, status }} busy={false} conversation requireApplyConfirmation onApply={vi.fn()} onUndo={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /^应用/ })).not.toBeInTheDocument();
  });

  it("checks before confirmation and never auto-applies a pending proposal", async () => {
    const apply = vi.fn();
    const simulate = vi.fn().mockResolvedValue({ patch_id: 8, can_apply: true, blockers: [], warnings: [], confirmation_token: "private-proof", requires_author_confirmation: false });
    render(<AgentPatchReview patchSet={patch} busy={false} conversation requireApplyConfirmation onApply={apply} onSimulate={simulate} onUndo={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "应用 3 项" }));
    expect(await screen.findByRole("button", { name: "确认应用" })).toBeEnabled();
    expect(simulate).toHaveBeenCalledWith(null, [], undefined);
    expect(apply).not.toHaveBeenCalled();
    expect(screen.queryByText("private-proof")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认应用" }));
    expect(apply).toHaveBeenCalledWith(null, { confirmationToken: "private-proof" });
  });

  it("keeps blocked proposals out of the confirmation step", async () => {
    const apply = vi.fn();
    const simulate = vi.fn().mockResolvedValue({ patch_id: 8, can_apply: false, blockers: [{ notice_id: "private-blocker", message: "删除对象仍被引用" }], warnings: [], confirmation_token: null, requires_author_confirmation: false });
    render(<AgentPatchReview patchSet={patch} busy={false} conversation requireApplyConfirmation onApply={apply} onSimulate={simulate} onUndo={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "应用 3 项" }));
    expect(await screen.findByText("删除对象仍被引用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认应用" })).not.toBeInTheDocument();
    expect(apply).not.toHaveBeenCalled();
  });

  it("invalidates a selective review when selection changes and disables empty apply", async () => {
    const simulate = vi.fn().mockResolvedValue({ patch_id: 8, can_apply: true, blockers: [], warnings: [], confirmation_token: null, requires_author_confirmation: false });
    render(<AgentPatchReview patchSet={{ ...patch, review_rule: "selective", changes: [update] }} busy={false} conversation requireApplyConfirmation onApply={vi.fn()} onSimulate={simulate} onUndo={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "应用 1 项" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认应用" })).toBeEnabled());
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.queryByRole("button", { name: "确认应用" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "应用 0 项" })).toBeDisabled();
  });
});
