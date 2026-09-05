import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type {
  PublicAgentMessage,
  PublicFinding,
  PublicPatchReviewResult,
  PublicPatchSet,
} from "@casefile/contracts";

import { WorkbenchAgentInspector } from "@/features/analyst-workbench/workbench-agent-inspector";

const patchSet: PublicPatchSet = {
  patch_id: 61,
  title: "修改建议",
  summary: "你要求的修改 1 项；为保持一致性同步调整 1 项。",
  status: "pending",
  review_rule: "atomic",
  base_revision: 4,
  impact: {
    summary: "共涉及 2 项卷宗修改，包含 1 项一致性调整。",
    affected_change_count: 2,
    has_deletions: false,
  },
  changes: [
    {
      change_id: 611,
      kind: "update",
      relationship: "requested",
      target: {
        target_id: "object:person",
        type_label: "人物或对象",
        name: "研究员",
      },
      field_label: "名称",
      before: { kind: "text", text: "旧名字" },
      after: { kind: "text", text: "新名字" },
      explanation: "这是你要求调整的卷宗内容。",
    },
    {
      change_id: 612,
      kind: "update",
      relationship: "consistency_support",
      target: {
        target_id: "object:location",
        type_label: "地点",
        name: "灯塔",
      },
      field_label: "描述",
      before: { kind: "text", text: "旧地点描述" },
      after: { kind: "text", text: "新地点描述" },
      explanation: "为保持卷宗前后一致，需要同步调整这项内容。",
    },
  ],
  actions: { can_simulate: true, can_undo: false, can_redo: false },
};

const message: PublicAgentMessage = {
  message_id: 9,
  sequence: 2,
  role: "assistant",
  status: "completed",
  response_kind: "patch_proposal",
  body: "建议补足两项信息。",
  interpretation: "change_request",
  references: [],
  findings: [],
  patch: patchSet,
  run: null,
  created_at: "2026-08-20T10:00:00Z",
  updated_at: "2026-08-20T10:00:00Z",
};

const passedReview: PublicPatchReviewResult = {
  patch_id: 61,
  can_apply: true,
  blockers: [],
  warnings: [],
  requires_author_confirmation: false,
  confirmation_token: null,
};

const finding: PublicFinding = {
  finding_id: "finding_opaque_1",
  severity: "warning",
  title: "证词与日志时间冲突",
  statement: "两条已取证记录对同一时段给出了不同描述。",
};

function renderInspector({
  currentPatch = patchSet,
  onApply,
  onSimulate,
  findings = [],
}: {
  currentPatch?: PublicPatchSet;
  onApply?: ComponentProps<typeof WorkbenchAgentInspector>["onApply"];
  onSimulate?: NonNullable<
    ComponentProps<typeof WorkbenchAgentInspector>["onSimulate"]
  >;
  findings?: Array<{ message: PublicAgentMessage; finding: PublicFinding }>;
} = {}) {
  const effectiveOnApply = onApply ??
    vi.fn<ComponentProps<typeof WorkbenchAgentInspector>["onApply"]>();
  const effectiveOnSimulate = onSimulate ??
    vi
      .fn<
        NonNullable<ComponentProps<typeof WorkbenchAgentInspector>["onSimulate"]>
      >()
      .mockResolvedValue(passedReview);
  render(
    <WorkbenchAgentInspector
      busyPatchSetId={null}
      findings={findings}
      focusFindingId={null}
      focusPatchSetId={currentPatch.patch_id}
      onApply={effectiveOnApply}
      onFocusPatch={vi.fn()}
      onLocateObject={vi.fn()}
      onRetry={vi.fn()}
      onSimulate={effectiveOnSimulate}
      onUndo={vi.fn()}
      patches={[{ message: { ...message, patch: currentPatch }, patchSet: currentPatch }]}
    />,
  );
  return { onApply: effectiveOnApply, onSimulate: effectiveOnSimulate };
}

describe("workbench agent public inspector", () => {
  afterEach(cleanup);

  it("renders author-readable grouped changes and keeps atomic patches indivisible", async () => {
    const { onApply, onSimulate } = renderInspector();

    expect(screen.getByText("你要求的修改")).toBeInTheDocument();
    expect(screen.getByText("为保持一致性同步调整")).toBeInTheDocument();
    expect(screen.getAllByText("修改前")).toHaveLength(2);
    expect(screen.getByText("新地点描述")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/Patch|Draft|R4|\/description|update_field/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "检查修改影响" }));
    await waitFor(() =>
      expect(onSimulate).toHaveBeenCalledWith(patchSet, null, [], undefined),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "应用修改" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "应用修改" }));
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认应用" }));
    expect(onApply).toHaveBeenCalledWith(patchSet, null, {});
  });

  it("allows change handles only for selective historical patches", async () => {
    const selective = { ...patchSet, review_rule: "selective" as const };
    const { onSimulate } = renderInspector({ currentPatch: selective });

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(2);
    expect(checkboxes[0]).toBeChecked();
    expect(checkboxes[1]).toBeChecked();
    fireEvent.click(checkboxes[0]!);
    fireEvent.click(screen.getByRole("button", { name: "检查修改影响" }));
    await waitFor(() =>
      expect(onSimulate).toHaveBeenCalledWith(selective, [612], [], undefined),
    );
  });

  it("keeps warning handles and confirmation tokens in controller state only", async () => {
    const warningReview: PublicPatchReviewResult = {
      patch_id: 61,
      can_apply: false,
      blockers: [],
      warnings: [
        { notice_id: "warning_opaque_1", message: "这项影响需要作者确认。" },
      ],
      requires_author_confirmation: true,
      confirmation_token: "opaque-delete-token",
    };
    const acceptedReview = { ...warningReview, can_apply: true };
    const onSimulate = vi
      .fn()
      .mockResolvedValueOnce(warningReview)
      .mockResolvedValueOnce(acceptedReview);
    const onApply = vi.fn();
    renderInspector({ onApply, onSimulate });

    fireEvent.click(screen.getByRole("button", { name: "检查修改影响" }));
    expect(await screen.findByText("这项影响需要作者确认。")).toBeInTheDocument();
    expect(screen.queryByText(/warning_opaque|opaque-delete-token/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("确认说明"), {
      target: { value: "我已审阅并接受这项影响。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "接受影响并重新检查" }));
    await waitFor(() =>
      expect(onSimulate).toHaveBeenLastCalledWith(
        patchSet,
        null,
        ["warning_opaque_1"],
        "我已审阅并接受这项影响。",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "应用修改" }));
    fireEvent.click(screen.getByRole("button", { name: "确认应用" }));
    expect(onApply).toHaveBeenCalledWith(patchSet, null, {
      confirmationToken: "opaque-delete-token",
      acceptedWarningIds: ["warning_opaque_1"],
      confirmationNote: "我已审阅并接受这项影响。",
    });
  });

  it("renders public findings without exposing their opaque identifiers", () => {
    renderInspector({ findings: [{ message, finding }] });

    expect(screen.getByText("证词与日志时间冲突")).toBeInTheDocument();
    expect(screen.getByText(finding.statement)).toBeInTheDocument();
    expect(screen.queryByText(finding.finding_id)).not.toBeInTheDocument();
  });

  it("does not invent a rejection operation by applying an empty selection", () => {
    const { onApply } = renderInspector();
    expect(screen.queryByRole("button", { name: "拒绝这组修改" })).not.toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });
});
