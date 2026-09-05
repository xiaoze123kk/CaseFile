import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BriefIntakeView,
  BriefView,
  DraftCandidateView,
  TaskView,
} from "@/lib/api-client";
import {
  CaseSessionProvider,
  useCaseSession,
} from "@/features/case-session/case-session-provider";
import { TaskCancelledError } from "@/features/case-session/case-session-api";

const mocks = vi.hoisted(() => ({
  adoptDraftCandidateWithReconciliation: vi.fn(),
  cancelTask: vi.fn(),
  fetchBrief: vi.fn(),
  fetchCaseDraft: vi.fn(),
  fetchCaseIntake: vi.fn(),
  fetchDraftCandidates: vi.fn(),
  fetchLatestTask: vi.fn(),
  runTaskWithProviderFallback: vi.fn(),
  startDraftGenerationTask: vi.fn(),
  waitForTask: vi.fn(),
  waitForRecoveredTask: vi.fn(),
}));

vi.mock("@/features/case-session/case-session-api", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@/features/case-session/case-session-api")
  >()),
  ...mocks,
}));

const intake: BriefIntakeView = {
  brief_intake_id: 1,
  project_id: 7,
  revision: 8,
  stage: "brief_review",
  current_source: null,
  current_questions_task_run_id: null,
  questions: [],
  hard_questions_resolved: true,
  current_candidate_id: null,
  adopted_candidate_id: null,
  candidates: [],
  pending_decisions: [],
  brief: {
    brief_id: 1,
    draft_revision: 8,
    current_version_id: 202,
    has_content: true,
  },
  updated_at: null,
};

const brief: BriefView = {
  brief_id: 1,
  public_id: "brief-7",
  draft_revision: 8,
  current_version_id: 202,
  current_version_no: 2,
  content: {
    source_record_ids: [],
    creative_intent: "核对跨版本候选恢复。",
    reasoning_proposition: "哪一份候选仍是 Current Draft？",
    resolution_mode: "agent_proposed",
    conclusion_mode: "unique",
    author_answer: null,
    author_anchors: [],
    boundary_text: null,
    creative_constraints: [],
  },
};

