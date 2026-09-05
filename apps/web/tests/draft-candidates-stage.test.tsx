import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DraftCandidateView, TaskView } from "@/lib/api-client";
import { buildWorkbenchCandidates } from "@/features/analyst-workbench/analyst-fixture";
import {
  createInitialCaseSessionState,
  type CandidateSlotStrategy,
  type CaseSessionState,
} from "@/features/case-session/case-session-provider";
import { mapWorkbenchCandidateView } from "@/features/case-session/case-session-mapping";
import {
  DraftCandidatesStage,
  formatCandidateCompletedAt,
} from "@/features/intake/draft-candidates-stage";

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
  useCaseSession: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

vi.mock("@/features/case-session/case-session-provider", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@/features/case-session/case-session-provider")
  >()),
  useCaseSession: mocks.useCaseSession,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("draft candidate cancellation feedback", () => {
  it("shows the complete six-step pipeline while the first component is still pending", () => {
    installSession(generatingState());

    render(<DraftCandidatesStage />);

    const pipeline = screen.getByLabelText("深稿生成部件进度");
    expect(pipeline).toHaveAttribute("data-active", "true");
    expect(within(pipeline).getByTestId("pipeline-signal")).toBeInTheDocument();
    expect(within(pipeline).getByText("六步生成流水线")).toBeInTheDocument();
    expect(within(pipeline).getAllByText("正在创建任务")).toHaveLength(2);
    expect(within(pipeline).getByText("已完成 0 / 6 步")).toBeInTheDocument();
    expect(within(pipeline).getByRole("progressbar", { name: "六步生成进度" })).toHaveAttribute("aria-valuenow", "0");
    expect(pipeline.querySelectorAll(":scope > ol > li")).toHaveLength(6);
    expect(
      Array.from(
        pipeline.querySelectorAll(":scope > ol > li > div > small"),
        (item) => item.textContent,
      ),
    ).toEqual(Array(6).fill("等待"));
    expect(within(pipeline).getByText("时间结构规划 · 等待")).toBeInTheDocument();
  });

  it.each([
    [1, ["context_pack_builder"], "正在进行第 2 步：案件蓝图规划"],
    [3, ["context_pack_builder", "case_blueprint_planner", "story_world", "evidence_logic", "temporal_structure_planner", "resolution_governance"], "正在进行第 4 步：引用链接"],
    [5, ["context_pack_builder", "case_blueprint_planner", "story_world", "evidence_logic", "temporal_structure_planner", "resolution_governance", "reference_linker", "casefile_compiler"], "正在进行第 6 步：质量与修复门禁"],
  ] as const)("shows %i completed top-level steps from real component state", (completed, componentIds, currentLabel) => {
    const state = generatingState();
    const task = state.generation.slots.structure_first.latestTask!;
    task.component_steps = componentIds.map((componentId, index) => componentStep(componentId, "succeeded", index + 1));
    const runningComponent = completed === 1 ? "case_blueprint_planner" : completed === 3 ? "reference_linker" : "quality_repair_gate";
    task.component_steps.push(componentStep(runningComponent, "running", 20));
    installSession(state);

    render(<DraftCandidatesStage />);

    const pipeline = screen.getByLabelText("深稿生成部件进度");
    expect(within(pipeline).getByText(`已完成 ${completed} / 6 步`)).toBeInTheDocument();
    expect(within(pipeline).getByText(currentLabel)).toBeInTheDocument();
    expect(within(pipeline).getByRole("progressbar")).toHaveAttribute("aria-valuenow", String(completed));
  });

  it("counts all four domain drafters before completing the third step", () => {
    const state = generatingState();
    const task = state.generation.slots.structure_first.latestTask!;
    task.component_steps = [
      componentStep("context_pack_builder", "succeeded", 1),
      componentStep("case_blueprint_planner", "succeeded", 2),
      componentStep("story_world", "succeeded", 3),
      componentStep("evidence_logic", "succeeded", 4),
      componentStep("temporal_structure_planner", "succeeded", 5),
      componentStep("resolution_governance", "running", 6),
    ];
    installSession(state);

    render(<DraftCandidatesStage />);

    expect(screen.getByText("三域创作 · 已完成 3 / 4")).toBeInTheDocument();
    expect(screen.getByText("已完成 2 / 6 步")).toBeInTheDocument();
  });

  it("shows the newest failed execution instead of an earlier repaired gate", () => {
    const state = generatingState(false);
    const task = generationTask("failed");
    task.component_steps = [
      failedStep({
        stepRunId: 901,
        componentId: "quality_repair_gate",
        executionNo: 5,
        message: "早期质量门禁失败",
      }),
      failedStep({
        stepRunId: 902,
        componentId: "temporal_structure_planner",
        executionNo: 2,
        message: "分钟精度不能包含秒",
      }),
    ];
    state.generation.slots.structure_first = {
      status: "failed",
      stage: "failed",
      taskRunId: task.task_run_id,
      attempt: 1,
      error: task.failure?.message ?? null,
      latestTask: task,
    };
    installSession(state);

    render(<DraftCandidatesStage />);

    expect(screen.getByText("时间结构规划执行失败")).toBeInTheDocument();
    expect(screen.getByText("/assignments/0/time/value · 分钟精度不能包含秒")).toBeInTheDocument();
    expect(screen.queryByText("早期质量门禁失败")).not.toBeInTheDocument();
  });

  it("keeps an in-flight recoverable gate failure as auto-repair instead of a failure alert", () => {
    const state = generatingState(true);
    const task = state.generation.slots.structure_first.latestTask!;
    task.component_steps = [
      failedStep({
        stepRunId: 901,
        componentId: "quality_repair_gate",
        executionNo: 1,
        message: "候选未通过质量门禁。",
      }),
    ];
    installSession(state);

    render(<DraftCandidatesStage />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("质量与修复门禁未通过")).toBeInTheDocument();
    expect(screen.getByText("未达到重试上限，正在自动修复，无需操作。")).toBeInTheDocument();
    expect(screen.getByText("第 6 步未通过，正在自动修复…")).toBeInTheDocument();
    expect(screen.getByText("修复中")).toBeInTheDocument();
  });

  it("keeps the failure alert when an active failure has exhausted its recovery budget", () => {
    const state = generatingState(true);
    const task = state.generation.slots.structure_first.latestTask!;
    task.component_steps = [
      {
        ...failedStep({
          stepRunId: 901,
          componentId: "quality_repair_gate",
          executionNo: 2,
          message: "修复预算耗尽",
        }),
        recoverable: false,
      },
    ];
    installSession(state);

    render(<DraftCandidatesStage />);

    expect(screen.getByRole("alert")).toHaveTextContent("质量与修复门禁执行失败");
    expect(screen.queryByText("未达到重试上限，正在自动修复，无需操作。")).not.toBeInTheDocument();
  });

  it("uses retryable task diagnostics when a legacy coordinator step is not recoverable", () => {
    const state = generatingState(false);
    const task = generationTask("failed");
    task.error_code = "agent_component_failed";
    task.failure = {
      code: "agent_component_failed",
      message: "深稿生成部件未通过门禁，可从失败阶段恢复。",
      retryable: true,
      issues: [
        {
          code: "competing_hypothesis_path_missing",
          path: "/reasoning_paths/hypothesis_b",
          message: "竞争假设缺少使用信息输入的对应推理路径",
        },
      ],
    };
    task.component_steps = [
      {
        step_run_id: 901,
        attempt_no: 1,
        component_id: "run_coordinator",
        parent_component_id: null,
        execution_no: 1,
        status: "failed",
        schema_id: "task-run-v1",
        input_hash: "coordinator-input",
        output_hash: null,
        failure_layer: "frozen_context",
        issues: [
          {
            component_id: "run_coordinator",
            failure_layer: "frozen_context",
            schema_id: "task-run-v1",
            code: "candidate_validation_failed",
            path: "",
            message: "CaseFile contract validation failed",
          },
        ],
        recoverable: false,
        resumed_from_step_run_id: null,
      },
    ];
    state.generation.slots.structure_first = {
      status: "failed",
      stage: "failed",
      taskRunId: task.task_run_id,
      attempt: 1,
      error: task.failure.message,
      latestTask: task,
    };
    const resumeGeneration = vi.fn().mockResolvedValue(true);
    installSession(state, { resumeGeneration });

    render(<DraftCandidatesStage />);

    expect(screen.getByText("/reasoning_paths/hypothesis_b · 竞争假设缺少使用信息输入的对应推理路径")).toBeInTheDocument();
    expect(screen.queryByText("CaseFile contract validation failed")).not.toBeInTheDocument();
    const resume = screen.getByRole("button", { name: "从失败阶段恢复" });
    expect(resume).toBeEnabled();
    fireEvent.click(resume);
    expect(resumeGeneration).toHaveBeenCalledWith("structure_first");
  });

  it.each([
    [
      "cancelling",
      "已请求安全停止；Worker 会结束当前步骤，Current Draft 不会改变。",
    ],
    ["cancelled", "本次生成已安全停止，Current Draft 未被修改。"],
  ] as const)("shows %s as a safe stop instead of an alert", async (status, message) => {
    const cancelGeneration = vi.fn().mockResolvedValue(generationTask(status));
    installSession(generatingState(), { cancelGeneration });

    render(<DraftCandidatesStage />);
    fireEvent.click(screen.getByRole("button", { name: "停止本次生成" }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a raced success and confirms that candidates were refreshed", async () => {
    const cancelGeneration = vi.fn().mockResolvedValue(generationTask("succeeded"));
    installSession(generatingState(), { cancelGeneration });

    render(<DraftCandidatesStage />);
    fireEvent.click(screen.getByRole("button", { name: "停止本次生成" }));

    expect(
      await screen.findByText("任务已在停止请求到达前完成，候选列表已刷新。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a raced failure without claiming that stopping succeeded", async () => {
    const cancelGeneration = vi.fn().mockResolvedValue(generationTask("failed"));
    installSession(generatingState(), { cancelGeneration });

    render(<DraftCandidatesStage />);
    fireEvent.click(screen.getByRole("button", { name: "停止本次生成" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "任务已在停止请求到达前失败：模型在取消请求到达前已经失败。",
    );
    expect(screen.queryByText(/已请求安全停止/u)).not.toBeInTheDocument();
  });

  it("keeps an active user cancellation out of the generation error alert", async () => {
    const generateCandidates = vi.fn().mockResolvedValue("cancelled");
    installSession(generatingState(false), { generateCandidates });

    render(<DraftCandidatesStage />);
    fireEvent.click(screen.getByRole("button", { name: /生成结构优先完整深稿/u }));

    expect(
      await screen.findByText("本次生成已安全停止，Current Draft 未被修改。"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still exposes a real generation failure as an alert", async () => {
    const generateCandidates = vi.fn().mockRejectedValue(new Error("候选结构校验失败"));
    installSession(generatingState(false), { generateCandidates });

    render(<DraftCandidatesStage />);
    fireEvent.click(screen.getByRole("button", { name: /生成结构优先完整深稿/u }));

    expect(await screen.findByRole("alert")).toHaveTextContent("候选结构校验失败");
  });

  it.each([
    ["structure_first", "结构优先"],
    ["atmosphere_first", "氛围优先"],
    ["reasoning_first", "推理优先"],
  ] as const)(
    "regenerates an existing %s candidate with the next strategy attempt",
    async (strategy, label) => {
      const candidate = mappedCandidate(null, strategy);
      const generateCandidates = vi.fn().mockResolvedValue("succeeded");
      installSession(candidateState(candidate, strategy), {
        generateCandidates,
      });

      render(<DraftCandidatesStage />);
      const regenerate = screen.getByRole("button", {
        name: new RegExp(`重新生成${label}完整深稿`, "u"),
      });
      expect(regenerate).toBeEnabled();
      fireEvent.click(regenerate);

      await waitFor(() =>
        expect(generateCandidates).toHaveBeenCalledWith(strategy, 2),
      );
      expect(
        await screen.findByText(
          `${label}完整深稿已重新生成并通过结构与引用校验。`,
        ),
      ).toBeInTheDocument();
    },
  );
});

describe("draft candidate completion time", () => {
  it.each([
    ["2026-08-09T00:01:00Z", null],
    [null, "完成时间待同步"],
  ] as const)("renders the server completion time %s in the expanded footer", (completedAt, fallback) => {
    const candidate = mappedCandidate(completedAt);
    installSession(candidateState(candidate));

    render(<DraftCandidatesStage />);
    // 最新待采用候选默认展开，完成时间直接可见。
    expect(candidate.candidateState?.completedAt).toBe(completedAt);
    expect(screen.getByTestId("candidate-completed-at-401")).toHaveTextContent(
      fallback ?? formatCandidateCompletedAt(completedAt),
    );
  });
});

describe("strategy analysis feedback", () => {
  it("shows an active, accessible loading instrument while reading the frozen brief", () => {
    const state = generatingState(false);
    state.strategyAnalysis.status = "analyzing";
    installSession(state);

    render(<DraftCandidatesStage />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-busy", "true");
    expect(status).toHaveTextContent("正在拆读冻结的创作简报");
    expect(status).toHaveTextContent("完成后由你选择");
    expect(screen.getByTestId("strategy-analysis-loader")).toBeInTheDocument();
  });

  it("stops pipeline motion and relabels a stale running step after cancellation", () => {
    const state = generatingState();
    const task = generationTask("cancelled");
    task.component_steps = [componentStep("context_pack_builder", "running", 1)];
    state.generation.status = "idle";
    state.generation.slots.structure_first = {
      status: "cancelled",
      stage: "cancelled",
      taskRunId: task.task_run_id,
      attempt: 1,
      error: null,
      latestTask: task,
    };
    installSession(state);

    render(<DraftCandidatesStage />);

    const pipeline = screen.getByLabelText("深稿生成部件进度");
    expect(pipeline).not.toHaveAttribute("data-active");
    expect(within(pipeline).getByText("本次生成已停止")).toBeInTheDocument();
    expect(within(pipeline).getByText("已停止")).toBeInTheDocument();
    expect(pipeline.querySelector('[data-status="stopped"]')).toBeInTheDocument();
    expect(within(pipeline).getByRole("progressbar").firstElementChild).not.toHaveAttribute("data-active");
  });

  it("selects a strategy from its detail area or the keyboard", () => {
    const state = generatingState(false);
    state.strategyAnalysis = {
      status: "ready",
      options: [
        {
          strategy: "structure_first",
          direction: "先建立完整因果骨架。",
          focus: "物证交叉验证链",
          strengths: ["结构稳定"],
          tradeoffs: ["氛围稍后深化"],
          brief_fit: "直接匹配冻结建案中的推理目标。",
        },
      ],
      recommendedStrategy: "structure_first",
      recommendationReason: "适合先固定证据闭环。",
      error: null,
    };
    const selectStrategy = vi.fn();
    installSession(state, { selectStrategy });

    render(<DraftCandidatesStage />);

    expect(screen.queryByText(/Agent 建议/u)).not.toBeInTheDocument();
    expect(screen.queryByText("适合先固定证据闭环。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("直接匹配冻结建案中的推理目标。"));
    expect(selectStrategy).toHaveBeenCalledWith("structure_first");

    const card = screen.getByRole("button", {
      name: "结构优先：物证交叉验证链",
    });
    fireEvent.keyDown(card, { key: "Enter" });
    fireEvent.keyDown(card, { key: " " });
    expect(selectStrategy).toHaveBeenCalledTimes(3);
  });
});

function installSession(
  state: CaseSessionState,
  overrides: Record<string, unknown> = {},
) {
  mocks.useCaseSession.mockReturnValue({
    state,
    activeProjectId: 7,
    analyzeStrategies: vi.fn().mockResolvedValue(true),
    selectStrategy: vi.fn(),
    generateCandidates: vi.fn().mockResolvedValue("not_started"),
    resumeGeneration: vi.fn().mockResolvedValue(true),
    cancelGeneration: vi.fn().mockResolvedValue(null),
    previewCandidate: vi.fn(),
    adoptCandidate: vi.fn().mockResolvedValue(true),
    beginBriefRevision: vi.fn(),
    candidateStatus: vi.fn().mockReturnValue("pending"),
    ...overrides,
  });
}

function generatingState(generating = true) {
  const state = createInitialCaseSessionState();
  state.hydration = { status: "ready", error: null };
  state.workingBriefVersion = 2;
  state.frozenBriefVersion = 2;
  state.selectedStrategy = "structure_first";
  state.strategyAnalysis = {
    status: "ready",
    options: [],
    recommendedStrategy: null,
    recommendationReason: null,
    error: null,
  };
  state.generation.status = generating ? "generating" : "idle";
  state.generation.slots.structure_first = {
    status: generating ? "running" : "pending",
    stage: generating ? "generating" : "queued",
    taskRunId: generating ? 301 : null,
    attempt: 1,
    error: null,
    latestTask: generating ? generationTask("running") : null,
  };
  return state;
}

function candidateState(
  candidate: CaseSessionState["draftCandidates"][number],
  strategy: CandidateSlotStrategy = "structure_first",
) {
  const state = generatingState(false);
  state.selectedStrategy = strategy;
  state.draftCandidates = [candidate];
  return state;
}

function mappedCandidate(
  completedAt: string | null,
  strategy: DraftCandidateView["candidate_strategy"] = "structure_first",
) {
  const strategyLabels = {
    structure_first: "结构优先",
    atmosphere_first: "氛围优先",
    reasoning_first: "推理优先",
    balanced: "平衡",
  } as const;
  const view: DraftCandidateView = {
    task_run_id: 401,
    brief_version_no: 2,
    is_current_brief: true,
    is_current: false,
    is_adopted: false,
    can_adopt: true,
    provider: "openai",
    model_id: "gpt-5.6-sol",
    candidate_strategy_attempt: 1,
    attempt_count: 1,
    created_at: "2026-08-09T00:00:00Z",
    completed_at: completedAt,
    candidate_strategy: strategy,
    candidate_strategy_version: "candidate-strategy-v1",
    candidate_strategy_label: strategyLabels[strategy],
    title: "完成时间候选",
    content_hash: "candidate-completed-at",
    object_counts: {
      entities: 1,
      events: 1,
      information_units: 1,
      reasoning_paths: 1,
    },
    reasoning_questions: ["候选何时完成？"],
    constraint_statements: [],
  };
  const baseFocus = {
    structure_first: "structure",
    atmosphere_first: "atmosphere",
    reasoning_first: "reasoning",
    balanced: "structure",
  }[strategy];
  const base =
    buildWorkbenchCandidates(
      {
        creativeIntent: "展示真实完成时间。",
        reasoningProposition: "候选何时完成？",
        authorAnswer: "",
        constraints: [],
      },
      2,
    ).find((candidate) => candidate.focus === baseFocus) ??
    buildWorkbenchCandidates(
      {
        creativeIntent: "展示真实完成时间。",
        reasoningProposition: "候选何时完成？",
        authorAnswer: "",
        constraints: [],
      },
      2,
    )[0];
  return mapWorkbenchCandidateView(view, base);
}

function generationTask(status: TaskView["status"]): TaskView {
  return {
    task_run_id: 301,
    project_id: 7,
    task_type: "brief_to_draft",
    status,
    stage:
      status === "succeeded"
        ? "completed"
        : status === "failed" || status === "cancelled"
          ? "failed"
          : status,
    provider: "openai",
    model_id: "gpt-5.6-sol",
    input_draft_revision: 4,
    input_brief_revision: 8,
    input_source_record_id: null,
    input_brief_intake_id: null,
    input_brief_intake_revision: null,
    base_brief_intake_candidate_id: null,
    agent_thread_id: null,
    input_message_id: null,
    output_message_id: null,
    input_hash: "candidate-task-hash",
    candidate_strategy: "structure_first",
    attempt_count: 1,
    usage: {},
    result_snapshot_id: null,
    result: null,
    error_code: status === "failed" ? "generation_failed" : null,
    failure:
      status === "failed"
        ? {
            code: "generation_failed",
            message: "模型在取消请求到达前已经失败。",
            retryable: true,
            issues: [],
          }
        : null,
    component_steps: [],
    created_at: "2026-08-09T00:00:00Z",
    updated_at: "2026-08-09T00:01:00Z",
  };
}

function failedStep({
  stepRunId,
  componentId,
  executionNo,
  message,
}: {
  stepRunId: number;
  componentId: string;
  executionNo: number;
  message: string;
}): TaskView["component_steps"][number] {
  return {
    step_run_id: stepRunId,
    attempt_no: 1,
    component_id: componentId,
    parent_component_id: null,
    execution_no: executionNo,
    status: "failed",
    schema_id: "temporal-plan-v1",
    input_hash: "failed-step-input",
    output_hash: null,
    failure_layer: "schema_validation",
    issues: [
      {
        component_id: componentId,
        failure_layer: "schema_validation",
        schema_id: "temporal-plan-v1",
        code: "validation_failed",
        path: "/assignments/0/time/value",
        message,
      },
    ],
    recoverable: true,
    resumed_from_step_run_id: null,
  };
}

function componentStep(
  componentId: string,
  status: TaskView["component_steps"][number]["status"],
  stepRunId: number,
): TaskView["component_steps"][number] {
  return {
    step_run_id: stepRunId,
    attempt_no: 1,
    component_id: componentId,
    parent_component_id: ["story_world", "evidence_logic", "temporal_structure_planner", "resolution_governance"].includes(componentId)
      ? "domain_drafters"
      : null,
    execution_no: 1,
    status,
    schema_id: "test-schema-v1",
    input_hash: `input-${componentId}`,
    output_hash: status === "succeeded" ? `output-${componentId}` : null,
    failure_layer: null,
    issues: [],
    recoverable: false,
    resumed_from_step_run_id: null,
  };
}
