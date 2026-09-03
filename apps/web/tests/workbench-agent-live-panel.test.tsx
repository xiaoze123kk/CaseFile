import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type {
  PublicAgentEvent,
  PublicAgentMessage,
  PublicAgentRun,
  PublicPatchReviewResult,
  PublicPatchSet,
} from "@casefile/contracts";

import { AgentLivePanel } from "@/features/analyst-workbench/workbench-agent-live-panel";

const mocks = vi.hoisted(() => ({
  applyAgentPatchSet: vi.fn(),
  cancelAgentRun: vi.fn(),
  createAgentThread: vi.fn(),
  getAgentRun: vi.fn(),
  listAgentMessages: vi.fn(),
  listAgentThreads: vi.fn(),
  redoAgentPatchSet: vi.fn(),
  sendAgentMessage: vi.fn(),
  sendAgentRoutingFeedback: vi.fn(),
  simulateAgentPatchSet: vi.fn(),
  streamAgentRunEvents: vi.fn(),
  undoAgentPatchSet: vi.fn(),
  updateAgentThread: vi.fn(),
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    applyAgentPatchSet: mocks.applyAgentPatchSet,
    cancelAgentRun: mocks.cancelAgentRun,
    createAgentThread: mocks.createAgentThread,
    getAgentRun: mocks.getAgentRun,
    listAgentMessages: mocks.listAgentMessages,
    listAgentThreads: mocks.listAgentThreads,
    redoAgentPatchSet: mocks.redoAgentPatchSet,
    sendAgentMessage: mocks.sendAgentMessage,
    sendAgentRoutingFeedback: mocks.sendAgentRoutingFeedback,
    simulateAgentPatchSet: mocks.simulateAgentPatchSet,
    streamAgentRunEvents: mocks.streamAgentRunEvents,
    undoAgentPatchSet: mocks.undoAgentPatchSet,
    updateAgentThread: mocks.updateAgentThread,
  };
});

const thread = {
  thread_id: 7,
  title: "主线复查",
  title_source: "user" as const,
  is_pinned: false,
  status: "active" as const,
  last_message_at: "2026-08-26T08:00:00Z",
  created_at: "2026-08-26T08:00:00Z",
  updated_at: "2026-08-26T08:00:00Z",
};

function run(overrides: Partial<PublicAgentRun> = {}): PublicAgentRun {
  return {
    run_id: 80,
    status: "queued",
    activity: null,
    cancellable: true,
    failure: null,
    ...overrides,
  };
}

function message(overrides: Partial<PublicAgentMessage> = {}): PublicAgentMessage {
  return {
    message_id: 90,
    sequence: 2,
    role: "assistant",
    status: "completed",
    response_kind: "answer",
    body: "公开回复正文。",
    interpretation: "analysis",
    references: [],
    findings: [],
    patch: null,
    run: run({ status: "succeeded", cancellable: false }),
    created_at: "2026-08-26T08:01:00Z",
    updated_at: "2026-08-26T08:01:00Z",
    ...overrides,
  };
}

function patch(overrides: Partial<PublicPatchSet> = {}): PublicPatchSet {
  return {
    patch_id: 200,
    title: "修改建议",
    summary: "你要求的修改 1 项。",
    status: "pending",
    review_rule: "atomic",
    base_revision: 4,
    impact: {
      summary: "共涉及 1 项卷宗修改。",
      affected_change_count: 1,
      has_deletions: false,
    },
    changes: [
      {
        change_id: 201,
        kind: "update",
        relationship: "requested",
        target: {
          target_id: "entity_internal_handle",
          type_label: "人物或对象",
          name: "林澈",
        },
        field_label: "描述",
        before: { kind: "text", text: "旧描述" },
        after: { kind: "text", text: "新描述" },
        explanation: "这是你要求调整的卷宗内容。",
      },
    ],
    actions: { can_simulate: true, can_undo: false, can_redo: false },
    ...overrides,
  };
}

function renderPanel(
  overrides: Partial<ComponentProps<typeof AgentLivePanel>> = {},
) {
  return render(
    <AgentLivePanel
      draftId={9}
      draftRevision={4}
      focus={{ object_ids: [], event_ids: [], validation_issue_ids: [], view: null }}
      kickoff={null}
      onClose={vi.fn()}
      onDraftChanged={vi.fn().mockResolvedValue(undefined)}
      onFocusFinding={vi.fn()}
      onFocusPatch={vi.fn()}
      onLocateEvent={vi.fn()}
      onLocateObject={vi.fn()}
      projectId={1}
      referenceLabels={{ objects: {}, events: {}, issues: {} }}
      surface="desk"
      {...overrides}
    />,
  );
}