function candidate(
  taskRunId: number,
  title: string,
  briefVersion: number,
  state: Pick<
    DraftCandidateView,
    "is_current_brief" | "is_current" | "is_adopted" | "can_adopt"
  >,
  strategy: DraftCandidateView["candidate_strategy"] = "structure_first",
  strategyAttempt = 1,
): DraftCandidateView {
  const strategyLabels = {
    structure_first: "结构优先",
    atmosphere_first: "氛围优先",
    reasoning_first: "推理优先",
    balanced: "平衡",
  } as const;
  return {
    task_run_id: taskRunId,
    brief_version_no: briefVersion,
    ...state,
    provider: "openai",
    model_id: "gpt-5.6-sol",
    candidate_strategy_attempt: strategyAttempt,
    attempt_count: 1,
    created_at: "2026-08-09T00:00:00Z",
    completed_at: "2026-08-09T00:01:00Z",
    candidate_strategy: strategy,
    candidate_strategy_version: "candidate-strategy-v1",
    candidate_strategy_label: strategyLabels[strategy],
    title,
    content_hash: `hash-${taskRunId}`,
    object_counts: {
      entities: 1,
      events: 1,
      information_units: 1,
      reasoning_paths: 1,
    },
    reasoning_questions: ["哪一份候选仍是 Current Draft？"],
    constraint_statements: [],
  };
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

function RecoveryProbe() {
  const { adoptCandidate, candidateStatus, state } = useCaseSession();
  const [adoptionError, setAdoptionError] = useState("none");
  return (
    <section>
      <output data-testid="visible-step">{state.step}</output>
      <output data-testid="furthest-step">{state.furthestStep}</output>
      <output data-testid="adopted">{state.adoptedCandidateId ?? "none"}</output>
      <output data-testid="adoption-error">{adoptionError}</output>
      {state.draftCandidates.map((item) => {
        const status = candidateStatus(item);
        return (
          <button
            disabled={status === "stale"}
            key={item.id}
            onClick={() => {
              void adoptCandidate(item.id).catch((caught) =>
                setAdoptionError(caught instanceof Error ? caught.message : "unknown"),
              );
            }}
            type="button"
          >
            {item.title} · {status}
          </button>
        );
      })}
    </section>
  );
}

function CancellationProbe() {
  const { cancelGeneration, patchState, state } = useCaseSession();
  const slot = state.generation.slots.structure_first;
  return (
    <section>
      <button
        onClick={() => {
          const task = generationTask("running");
          patchState({
            selectedStrategy: "structure_first",
            generation: {
              ...state.generation,
              status: "generating",
              slots: {
                ...state.generation.slots,
                structure_first: {
                  status: "running",
                  stage: "generating",
                  taskRunId: task.task_run_id,
                  attempt: 1,
                  error: null,
                  latestTask: task,
                },
              },
            },
          });
        }}
        type="button"
      >
        准备取消
      </button>
      <button
        onClick={() => void cancelGeneration("structure_first")}
        type="button"
      >
        停止生成
      </button>
      <output data-testid="slot-status">{slot.status}</output>
      <output data-testid="slot-error">{slot.error ?? "none"}</output>
      <output data-testid="cancel-generation-status">{state.generation.status}</output>
      <output data-testid="candidate-count">{state.draftCandidates.length}</output>
    </section>
  );
}

function GenerationProbe() {
  const { generateCandidates, state } = useCaseSession();
  const [outcome, setOutcome] = useState("none");
  const [error, setError] = useState("none");
  return (
    <section>
      <button
        onClick={() => {
          void generateCandidates("structure_first")
            .then(setOutcome)
            .catch((caught) => setError(caught instanceof Error ? caught.message : "unknown"));
        }}
        type="button"
      >
        生成候选
      </button>
      <output data-testid="hydration-status">{state.hydration.status}</output>
      <output data-testid="generation-outcome">{outcome}</output>
      <output data-testid="generation-status">{state.generation.status}</output>
      <output data-testid="generated-slot-status">
        {state.generation.slots.structure_first.status}
      </output>
      <output data-testid="generation-error">{error}</output>
      <output data-testid="recovered-candidate-count">
        {state.draftCandidates.length}
      </output>
      <output data-testid="latest-generation-status">
        {state.latestTasks.brief_to_draft?.status ?? "none"}
      </output>
    </section>
  );
}

function RegenerationProbe() {
  const { generateCandidates, state } = useCaseSession();
  const [outcome, setOutcome] = useState("none");
  const [error, setError] = useState("none");
  return (
    <section>
      <button
        onClick={() => {
          void generateCandidates("reasoning_first", 2)
            .then(setOutcome)
            .catch((caught) =>
              setError(caught instanceof Error ? caught.message : "unknown"),
            );
        }}
        type="button"
      >
        重新生成推理优先
      </button>
      <output data-testid="regeneration-outcome">{outcome}</output>
      <output data-testid="regeneration-error">{error}</output>
      <output data-testid="regeneration-hydration">
        {state.hydration.status}
      </output>
      <output data-testid="regeneration-slot-attempt">
        {state.generation.slots.reasoning_first.attempt}
      </output>
      <output data-testid="regeneration-candidate-count">
        {state.draftCandidates.length}
      </output>
    </section>
  );
}

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.clearAllMocks();
});

