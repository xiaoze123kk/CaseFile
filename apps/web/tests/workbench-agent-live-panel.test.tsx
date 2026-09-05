import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import type {
  PublicAgentEvent,
  PublicAgentMessage,
  PublicAgentRun,
  PublicPatchReviewResult,
  PublicPatchSet,
  PublicGoalSession,
} from "@casefile/contracts";

import { AgentLivePanel } from "@/features/analyst-workbench/workbench-agent-live-panel";

const mocks = vi.hoisted(() => ({
  applyAgentPatchSet: vi.fn(),
  cancelAgentRun: vi.fn(),
  createAgentThread: vi.fn(),
  getAgentRun: vi.fn(),
  listAgentMessages: vi.fn(),
  listAgentThreads: vi.fn(),
  listAgentRunFeedback: vi.fn(),
  getAgentGoal: vi.fn(),
  listAgentGoalDeliveries: vi.fn(),
  streamAgentGoalEvents: vi.fn(),
  cancelAgentGoal: vi.fn(),
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
    listAgentRunFeedback: mocks.listAgentRunFeedback,
    getAgentGoal: mocks.getAgentGoal,
    listAgentGoalDeliveries: mocks.listAgentGoalDeliveries,
    streamAgentGoalEvents: mocks.streamAgentGoalEvents,
    cancelAgentGoal: mocks.cancelAgentGoal,
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

function panel(
  overrides: Partial<ComponentProps<typeof AgentLivePanel>> = {},
) {
  return (
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
      surface="center"
      {...overrides}
    />
  );
}

function renderPanel(overrides: Partial<ComponentProps<typeof AgentLivePanel>> = {}) {
  return render(panel(overrides));
}

describe("workbench public agent live panel", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.listAgentThreads.mockResolvedValue([thread]);
    mocks.listAgentMessages.mockResolvedValue([]);
    mocks.listAgentRunFeedback.mockResolvedValue([]);
    mocks.listAgentGoalDeliveries.mockResolvedValue([]);
    mocks.streamAgentGoalEvents.mockImplementation((_a, _p, _g, _onEvent, signal: AbortSignal) => new Promise((resolve) => {
      signal.addEventListener("abort", () => resolve(0), { once: true });
    }));
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

  it.each(["center", "side"] as const)("keeps the compact composer and in-message stop action on %s", async (surface) => {
    const activeRun = run({ status: "running" });
    mocks.listAgentMessages.mockResolvedValue([message({ body: null, status: "pending", run: activeRun })]);
    mocks.getAgentRun.mockResolvedValue(activeRun);
    mocks.cancelAgentRun.mockResolvedValue(run({ status: "cancelling" }));
    mocks.streamAgentRunEvents.mockImplementation((_a, _p, _r, _onEvent, signal: AbortSignal) => new Promise((resolve) => {
      signal.addEventListener("abort", () => resolve(0), { once: true });
    }));
    renderPanel({ surface });

    const stop = await screen.findByRole("button", { name: "停止回复" });
    const composer = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });
    expect(composer.closest("form")).toHaveAttribute("data-surface", "dock");
    expect(composer).toHaveAttribute("rows", "1");
    expect(screen.getByRole("button", { name: "发送" }).querySelector("svg")).not.toBeNull();
    expect(screen.queryByText("Enter 发送 · Shift+Enter 换行")).not.toBeInTheDocument();
    const record = screen.getByRole("region", { name: "工作记录" });
    expect(within(record).getByRole("button", { name: "停止回复" })).toBe(stop);
    expect(record.closest("article")).toHaveAttribute("data-role", "assistant");
    expect(composer.closest("form")?.parentElement?.querySelector(":scope > [aria-label='Agent 状态']")).toBeNull();
    fireEvent.click(stop);
    await waitFor(() => expect(mocks.cancelAgentRun).toHaveBeenCalledWith(1, 1, 80));
  });

  it("shows an unfinished preview and explicitly submits a replacement with frozen scope", async () => {
    const activeGoal: PublicGoalSession = { goal_id: 12, status: "running", revision: 3,
      waiting_for: "none", active_run_id: 80, active_patch_id: null, can_steer: true,
      can_replace: true, can_follow_up: false, cancellable: true,
      created_at: thread.created_at, updated_at: thread.updated_at };
    mocks.getAgentGoal.mockResolvedValue(activeGoal);
    const running = run({ status: "running", goal_id: 12, goal_revision: 3 });
    mocks.getAgentRun.mockResolvedValue(running);
    mocks.listAgentMessages.mockResolvedValue([message({ status: "pending", body: null, run: running })]);
    mocks.streamAgentRunEvents.mockImplementation((_a, _p, _r, onEvent: (event: PublicAgentEvent) => void, signal: AbortSignal) => {
      onEvent({ sequence: 1, event: "message.preview_started" });
      onEvent({ sequence: 2, event: "message.preview_delta", preview_sequence: 1, offset: 0, text: "先核对目前已知的线索。" });
      return new Promise((resolve) => signal.addEventListener("abort", () => resolve(2), { once: true }));
    });
    mocks.sendAgentMessage.mockResolvedValue({ user_message: message({ message_id: 95, role: "user", body: "改查人物关系", run: null }),
      assistant_message: message({ message_id: 96, body: null, status: "pending", run: null }), goal: activeGoal });
    renderPanel();
    expect(await screen.findByText("生成中，尚未完成校验")).toBeInTheDocument();
    expect(screen.queryByText("目标已完成")).not.toBeInTheDocument();
    const mode = await screen.findByRole("combobox", { name: "运行中消息用途" });
    fireEvent.change(mode, { target: { value: "replace" } });
    fireEvent.change(screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }), { target: { value: "改查人物关系" } });
    const send = screen.getByRole("button", { name: "发送" });
    await waitFor(() => expect(send).toBeEnabled());
    fireEvent.click(send);
    await waitFor(() => expect(mocks.sendAgentMessage).toHaveBeenCalled());
    expect(mocks.sendAgentMessage.mock.calls[0][9]).toEqual({ delivery_mode: "replace", expected_goal_id: 12, expected_goal_revision: 3 });
    expect(mocks.sendAgentMessage.mock.calls[0].slice(3, 5)).toEqual([9, 4]);
  });

  it("requires explicit acknowledgement before resuming a stale goal", async () => {
    mocks.getAgentGoal.mockResolvedValue({ goal_id: 12, status: "stale", revision: 2,
      waiting_for: "stale", active_run_id: null, active_patch_id: null, can_steer: true,
      can_replace: true, can_follow_up: false, cancellable: true,
      created_at: thread.created_at, updated_at: thread.updated_at });
    mocks.listAgentMessages.mockResolvedValue([message({ run: run({ status: "succeeded", goal_id: 12 }) })]);
    renderPanel();
    expect(await screen.findByText("工作稿已变化，需要确认后继续")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }), { target: { value: "继续核对" } });
    expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "基于当前工作稿继续" }));
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
  });

  it("does not lose a kickoff while the candidate context initializes", async () => {
    mocks.sendAgentMessage.mockRejectedValue(new Error("test transport failure"));
    renderPanel({ kickoff: { id: 1, prompt: "检查所选对象" }, focus: { object_ids: ["a"], event_ids: [], validation_issue_ids: [], view: "relations" } });
    await waitFor(() => expect(mocks.sendAgentMessage).toHaveBeenCalledTimes(1));
    expect(mocks.sendAgentMessage.mock.calls[0][7]).toMatchObject({ object_ids: ["a"], view: "relations" });
  });

  it("sends the latest workspace selection without displaying context controls", async () => {
    mocks.sendAgentMessage.mockResolvedValue({
      user_message: message({ message_id: 91, role: "user", body: "检查这里" }),
      assistant_message: message(),
      run: run({ status: "succeeded", cancellable: false }),
    });
    const { rerender } = renderPanel({
      focus: { object_ids: ["a"], event_ids: [], validation_issue_ids: [], view: "relations" },
    });
    const input = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "检查这里" } });
    rerender(panel({
      focus: { object_ids: ["b"], event_ids: [], validation_issue_ids: [], view: "relations" },
    }));
    expect(screen.queryByLabelText("当前上下文")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加上下文" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(mocks.sendAgentMessage).toHaveBeenCalledTimes(1));
    expect(mocks.sendAgentMessage.mock.calls[0][7]).toEqual({
      object_ids: ["b"], event_ids: [], validation_issue_ids: [], view: "relations",
    });
  });

  it("preserves one composer across docking and restores per-thread text without context controls", async () => {
    mocks.listAgentThreads.mockResolvedValue([thread, { ...thread, thread_id: 8, title: "支线" }]);
    const center = document.createElement("div");
    const side = document.createElement("div");
    const threadHost = document.createElement("div");
    document.body.append(center, side, threadHost);
    const props: Partial<ComponentProps<typeof AgentLivePanel>> = {
      presentationHost: center, threadHost, surface: "dock",
      focus: { object_ids: ["a"], event_ids: [], validation_issue_ids: [], view: "relations" },
      referenceLabels: { objects: { a: "人物甲", b: "人物乙" }, events: { e: "事件甲" }, issues: {} },
    };
    const { rerender, unmount } = renderPanel(props);
    const input = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "保留这个草稿" } });
    expect(screen.queryByLabelText("当前上下文")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加上下文" })).not.toBeInTheDocument();
    input.focus();
    rerender(panel({ ...props, surface: "side", presentationHost: side }));
    expect(side.contains(input)).toBe(true);
    expect(screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" })).toBe(input);
    expect(input).toHaveFocus();
    expect(input).toHaveValue("保留这个草稿");
    fireEvent.click(screen.getByRole("button", { name: /主线复查/ }));
    fireEvent.click(screen.getByRole("option", { name: /支线/ }));
    await waitFor(() => expect(input).toHaveValue(""));
    fireEvent.change(input, { target: { value: "支线草稿" } });
    fireEvent.click(screen.getByRole("button", { name: /支线/ }));
    fireEvent.click(screen.getByRole("option", { name: /主线复查/ }));
    await waitFor(() => expect(input).toHaveValue("保留这个草稿"));
    expect(screen.queryByRole("button", { name: "移除上下文 事件甲" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "移除上下文 关系图" })).not.toBeInTheDocument();
    unmount(); center.remove(); side.remove(); threadHost.remove();
  });

  it("keeps Patch review state when a detail is covered and returned to", async () => {
    mocks.listAgentMessages.mockResolvedValue([message({ patch: patch() })]);
    mocks.simulateAgentPatchSet.mockResolvedValue({ patch_id: 200, can_apply: true, blockers: [], warnings: [], requires_author_confirmation: false, confirmation_token: null });
    const host = document.createElement("div"); document.body.append(host);
    const details: NonNullable<ComponentProps<typeof AgentLivePanel>["details"]> = {
      center: { kind: "patch", patchId: 200 }, side: null, centerHost: host, sideHost: null,
      data: { document: null, context: null, issues: [] }, onBack: vi.fn(), onOpen: vi.fn(),
    };
    const { rerender, unmount } = renderPanel({ details });
    fireEvent.click(await screen.findByRole("button", { name: "检查修改影响" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "应用修改" })).toBeEnabled());
    rerender(panel({ details: { ...details, center: { kind: "patch", patchId: 999 } } }));
    expect(screen.getByText(/内容已变化/)).toBeInTheDocument();
    rerender(panel({ details }));
    expect(screen.getByRole("button", { name: "应用修改" })).toBeEnabled();
    expect(mocks.simulateAgentPatchSet).toHaveBeenCalledTimes(1);
    unmount(); host.remove();
  });

  it("shows historical message bodies without author, time or context metadata", async () => {
    mocks.listAgentMessages.mockResolvedValue([
      message({ message_id: 1, sequence: 1, role: "user", body: "旧消息正文", context_snapshot: null }),
      message({ message_id: 2, sequence: 2, role: "user", body: "带快照的消息正文", context_snapshot: {
        draft_id: 9, draft_revision: 3, object_ids: ["deleted-object"], event_ids: [], validation_issue_ids: [], view: null,
      } }),
    ]);
    renderPanel();
    const body = await screen.findByText("旧消息正文");
    expect(screen.getByText("带快照的消息正文")).toBeInTheDocument();
    expect(body.closest("article")?.querySelector("header, time")).toBeNull();
    expect(screen.queryByLabelText("发送时上下文")).not.toBeInTheDocument();
    expect(screen.queryByText("旧消息未记录上下文")).not.toBeInTheDocument();
    expect(screen.queryByText("基于工作稿 R3")).not.toBeInTheDocument();
    expect(screen.queryByText("deleted-object")).not.toBeInTheDocument();
  });

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
    expect(composer.closest("form")).toHaveAttribute("data-surface", "dock");
    fireEvent.change(composer, { target: { value: "检查时间线。" } });
    const sendButton = screen.getByRole("button", { name: "发送" });
    await waitFor(() => expect(sendButton).toBeEnabled());
    fireEvent.click(sendButton);

    expect(await screen.findByText("检查时间线。")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" })).toBe(composer);
    expect(composer.closest("form")).toHaveAttribute("data-surface", "dock");
    expect(screen.queryByLabelText("Agent 回复状态")).not.toBeInTheDocument();
    expect(composer).toHaveAttribute(
      "placeholder",
      "可继续起草下一条消息…",
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
      undefined,
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
    fireEvent.click(within(screen.getByRole("region", { name: "Agent 审阅" })).getByRole("button", { name: "确认应用" }));
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
    fireEvent.click(await within(screen.getByRole("region", { name: "Agent 审阅" })).findByRole("button", { name: "撤销应用" }));
    await waitFor(() =>
      expect(mocks.undoAgentPatchSet).toHaveBeenCalledWith(1, 1, 200, 9, 5),
    );
    mocks.listAgentMessages.mockResolvedValue([
      { ...patchMessage, patch: applied },
    ]);
    fireEvent.click(await within(screen.getByRole("region", { name: "Agent 审阅" })).findByRole("button", { name: "重做应用" }));
    await waitFor(() =>
      expect(mocks.redoAgentPatchSet).toHaveBeenCalledWith(1, 1, 200, 9, 6),
    );
    expect(screen.queryByText(/200|201|entity_internal_handle|R4|patch/i)).not.toBeInTheDocument();
  });

  it.each(["center", "side"] as const)("reviews patches directly in the %s conversation and shares review with details", async (surface) => {
    mocks.listAgentMessages.mockResolvedValue([message({ patch: patch(), response_kind: "patch_proposal" })]);
    mocks.simulateAgentPatchSet.mockResolvedValue({ patch_id: 200, can_apply: true, blockers: [], warnings: [], requires_author_confirmation: false, confirmation_token: null });
    const onFocusPatch = vi.fn();
    renderPanel({ surface, onFocusPatch });
    const conversation = within(screen.getByRole("region", { name: "卷宗统筹 Agent 对话" }));
    const card = within(await conversation.findByRole("article", { name: "修改建议：修改建议" }));
    expect(card.getByText("旧描述")).toBeInTheDocument();
    expect(card.getByText("新描述")).toBeInTheDocument();
    fireEvent.click(card.getByRole("button", { name: "查看细节" }));
    expect(onFocusPatch).toHaveBeenCalledWith(200);
    fireEvent.click(card.getByRole("button", { name: "应用 1 项" }));
    expect(await card.findByRole("button", { name: "确认应用" })).toBeEnabled();
    expect(mocks.applyAgentPatchSet).not.toHaveBeenCalled();
    expect(within(screen.getByRole("region", { name: "Agent 审阅" })).getByRole("button", { name: "确认应用" })).toBeEnabled();
    fireEvent.click(card.getByRole("button", { name: "返回审阅" }));
    expect(screen.queryByRole("button", { name: "确认应用" })).not.toBeInTheDocument();
    const composer = conversation.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });
    fireEvent.change(composer, { target: { value: "保留原有要求" } });
    fireEvent.click(card.getByRole("button", { name: "调整" }));
    expect(composer).toHaveValue("保留原有要求\n\n请调整「修改建议」这组修改建议：");
    expect(mocks.sendAgentMessage).not.toHaveBeenCalled();
  });

  it("reports an inline check failure without applying or losing the proposal", async () => {
    mocks.listAgentMessages.mockResolvedValue([message({ patch: patch() })]);
    mocks.simulateAgentPatchSet.mockRejectedValue(new Error("工作稿已变化"));
    renderPanel();
    const conversation = within(screen.getByRole("region", { name: "卷宗统筹 Agent 对话" }));
    fireEvent.click(await conversation.findByRole("button", { name: "应用 1 项" }));
    expect(await conversation.findByRole("alert")).toHaveTextContent("工作稿已变化");
    expect(conversation.getByText("新描述")).toBeInTheDocument();
    expect(conversation.queryByRole("button", { name: "确认应用" })).not.toBeInTheDocument();
    expect(mocks.applyAgentPatchSet).not.toHaveBeenCalled();
  });
});
