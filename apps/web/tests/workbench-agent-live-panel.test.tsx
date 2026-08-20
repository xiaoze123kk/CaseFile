import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentLivePanel } from "@/features/analyst-workbench/workbench-agent-live-panel";
import {
  ApiError,
  type AgentChatFocus,
  type AgentMessageView,
  type AgentPatchSetView,
  type AgentThreadView,
  type TaskView,
} from "@/lib/api-client";

const mocks = vi.hoisted(() => ({
  applyAgentPatchSet: vi.fn(),
  cancelTask: vi.fn(),
  createAgentThread: vi.fn(),
  listAgentMessages: vi.fn(),
  listAgentThreads: vi.fn(),
  sendAgentMessage: vi.fn(),
  sendAgentRoutingFeedback: vi.fn(),
  undoAgentPatchSet: vi.fn(),
  updateAgentThread: vi.fn(),
  waitForTask: vi.fn(),
}));

const locateMocks = vi.hoisted(() => ({
  event: vi.fn(),
  issue: vi.fn(),
  object: vi.fn(),
  view: vi.fn(),
}));

const onDraftChangedMock = vi.fn(() => Promise.resolve());

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    applyAgentPatchSet: mocks.applyAgentPatchSet,
    createAgentThread: mocks.createAgentThread,
    listAgentMessages: mocks.listAgentMessages,
    listAgentThreads: mocks.listAgentThreads,
    sendAgentMessage: mocks.sendAgentMessage,
    sendAgentRoutingFeedback: mocks.sendAgentRoutingFeedback,
    undoAgentPatchSet: mocks.undoAgentPatchSet,
    updateAgentThread: mocks.updateAgentThread,
  };
});