describe("draft candidate project recovery", () => {
  it("restores all Brief versions and keeps an adopted old candidate current", async () => {
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockResolvedValue(null);
    mocks.fetchDraftCandidates.mockResolvedValue([
      candidate(103, "当前简报待采用稿", 2, {
        is_current_brief: true,
        is_current: false,
        is_adopted: false,
        can_adopt: true,
      }),
      candidate(102, "旧简报未采用稿", 1, {
        is_current_brief: false,
        is_current: false,
        is_adopted: false,
        can_adopt: false,
      }),
      candidate(101, "旧简报 Current Draft", 1, {
        is_current_brief: false,
        is_current: true,
        is_adopted: true,
        can_adopt: false,
      }),
    ]);
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <RecoveryProbe />
      </CaseSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("adopted")).toHaveTextContent("draft-101");
    });
    expect(screen.getByTestId("visible-step")).toHaveTextContent("candidates");
    expect(screen.getByTestId("furthest-step")).toHaveTextContent("3");
    expect(
      screen.getByRole("button", { name: "旧简报 Current Draft · current" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "当前简报待采用稿 · pending" }),
    ).toBeEnabled();
    const stale = screen.getByRole("button", {
      name: "旧简报未采用稿 · stale",
    });
    expect(stale).toBeDisabled();
    fireEvent.click(stale);
    expect(mocks.adoptDraftCandidateWithReconciliation).not.toHaveBeenCalled();
    expect(mocks.fetchDraftCandidates).toHaveBeenCalledWith(7);
  });

  it("restores an unfinished hidden brief_review lifecycle to visible step 03", async () => {
    mocks.fetchCaseIntake.mockResolvedValue({
      ...intake,
      brief: {
        ...intake.brief,
        current_version_id: null,
      },
    });
    mocks.fetchBrief.mockResolvedValue({
      ...brief,
      current_version_id: null,
      current_version_no: null,
    });
    mocks.fetchLatestTask.mockResolvedValue(null);
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <RecoveryProbe />
      </CaseSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("visible-step")).toHaveTextContent("confirmation");
    });
    expect(screen.getByTestId("furthest-step")).toHaveTextContent("2");
    expect(mocks.fetchDraftCandidates).not.toHaveBeenCalled();
  });

  it("keeps adoption successful when the follow-up Brief refresh fails", async () => {
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief
      .mockResolvedValueOnce(brief)
      .mockRejectedValueOnce(new Error("Brief refresh unavailable"));
    mocks.fetchLatestTask.mockResolvedValue(null);
    mocks.fetchCaseDraft.mockResolvedValue({ draft_id: 9, revision: 4 });
    mocks.fetchDraftCandidates.mockResolvedValue([
      candidate(103, "当前简报待采用稿", 2, {
        is_current_brief: true,
        is_current: false,
        is_adopted: false,
        can_adopt: true,
      }),
    ]);
    mocks.adoptDraftCandidateWithReconciliation.mockResolvedValue({
      adoption: { draft_id: 9 },
      facts: null,
      error: null,
    });
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <RecoveryProbe />
      </CaseSessionProvider>,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "当前简报待采用稿 · pending",
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("adopted")).toHaveTextContent("draft-103");
    });
    expect(mocks.adoptDraftCandidateWithReconciliation).toHaveBeenCalledWith(
      7,
      103,
      9,
    );
    expect(mocks.fetchBrief).toHaveBeenCalledTimes(2);
  });

  it("regenerates an existing reasoning_first candidate with the requested attempt", async () => {
    const existing = candidate(
      301,
      "推理优先初稿",
      2,
      {
        is_current_brief: true,
        is_current: false,
        is_adopted: false,
        can_adopt: true,
      },
      "reasoning_first",
      1,
    );
    const refreshed = candidate(
      302,
      "推理优先重生成稿",
      2,
      {
        is_current_brief: true,
        is_current: false,
        is_adopted: false,
        can_adopt: true,
      },
      "reasoning_first",
      2,
    );
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockResolvedValue(null);
    mocks.fetchCaseDraft.mockResolvedValue({ draft_id: 9, revision: 4 });
    mocks.fetchDraftCandidates
      .mockResolvedValueOnce([existing])
      .mockResolvedValueOnce([existing])
      .mockResolvedValueOnce([refreshed, existing]);
    mocks.startDraftGenerationTask.mockResolvedValue(generationTask("running"));
    mocks.runTaskWithProviderFallback.mockImplementation(async (operation) => ({
      provider: "openai",
      result: await operation("openai"),
    }));
    mocks.waitForTask.mockImplementation(
      async (_projectId: number, _taskRunId: number, onTick: (task: TaskView) => void) => {
        const succeeded = generationTask("succeeded");
        onTick(succeeded);
        return succeeded;
      },
    );
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <RegenerationProbe />
      </CaseSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("regeneration-hydration")).toHaveTextContent("ready");
    });
    fireEvent.click(
      screen.getByRole("button", { name: "重新生成推理优先" }),
    );

    await waitFor(() => {
      expect(mocks.startDraftGenerationTask).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(mocks.fetchDraftCandidates).toHaveBeenCalledTimes(3);
    });
    await waitFor(() => {
      expect(screen.getByTestId("regeneration-outcome")).toHaveTextContent(
        "succeeded",
      );
      expect(screen.getByTestId("regeneration-slot-attempt")).toHaveTextContent("2");
      expect(screen.getByTestId("regeneration-candidate-count")).toHaveTextContent("2");
    });
    expect(screen.getByTestId("regeneration-error")).toHaveTextContent("none");
    expect(mocks.startDraftGenerationTask).toHaveBeenCalledWith(
      7,
      202,
      9,
      4,
      "openai",
      "reasoning_first",
      2,
    );
  });

  it("recovers a running generation to succeeded and refreshes candidates", async () => {
    const running = generationTask("running");
    const succeeded = generationTask("succeeded");
    const generatedCandidate = candidate(301, "Recovered candidate", 2, {
      is_current_brief: true,
      is_current: false,
      is_adopted: false,
      can_adopt: true,
    });
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockImplementation(
      async (_projectId: number, taskType: string) =>
        taskType === "brief_to_draft" ? running : null,
    );
    mocks.fetchDraftCandidates
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([generatedCandidate]);
    mocks.waitForRecoveredTask.mockImplementation(
      async (_projectId: number, _taskRunId: number, onTick: (task: TaskView) => void) => {
        onTick(succeeded);
        return succeeded;
      },
    );
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <GenerationProbe />
      </CaseSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("generated-slot-status")).toHaveTextContent("succeeded");
      expect(screen.getByTestId("generation-status")).toHaveTextContent("ready");
      expect(screen.getByTestId("latest-generation-status")).toHaveTextContent(
        "succeeded",
      );
      expect(screen.getByTestId("recovered-candidate-count")).toHaveTextContent("1");
    });
    expect(mocks.waitForRecoveredTask).toHaveBeenCalledWith(
      7,
      301,
      expect.any(Function),
    );
    expect(mocks.fetchDraftCandidates).toHaveBeenCalledTimes(2);
  });

  it("recovers a cancelled generation as cancelled and idle, not failed", async () => {
    const running = generationTask("running");
    const cancelled = generationTask("cancelled");
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockImplementation(
      async (_projectId: number, taskType: string) =>
        taskType === "brief_to_draft" ? running : null,
    );
    mocks.fetchDraftCandidates.mockResolvedValue([]);
    mocks.waitForRecoveredTask.mockImplementation(
      async (_projectId: number, _taskRunId: number, onTick: (task: TaskView) => void) => {
        onTick(cancelled);
        return cancelled;
      },
    );
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <GenerationProbe />
      </CaseSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("generated-slot-status")).toHaveTextContent("cancelled");
      expect(screen.getByTestId("generation-status")).toHaveTextContent("idle");
      expect(screen.getByTestId("latest-generation-status")).toHaveTextContent(
        "cancelled",
      );
    });
    expect(screen.getByTestId("generated-slot-status")).not.toHaveTextContent("failed");
  });

  it("returns an active user cancellation without surfacing a generation failure", async () => {
    const cancelled = generationTask("cancelled");
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockResolvedValue(null);
    mocks.fetchCaseDraft.mockResolvedValue({ draft_id: 9, revision: 4 });
    mocks.fetchDraftCandidates.mockResolvedValue([]);
    mocks.startDraftGenerationTask.mockResolvedValue(generationTask("running"));
    mocks.runTaskWithProviderFallback.mockImplementation(async (operation) => ({
      provider: "openai",
      result: await operation("openai"),
    }));
    mocks.waitForTask.mockImplementation(async (_projectId, _taskRunId, onTick) => {
      onTick?.(cancelled);
      throw new TaskCancelledError(cancelled);
    });
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <GenerationProbe />
      </CaseSessionProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("hydration-status")).toHaveTextContent("ready");
    });
    fireEvent.click(screen.getByRole("button", { name: "生成候选" }));

    await waitFor(() => {
      expect(screen.getByTestId("generation-outcome")).toHaveTextContent("cancelled");
      expect(screen.getByTestId("generation-status")).toHaveTextContent("idle");
      expect(screen.getByTestId("generated-slot-status")).toHaveTextContent("cancelled");
    });
    expect(screen.getByTestId("generation-error")).toHaveTextContent("none");
  });

  it("maps a raced successful cancellation response and refreshes candidates", async () => {
    let terminal = false;
    const generatedCandidate = candidate(301, "临界成功候选", 2, {
      is_current_brief: true,
      is_current: false,
      is_adopted: false,
      can_adopt: true,
    });
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockImplementation(
      async (_projectId: number, taskType: string) =>
        terminal && taskType === "brief_to_draft"
          ? generationTask("succeeded")
          : null,
    );
    mocks.fetchDraftCandidates.mockImplementation(async () =>
      terminal ? [generatedCandidate] : [],
    );
    mocks.cancelTask.mockImplementation(async () => {
      terminal = true;
      return generationTask("succeeded");
    });
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <CancellationProbe />
      </CaseSessionProvider>,
    );

    await screen.findByRole("button", { name: "准备取消" });
    fireEvent.click(screen.getByRole("button", { name: "准备取消" }));
    await waitFor(() => {
      expect(screen.getByTestId("slot-status")).toHaveTextContent("running");
    });
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    await waitFor(() => {
      expect(screen.getByTestId("slot-status")).toHaveTextContent("succeeded");
      expect(screen.getByTestId("cancel-generation-status")).toHaveTextContent("ready");
      expect(screen.getByTestId("candidate-count")).toHaveTextContent("1");
    });
    expect(mocks.cancelTask).toHaveBeenCalledWith(7, 301);
    expect(mocks.fetchDraftCandidates).toHaveBeenCalledTimes(2);
  });

  it("keeps a confirmed cancellation out of the failed generation state", async () => {
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockResolvedValue(null);
    mocks.fetchDraftCandidates.mockResolvedValue([]);
    mocks.cancelTask.mockResolvedValue(generationTask("cancelled"));
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <CancellationProbe />
      </CaseSessionProvider>,
    );

    await screen.findByRole("button", { name: "准备取消" });
    fireEvent.click(screen.getByRole("button", { name: "准备取消" }));
    await waitFor(() => {
      expect(screen.getByTestId("slot-status")).toHaveTextContent("running");
    });
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    await waitFor(() => {
      expect(screen.getByTestId("slot-status")).toHaveTextContent("cancelled");
      expect(screen.getByTestId("cancel-generation-status")).toHaveTextContent("idle");
    });
    expect(screen.getByTestId("slot-error")).toHaveTextContent("none");
    expect(mocks.fetchDraftCandidates).toHaveBeenCalledTimes(1);
  });

  it("maps a raced failed cancellation response to the failed slot", async () => {
    mocks.fetchCaseIntake.mockResolvedValue(intake);
    mocks.fetchBrief.mockResolvedValue(brief);
    mocks.fetchLatestTask.mockResolvedValue(null);
    mocks.fetchDraftCandidates.mockResolvedValue([]);
    mocks.cancelTask.mockResolvedValue(generationTask("failed"));
    window.history.replaceState({}, "", "/?project=7");

    render(
      <CaseSessionProvider>
        <CancellationProbe />
      </CaseSessionProvider>,
    );

    await screen.findByRole("button", { name: "准备取消" });
    fireEvent.click(screen.getByRole("button", { name: "准备取消" }));
    await waitFor(() => {
      expect(screen.getByTestId("slot-status")).toHaveTextContent("running");
    });
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));

    await waitFor(() => {
      expect(screen.getByTestId("slot-status")).toHaveTextContent("failed");
      expect(screen.getByTestId("cancel-generation-status")).toHaveTextContent("idle");
      expect(screen.getByTestId("slot-error")).toHaveTextContent(
        "模型在取消请求到达前已经失败。",
      );
    });
    expect(mocks.fetchDraftCandidates).toHaveBeenCalledTimes(1);
  });
});