describe("workbench public agent live panel", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listAgentThreads.mockResolvedValue([thread]);
    mocks.listAgentMessages.mockResolvedValue([]);
    mocks.getAgentRun.mockResolvedValue(
      run({ status: "succeeded", cancellable: false }),
    );
    mocks.streamAgentRunEvents.mockResolvedValue(0);
    mocks.sendAgentRoutingFeedback.mockResolvedValue({
      message_id: 90,
      acknowledged: true,
      interpretation: "analysis",
    });
  });

  afterEach(cleanup);

  it("renders historical messages from PublicAgentMessage only", async () => {
    mocks.listAgentMessages.mockResolvedValue([
      message({
        references: [
          { kind: "story_item", target_id: "opaque_story_ref", label: "林澈" },
        ],
      }),
    ]);
    renderPanel();

    expect(await screen.findByText("公开回复正文。")).toBeInTheDocument();
    expect(screen.queryByText("结论")).not.toBeInTheDocument();
    expect(screen.queryByText("理解为：分析")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "引用 1" })).not.toBeInTheDocument();
    expect(screen.queryByText(/provider|prompt|token|route_source|TaskRun/i)).not.toBeInTheDocument();
  });

  it("sends a public receipt and follows public activity/context events", async () => {
    const queued = run();
    const userMessage = message({
      message_id: 91,
      sequence: 1,
      role: "user",
      body: "检查时间线。",
      interpretation: null,
      run: null,
    });
    const assistantMessage = message({
      message_id: 92,
      status: "pending",
      body: null,
      run: queued,
    });
    mocks.sendAgentMessage.mockResolvedValue({
      user_message: userMessage,
      assistant_message: assistantMessage,
    });
    mocks.streamAgentRunEvents.mockImplementation(
      async (
        _actorId: number,
        _projectId: number,
        _runId: number,
        onEvent: (event: PublicAgentEvent) => void,
        signal: AbortSignal,
      ) => {
        onEvent({ sequence: 4, event: "run.activity", activity: "checking" });
        onEvent({ sequence: 7, event: "run.context", context_state: "near_limit" });
        return await new Promise<number>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("aborted")), {
            once: true,
          });
        });
      },
    );
    renderPanel();

    const composer = await screen.findByRole("textbox", {
      name: "给卷宗统筹 Agent 的指令",
    });
    await waitFor(() => expect(composer).toBeEnabled());
    fireEvent.change(composer, { target: { value: "检查时间线。" } });
    const sendButton = screen.getByRole("button", { name: "发送" });
    await waitFor(() => expect(sendButton).toBeEnabled());
    fireEvent.click(sendButton);

    expect(await screen.findByText("检查时间线。")).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 回复状态")).not.toBeInTheDocument();
    expect(composer).toHaveAttribute(
      "placeholder",
      "卷宗正在梳理线索，请稍候……",
    );
    expect(mocks.sendAgentMessage).toHaveBeenCalledWith(
      1,
      1,
      7,
      9,
      4,
      "检查时间线。",
      "deepseek",
      expect.any(Object),
      { entrypoint: "free_text" },
    );
    expect(mocks.streamAgentRunEvents).toHaveBeenCalledWith(
      1,
      1,
      80,
      expect.any(Function),
      expect.any(AbortSignal),
      0,
    );
  });

  it("keeps the composer enabled after reopening the current thread", async () => {
    const threadHost = document.createElement("div");
    document.body.appendChild(threadHost);
    renderPanel({ threadHost });

    const composer = await screen.findByRole("textbox", {
      name: "给卷宗统筹 Agent 的指令",
    });
    await waitFor(() => expect(composer).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: /主线复查/ }));
    fireEvent.click(screen.getByRole("option", { name: /主线复查/ }));

    await waitFor(() => expect(mocks.listAgentMessages).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(composer).toBeEnabled());
    fireEvent.change(composer, { target: { value: "你好" } });
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    threadHost.remove();
  });

  it("uses public review/apply/undo/redo DTOs and keeps handles out of the UI", async () => {
    const pendingPatch = patch();
    const patchMessage = message({ patch: pendingPatch, response_kind: "patch_proposal" });
    mocks.listAgentMessages.mockResolvedValue([patchMessage]);
    const review: PublicPatchReviewResult = {
      patch_id: 200,
      can_apply: true,
      blockers: [],
      warnings: [],
      requires_author_confirmation: false,
      confirmation_token: null,
    };
    mocks.simulateAgentPatchSet.mockResolvedValue(review);
    const applied = patch({
      status: "applied",
      actions: { can_simulate: false, can_undo: true, can_redo: false },
    });
    const undone = patch({
      status: "undone",
      actions: { can_simulate: false, can_undo: false, can_redo: true },
    });
    mocks.applyAgentPatchSet.mockResolvedValue({ patch: applied, review, revision: 5 });
    mocks.undoAgentPatchSet.mockResolvedValue({ patch: undone, review, revision: 6 });
    mocks.redoAgentPatchSet.mockResolvedValue({ patch: applied, review, revision: 7 });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "检查修改影响" }));
    await waitFor(() =>
      expect(mocks.simulateAgentPatchSet).toHaveBeenCalledWith(
        1,
        1,
        200,
        9,
        4,
        null,
        [],
        undefined,
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "应用修改" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "应用修改" }));
    mocks.listAgentMessages.mockResolvedValue([
      { ...patchMessage, patch: applied },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "确认应用" }));
    await waitFor(() =>
      expect(mocks.applyAgentPatchSet).toHaveBeenCalledWith(
        1,
        1,
        200,
        9,
        4,
        null,
        undefined,
        [],
        undefined,
      ),
    );
    mocks.listAgentMessages.mockResolvedValue([
      { ...patchMessage, patch: undone },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "撤销应用" }));
    await waitFor(() =>
      expect(mocks.undoAgentPatchSet).toHaveBeenCalledWith(1, 1, 200, 9, 5),
    );
    mocks.listAgentMessages.mockResolvedValue([
      { ...patchMessage, patch: applied },
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "重做应用" }));
    await waitFor(() =>
      expect(mocks.redoAgentPatchSet).toHaveBeenCalledWith(1, 1, 200, 9, 6),
    );
    expect(screen.queryByText(/200|201|entity_internal_handle|R4|patch/i)).not.toBeInTheDocument();
  });
});