vi.mock("@/features/case-session/case-session-api", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/features/case-session/case-session-api")
  >();
  return {
    ...actual,
    cancelTask: mocks.cancelTask,
    waitForTask: mocks.waitForTask,
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {
  mocks.waitForTask.mockResolvedValue(makeTask({ status: "succeeded" }));
});

function makeTask(overrides: Partial<TaskView> = {}): TaskView {
  return {
    task_run_id: 77,
    project_id: 1,
    task_type: "casefile_chat",
    status: "queued",
    stage: "queued",
    provider: "deepseek",
    model_id: "chat-model",
    input_draft_revision: 3,
    input_brief_revision: null,
    input_source_record_id: null,
    input_brief_intake_id: null,
    input_brief_intake_revision: null,
    base_brief_intake_candidate_id: null,
    agent_thread_id: 11,
    input_message_id: 1,
    output_message_id: 2,
    input_hash: "hash",
    candidate_strategy: null,
    attempt_count: 1,
    usage: {},
    result_snapshot_id: null,
    result: null,
    error_code: null,
    failure: null,
    component_steps: [],
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

function makeThread(overrides: Partial<AgentThreadView> = {}): AgentThreadView {
  return {
    thread_id: 11,
    title: "主对话",
    title_source: "auto",
    is_pinned: false,
    status: "active",
    last_message_at: null,
    created_at: "2026-08-16T08:00:00Z",
    updated_at: "2026-08-16T08:00:00Z",
    ...overrides,
  };
}

function makeMessage(overrides: Partial<AgentMessageView>): AgentMessageView {
  return {
    message_id: 1,
    thread_id: 11,
    sequence_no: 1,
    role: "user",
    status: "completed",
    content: "请检查这份卷宗。",
    task: null,
    referenced_object_ids: [],
    referenced_event_ids: [],
    referenced_validation_issue_ids: [],
    suggested_view: null,
    patch_set: null,
    created_at: "2026-08-16T08:01:00Z",
    updated_at: "2026-08-16T08:01:00Z",
    ...overrides,
  };
}

function makePatchSet(
  overrides: Partial<AgentPatchSetView> = {},
): AgentPatchSetView {
  return {
    patch_set_id: 200,
    thread_id: 11,
    source_message_id: 2,
    task_run_id: 77,
    base_draft_revision: 3,
    reason_summary: "逐项整理卷宗修改建议。",
    status: "pending",
    is_stale: false,
    applied_from_revision: null,
    applied_to_revision: null,
    undone_to_revision: null,
    operations: [
      {
        operation_id: 201,
        operation_key: "person-name-update",
        ordinal: 1,
        object_id: "object:person_1",
        object_type: "entity",
        operation_type: "field_update",
        field_path: "/name",
        expected_object_revision: 2,
        old_value: "旧名称",
        new_value: "研究员",
        reason: "明确人物职责。",
        decision: "pending",
        reviewed_at: null,
      },
      {
        operation_id: 202,
        operation_key: "location-description-update",
        ordinal: 2,
        object_id: "object:loc_1",
        object_type: "location",
        operation_type: "field_update",
        field_path: "/description",
        expected_object_revision: 2,
        old_value: "旧描述",
        new_value: "新描述",
        reason: "补充地点信息。",
        decision: "pending",
        reviewed_at: null,
      },
    ],
    validation_warning: false,
    validator_issues: [],
    created_at: "2026-08-16T09:00:00Z",
    updated_at: "2026-08-16T09:00:00Z",
    ...overrides,
  };
}

function makeFocus(overrides: Partial<AgentChatFocus> = {}): AgentChatFocus {
  return {
    object_ids: ["object:res_core"],
    event_ids: ["event:ev_1"],
    validation_issue_ids: ["validator:issue-1"],
    view: "timeline",
    ...overrides,
  };
}

function renderPanel(
  options: {
    kickoff?: {
      id: number;
      prompt: string;
      routingHint?: { entrypoint: "issue_action" };
    } | null;
  } = {},
) {
  return render(
    <AgentLivePanel
      draftId={9}
      draftRevision={3}
      focus={makeFocus()}
      kickoff={options.kickoff ?? null}
      onClose={vi.fn()}
      onDraftChanged={onDraftChangedMock}
      onLocateEvent={locateMocks.event}
      onLocateIssue={locateMocks.issue}
      onLocateObject={locateMocks.object}
      onLocateView={locateMocks.view}
      projectId={1}
      referenceLabels={{
        objects: { "object:person_1": "研究员" },
        events: { "event:known": "重启事件" },
        issues: { "validator:issue-1": "关键主张缺少支撑" },
      }}
    />,
  );
}

describe("workbench agent live panel", () => {
  it("restores an existing thread and its message history", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({ message_id: 1, content: "请检查这份卷宗。" }),
      makeMessage({
        message_id: 2,
        sequence_no: 2,
        role: "assistant",
        content: "检查完成，没有发现问题。",
        task: makeTask({ status: "succeeded" }),
      }),
    ]);

    renderPanel();

    expect(await screen.findByText("请检查这份卷宗。")).toBeInTheDocument();
    expect(
      await screen.findByText("检查完成，没有发现问题。"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /主对话/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
    ).not.toBeDisabled();
  });

  it("persists Desk thread actions and disables the composer after archiving", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([]);
    mocks.updateAgentThread.mockImplementation(
      async (
        _actorId: number,
        _projectId: number,
        _threadId: number,
        _draftId: number,
        _draftRevision: number,
        changes: { title?: string; is_pinned?: boolean; archived?: boolean },
      ) =>
        makeThread({
          ...(changes.title === undefined ? {} : { title: changes.title }),
          ...(changes.is_pinned === undefined
            ? {}
            : { is_pinned: changes.is_pinned }),
          ...(changes.archived === undefined
            ? {}
            : { status: changes.archived ? "archived" : "active" }),
        }),
    );

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /主对话/ }));
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByRole("textbox", { name: "对话标题" }), {
      target: { value: "时间线复核" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() =>
      expect(mocks.updateAgentThread).toHaveBeenCalledWith(
        1,
        1,
        11,
        9,
        3,
        { title: "时间线复核" },
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() =>
      expect(mocks.updateAgentThread).toHaveBeenLastCalledWith(
        1,
        1,
        11,
        9,
        3,
        { archived: true },
      ),
    );
    expect(
      screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
    ).toBeDisabled();
  });

  it("renders the routing chip and submits one route-error correction", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.sendAgentRoutingFeedback.mockResolvedValue({
      message_id: 2,
      task_run_id: 77,
      acknowledged: true,
    });
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "这是分析结论。",
        task: makeTask({
          status: "succeeded",
          result: {
            answer: "这是分析结论。",
            referenced_object_ids: [],
            referenced_event_ids: [],
            referenced_validation_issue_ids: [],
            suggested_view: null,
            patch_set_id: null,
            stale: false,
            routing: {
              route_source: "llm",
              intent: "analysis",
              route_hash: "h",
            },
          },
        }),
      }),
    ]);

    renderPanel();

    expect(await screen.findByText("AI 理解 · 分析")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "路由错误" }));
    fireEvent.change(screen.getByRole("combobox", { name: "正确的意图" }), {
      target: { value: "question" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() =>
      expect(mocks.sendAgentRoutingFeedback).toHaveBeenCalledWith(
        1,
        1,
        11,
        2,
        "question",
      ),
    );
    expect(await screen.findByText("已记录反馈：问答")).toBeInTheDocument();
  });

  it("renders the logic audit chip and keeps its PatchSet reviewable", async () => {
    const pending = makePatchSet();
    const applied = makePatchSet({
      status: "applied",
      applied_from_revision: 3,
      applied_to_revision: 4,
      operations: pending.operations.map((operation) => ({
        ...operation,
        decision: "accepted",
        reviewed_at: "2026-08-16T09:10:00Z",
      })),
    });
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValueOnce([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "审计报告：发现一处描述缺口。",
        task: makeTask({
          status: "succeeded",
          result: {
            answer: "审计报告：发现一处描述缺口。",
            referenced_object_ids: [],
            referenced_event_ids: [],
            referenced_validation_issue_ids: [],
            suggested_view: null,
            patch_set_id: null,
            stale: false,
            routing: {
              route_source: "rule_preset",
              intent: "logic_audit",
              route_hash: "h",
            },
          },
        }),
        patch_set: pending,
      }),
    ]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "审计报告：发现一处描述缺口。",
        task: makeTask({ status: "succeeded" }),
        patch_set: applied,
      }),
    ]);
    mocks.applyAgentPatchSet.mockResolvedValue({
      ...applied,
      draft_revision: 4,
    });

    renderPanel();

    expect(
      await screen.findByText("预设路由 · 逻辑漏洞复查"),
    ).toBeInTheDocument();
    expect(screen.queryByText("修改建议")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    expect(await screen.findByText("修改建议")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "选择修改 研究员 /name" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "采纳所选（1）" }),
    );

    await waitFor(() => {
      expect(mocks.applyAgentPatchSet).toHaveBeenCalledWith(
        1,
        1,
        200,
        9,
        3,
        [201],
      );
    });
    expect(onDraftChangedMock).toHaveBeenCalled();
  });

  it("renders structured audit findings with clickable evidence", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 3,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "复查完成，发现一处矛盾。",
        task: makeTask({
          status: "succeeded",
          result: {
            answer: "复查完成，发现一处矛盾。",
            referenced_object_ids: ["object:person_1"],
            referenced_event_ids: ["event:known"],
            referenced_validation_issue_ids: ["validator:issue-1"],
            suggested_view: null,
            patch_set_id: null,
            stale: false,
            audit_findings: [
              {
                finding_id: "F1",
                kind: "contradiction",
                severity: "S1",
                title: "研究员描述前后矛盾",
                statement: "第一段说研究员支持重启，第二段又说反对。",
                needs_manual_review: false,
                evidence_object_ids: ["object:person_1"],
                evidence_event_ids: ["event:known"],
                evidence_validation_issue_ids: ["validator:issue-1"],
              },
              {
                finding_id: "F2",
                kind: "motivation_gap",
                severity: "S3",
                title: "动机不明",
                statement: "重启原因在材料中缺少直接说明。",
                needs_manual_review: true,
                evidence_object_ids: [],
                evidence_event_ids: [],
                evidence_validation_issue_ids: [],
              },
            ],
            routing: {
              route_source: "rule_preset",
              intent: "logic_audit",
              route_hash: "h",
            },
          },
        }),
      }),
    ]);

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "验证发现 2" }));
    expect(
      await screen.findByRole("article", { name: "逻辑漏洞复查发现" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("研究员描述前后矛盾")).toBeInTheDocument();
    expect(screen.getByText("矛盾")).toBeInTheDocument();
    expect(screen.getByText("致命")).toBeInTheDocument();
    expect(screen.getByText("待人工确认")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "对象 · 研究员" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "事件 · 重启事件" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "验证 · 关键主张缺少支撑" }),
    );

    expect(locateMocks.object).toHaveBeenCalledWith("object:person_1");
    expect(locateMocks.event).toHaveBeenCalledWith("event:known");
    expect(locateMocks.issue).toHaveBeenCalledWith("validator:issue-1");
  });

  it("renders four kinds of clickable references and routes them into the workbench", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 8,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "请看研究员、重启事件和这条验证问题。",
        task: makeTask({ status: "succeeded" }),
        referenced_object_ids: ["object:person_1"],
        referenced_event_ids: ["event:known"],
        referenced_validation_issue_ids: ["validator:issue-1"],
        suggested_view: "timeline",
      }),
    ]);

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "引用 4" }));
    fireEvent.click(screen.getByRole("button", { name: "对象 · 研究员" }));
    fireEvent.click(
      screen.getByRole("button", { name: "事件 · 重启事件" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "验证 · 关键主张缺少支撑" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "视图 · 时间线" }),
    );

    expect(locateMocks.object).toHaveBeenCalledWith("object:person_1");
    expect(locateMocks.event).toHaveBeenCalledWith("event:known");
    expect(locateMocks.issue).toHaveBeenCalledWith("validator:issue-1");
    expect(locateMocks.view).toHaveBeenCalledWith("timeline");
  });

  it("sends each real preset instruction with its frozen routing hint", async () => {
    const presets = [
      {
        label: "全卷宗体检",
        presetId: "inspect",
        prompt:
          "对整个卷宗做一次体检：按验证问题的严重程度分级列出待处理问题，并说明时间线与推理的收束情况。",
      },
      {
        label: "证据链摘要",
        presetId: "evidence",
        prompt:
          "汇总当前证据链：每份关键证据支撑了哪些主张，支撑不完整或存在断点的地方请如实指出。",
      },
      {
        label: "候选解释对比",
        presetId: "compare",
        prompt:
          "对比卷宗中实际存在的候选解释与推理路径的收束状态，指出仍存在竞争的解释。",
      },
      {
        label: "导出前检查",
        presetId: "gate",
        prompt:
          "按编译中心的发布门禁口径做导出前检查，结论必须与验证快照一致。",
      },
      {
        label: "逻辑漏洞复查",
        presetId: "audit",
        prompt:
          "对当前卷宗做一次全卷逻辑漏洞复查：找出矛盾、断链、时序错误和动机缺口；能给出可审阅补丁的就给出补丁，无法取证的列到待人工确认，未发现漏洞则如实说明。",
      },
    ];
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([]);
    mocks.sendAgentMessage.mockResolvedValue({
      thread: makeThread(),
      user_message: makeMessage({
        message_id: 3,
        sequence_no: 1,
        content: "预设指令",
      }),
      assistant_message: makeMessage({
        message_id: 4,
        sequence_no: 2,
        role: "assistant",
        status: "pending",
        content: null,
        task: makeTask({ status: "queued" }),
      }),
      task: makeTask({ status: "queued" }),
    });

    for (const preset of presets) {
      cleanup();
      renderPanel();

      fireEvent.click(
        await screen.findByRole("button", { name: preset.label }),
      );

      await waitFor(() => {
        expect(mocks.sendAgentMessage).toHaveBeenLastCalledWith(
          1,
          1,
          11,
          9,
          3,
          preset.prompt,
          "deepseek",
          makeFocus(),
          { entrypoint: "preset", preset_id: preset.presetId },
        );
      });
    }
  });

  it("sends an issue-driven kickoff once the Agent thread is ready", async () => {
    mocks.listAgentThreads.mockResolvedValue([]);
    mocks.createAgentThread.mockResolvedValue(
      makeThread({ title: "新对话", title_source: "auto" }),
    );
    mocks.listAgentMessages.mockResolvedValue([]);
    mocks.sendAgentMessage.mockResolvedValue({
      thread: makeThread(),
      user_message: makeMessage({
        message_id: 3,
        sequence_no: 1,
        content: "请处理当前焦点中的验证问题。",
      }),
      assistant_message: makeMessage({
        message_id: 4,
        sequence_no: 2,
        role: "assistant",
        status: "pending",
        content: null,
        task: makeTask({ status: "queued" }),
      }),
      task: makeTask({ status: "queued" }),
    });

    renderPanel({
      kickoff: {
        id: 42,
        prompt: "请处理当前焦点中的验证问题。",
        routingHint: { entrypoint: "issue_action" },
      },
    });

    await waitFor(() => {
      expect(mocks.sendAgentMessage).toHaveBeenCalledWith(
        1,
        1,
        11,
        9,
        3,
        "请处理当前焦点中的验证问题。",
        "deepseek",
        makeFocus(),
        { entrypoint: "issue_action" },
      );
    });
  });

  it("creates a first thread when the Draft has no Agent conversation yet", async () => {
    mocks.listAgentThreads.mockResolvedValue([]);
    mocks.createAgentThread.mockResolvedValue(
      makeThread({ title: "新对话", title_source: "auto" }),
    );
    mocks.listAgentMessages.mockResolvedValue([]);

    renderPanel();

    expect(
      await screen.findByText("从上方预设指令或输入框开始布置卷宗任务。"),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(mocks.createAgentThread).toHaveBeenCalledWith(1, 1, 9, 3);
    });
  });

  it("shows an explicit offline fallback instead of a blank panel and can reconnect", async () => {
    mocks.listAgentThreads.mockRejectedValueOnce(
      new ApiError(503, {
        code: "database_unavailable",
        message: "数据库暂时不可用，请稍后重试。",
        details: {},
      }),
    );

    renderPanel();

    expect(await screen.findByText("无法连接 Agent")).toBeInTheDocument();
    expect(
      screen.getByText("数据库暂时不可用，请稍后重试。"),
    ).toBeInTheDocument();

    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({ content: "恢复后还能看到历史。" }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "重新连接" }));

    expect(await screen.findByText("恢复后还能看到历史。")).toBeInTheDocument();
    expect(
      screen.queryByText("无法连接 Agent"),
    ).not.toBeInTheDocument();
  });

  it("sends against the frozen Draft revision, disables input while busy, and renders the completed reply", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([]);
    let resolveWait: (task: TaskView) => void = () => undefined;
    mocks.waitForTask.mockImplementation(
      (_projectId, _taskRunId, onTick) => {
        onTick?.(
          makeTask({ status: "running", stage: "reading_case" }),
        );
        return new Promise<TaskView>((resolve) => {
          resolveWait = resolve;
        });
      },
    );
    mocks.sendAgentMessage.mockResolvedValue({
      thread: makeThread(),
      user_message: makeMessage({
        message_id: 3,
        sequence_no: 1,
        content: "帮我检查对象。",
      }),
      assistant_message: makeMessage({
        message_id: 4,
        sequence_no: 2,
        role: "assistant",
        status: "pending",
        content: null,
        task: makeTask({ status: "queued" }),
      }),
      task: makeTask({ status: "queued" }),
    });

    renderPanel();
    const input = screen.getByRole("textbox", {
      name: "给卷宗统筹 Agent 的指令",
    });
    await waitFor(() => expect(input).not.toBeDisabled());
    fireEvent.change(input, {
      target: { value: "帮我检查对象。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("帮我检查对象。")).toBeInTheDocument();
    expect(
      await screen.findByText("Agent 正在回复 · reading_case"),
    ).toBeInTheDocument();
    expect(input).toBeDisabled();
    expect(mocks.sendAgentMessage).toHaveBeenCalledWith(
      1,
      1,
      11,
      9,
      3,
      "帮我检查对象。",
      "deepseek",
      makeFocus(),
      { entrypoint: "free_text" },
    );

    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 3,
        sequence_no: 1,
        content: "帮我检查对象。",
      }),
      makeMessage({
        message_id: 4,
        sequence_no: 2,
        role: "assistant",
        status: "completed",
        content: "检查完成。",
        task: makeTask({ status: "succeeded" }),
      }),
    ]);
    await act(async () => {
      resolveWait(makeTask({ status: "succeeded" }));
    });

    expect(await screen.findByText("检查完成。")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
      ).not.toBeDisabled();
    });
  });

  it("shows a failed reply with the service failure and retries the same instruction", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 5,
        sequence_no: 1,
        content: "重写对象描述。",
      }),
      makeMessage({
        message_id: 6,
        sequence_no: 2,
        role: "assistant",
        status: "failed",
        content: null,
        task: makeTask({
          status: "failed",
          failure: {
            code: "provider_connection_failed",
            message: "无法连接模型服务。",
            retryable: true,
            issues: [],
          },
        }),
      }),
    ]);
    mocks.sendAgentMessage.mockResolvedValue({
      thread: makeThread(),
      user_message: makeMessage({
        message_id: 7,
        sequence_no: 3,
        content: "重写对象描述。",
      }),
      assistant_message: makeMessage({
        message_id: 8,
        sequence_no: 4,
        role: "assistant",
        status: "pending",
        content: null,
        task: makeTask({ status: "queued" }),
      }),
      task: makeTask({ status: "queued" }),
    });

    renderPanel();
    expect(await screen.findByText("回复失败")).toBeInTheDocument();
    expect(screen.getByText("无法连接模型服务。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => {
      expect(mocks.sendAgentMessage).toHaveBeenCalledWith(
        1,
        1,
        11,
        9,
        3,
        "重写对象描述。",
        "deepseek",
        makeFocus(),
        { entrypoint: "free_text" },
      );
    });
  });

  it("reviews a pending PatchSet, applies selected operations and refreshes the Draft", async () => {
    const pending = makePatchSet();
    const applied = makePatchSet({
      status: "applied",
      applied_from_revision: 3,
      applied_to_revision: 4,
      operations: pending.operations.map((operation, index) => ({
        ...operation,
        decision: index === 0 ? "accepted" : "rejected",
        reviewed_at: "2026-08-16T09:10:00Z",
      })),
    });
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValueOnce([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "建议如下。",
        task: makeTask({ status: "succeeded" }),
        patch_set: pending,
      }),
    ]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "建议如下。",
        task: makeTask({ status: "succeeded" }),
        patch_set: applied,
      }),
    ]);
    mocks.applyAgentPatchSet.mockResolvedValue({
      ...applied,
      draft_revision: 4,
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    expect(await screen.findByText("修改建议")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "选择修改 研究员 /name" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "采纳所选（1）" }),
    );

    await waitFor(() => {
      expect(mocks.applyAgentPatchSet).toHaveBeenCalledWith(
        1,
        1,
        200,
        9,
        3,
        [201],
      );
    });
    expect(
      await screen.findByRole("button", { name: "撤销应用" }),
    ).toBeInTheDocument();
    expect(onDraftChangedMock).toHaveBeenCalled();
    expect(mocks.listAgentMessages).toHaveBeenCalledTimes(2);
  });

  it("undoes an applied PatchSet against its applied revision", async () => {
    const applied = makePatchSet({
      status: "applied",
      applied_from_revision: 3,
      applied_to_revision: 4,
      operations: makePatchSet().operations.map((operation) => ({
        ...operation,
        decision: "accepted",
        reviewed_at: "2026-08-16T09:10:00Z",
      })),
    });
    const undone = makePatchSet({
      status: "undone",
      undone_to_revision: 3,
      operations: applied.operations,
    });
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValueOnce([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "已应用的建议。",
        task: makeTask({ status: "succeeded" }),
        patch_set: applied,
      }),
    ]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "已应用的建议。",
        task: makeTask({ status: "succeeded" }),
        patch_set: undone,
      }),
    ]);
    mocks.undoAgentPatchSet.mockResolvedValue({
      ...undone,
      draft_revision: 3,
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    fireEvent.click(screen.getByRole("button", { name: "撤销应用" }));

    await waitFor(() => {
      expect(mocks.undoAgentPatchSet).toHaveBeenCalledWith(1, 1, 200, 9, 4);
    });
    expect(await screen.findByText("已撤销")).toBeInTheDocument();
    expect(onDraftChangedMock).toHaveBeenCalled();
  });

  it("locates a PatchSet target object in the workbench before reviewing", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "建议如下。",
        task: makeTask({ status: "succeeded" }),
        patch_set: makePatchSet(),
      }),
    ]);

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    fireEvent.click(screen.getByRole("button", { name: "定位对象 研究员" }));
    expect(locateMocks.object).toHaveBeenCalledWith("object:person_1");
  });

  it("requires an explicit confirmation before rejecting every operation", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "建议如下。",
        task: makeTask({ status: "succeeded" }),
        patch_set: makePatchSet(),
      }),
    ]);
    mocks.applyAgentPatchSet.mockResolvedValue({
      ...makePatchSet({ status: "rejected" }),
      draft_revision: 3,
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    fireEvent.click(screen.getByRole("button", { name: "全部拒绝" }));
    expect(mocks.applyAgentPatchSet).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认拒绝" }));

    await waitFor(() => {
      expect(mocks.applyAgentPatchSet).toHaveBeenCalledWith(1, 1, 200, 9, 3, []);
    });
  });

  it("expands nonblocking validator warnings inside the PatchSet review", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 2,
        sequence_no: 1,
        role: "assistant",
        status: "completed",
        content: "建议如下。",
        task: makeTask({ status: "succeeded" }),
        patch_set: makePatchSet({
          validation_warning: true,
          validator_issues: [
            {
              rule_id: "CF-W-CLAIM-001",
              severity: "S1",
              title: "关键主张缺少支撑信息",
              message: "该关键主张被标记为已支持，但尚未关联任何支撑信息。",
              object_refs: [],
              field_path: "/support_refs",
            },
          ],
        }),
      }),
    ]);

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    fireEvent.click(screen.getByRole("button", { name: "查看验证警告（1）" }));
    expect(await screen.findByText("关键主张缺少支撑信息")).toBeInTheDocument();
    expect(screen.getByText("CF-W-CLAIM-001")).toBeInTheDocument();
    expect(
      screen.getByText("该关键主张被标记为已支持，但尚未关联任何支撑信息。"),
    ).toBeInTheDocument();
  });

  it("re-requests a stale PatchSet with its original instruction", async () => {
    mocks.listAgentThreads.mockResolvedValue([makeThread()]);
    mocks.listAgentMessages.mockResolvedValue([
      makeMessage({
        message_id: 5,
        sequence_no: 1,
        content: "请重新审计卷宗。",
      }),
      makeMessage({
        message_id: 6,
        sequence_no: 2,
        role: "assistant",
        status: "completed",
        content: "旧草稿上的建议。",
        task: makeTask({ status: "succeeded" }),
        patch_set: makePatchSet({ status: "stale", is_stale: true }),
      }),
    ]);
    mocks.sendAgentMessage.mockResolvedValue({
      thread: makeThread(),
      user_message: makeMessage({
        message_id: 7,
        sequence_no: 3,
        content: "请重新审计卷宗。",
      }),
      assistant_message: makeMessage({
        message_id: 8,
        sequence_no: 4,
        role: "assistant",
        status: "pending",
        content: null,
        task: makeTask({ status: "queued" }),
      }),
      task: makeTask({ status: "queued" }),
    });

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "待审修改 2" }));
    fireEvent.click(screen.getByRole("button", { name: "重新生成建议" }));

    await waitFor(() => {
      expect(mocks.sendAgentMessage).toHaveBeenCalledWith(
        1,
        1,
        11,
        9,
        3,
        "请重新审计卷宗。",
        "deepseek",
        makeFocus(),
        { entrypoint: "free_text" },
      );
    });
  });
});
