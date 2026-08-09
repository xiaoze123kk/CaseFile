import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DraftCandidateView, TaskView } from "@/lib/api-client";
import { buildWorkbenchCandidates } from "@/features/analyst-workbench/analyst-fixture";
import {
  createInitialCaseSessionState,
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
});

describe("draft candidate completion time", () => {
  it.each([
    ["2026-08-09T00:01:00Z", null],
    [null, "完成时间待同步"],
  ] as const)("renders the server completion time %s in the expanded footer", (completedAt, fallback) => {
    const candidate = mappedCandidate(completedAt);
    installSession(candidateState(candidate));

    render(<DraftCandidatesStage />);
    fireEvent.click(screen.getByRole("button", { name: /完成时间候选/u }));

    expect(candidate.candidateState?.completedAt).toBe(completedAt);
    expect(screen.getByTestId("candidate-completed-at-401")).toHaveTextContent(
      fallback ?? formatCandidateCompletedAt(completedAt),
    );
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

function candidateState(candidate: CaseSessionState["draftCandidates"][number]) {
  const state = generatingState(false);
  state.draftCandidates = [candidate];
  return state;
}

function mappedCandidate(completedAt: string | null) {
  const view: DraftCandidateView = {
    task_run_id: 401,
    brief_version_no: 2,
    is_current_brief: true,
    is_current: false,
    is_adopted: false,
    can_adopt: true,
    provider: "openai",
    model_id: "gpt-5.6-sol",
    attempt_count: 1,
    created_at: "2026-08-09T00:00:00Z",
    completed_at: completedAt,
    candidate_strategy: "structure_first",
    candidate_strategy_version: "candidate-strategy-v1",
    candidate_strategy_label: "结构优先",
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
  const base = buildWorkbenchCandidates(
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
