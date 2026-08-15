import { afterEach, describe, expect, it, vi } from "vitest";

import type { BriefContent, TaskView } from "@/lib/api-client";

import {
  CaseSessionError,
  TaskCancelledError,
  adoptDraftCandidateWithReconciliation,
  cancelTask,
  fetchDraftCandidatePreview,
  fetchLatestTask,
  isBriefIntakeRevisionConflict,
  isProviderAuthFailure,
  isTaskCancelledError,
  runTaskWithProviderFallback,
  startAnchorExtractTask,
  startStrategyOptionsTask,
  strategyOptionsResult,
  waitForTask,
} from "@/features/case-session/case-session-api";

const { apiRequestMock, streamTaskEventsMock, ApiErrorMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
  streamTaskEventsMock: vi.fn(),
  ApiErrorMock: class extends Error {
    constructor(
      readonly status: number,
      readonly body: { code: string; message: string },
    ) {
      super(body.message);
    }
  },
}));

vi.mock("@/lib/api-client", () => ({
  ApiError: ApiErrorMock,
  apiRequest: apiRequestMock,
  streamTaskEvents: streamTaskEventsMock,
}));

afterEach(() => {
  apiRequestMock.mockReset();
  streamTaskEventsMock.mockReset();
});

describe("case session provider fallback", () => {
  it("requests cooperative cancellation through the task endpoint", async () => {
    const cancelling = { task_run_id: 31, status: "cancelling" };
    apiRequestMock.mockResolvedValue(cancelling);

    await expect(cancelTask(7, 31)).resolves.toBe(cancelling);
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/projects/7/tasks/31/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("waits over SSE and refreshes the authoritative task after completion", async () => {
    const queued = taskView({ status: "queued", stage: "queued" });
    const succeeded = taskView({ status: "succeeded", stage: "completed" });
    apiRequestMock.mockResolvedValueOnce(queued).mockResolvedValueOnce(succeeded);
    streamTaskEventsMock.mockImplementation(
      async (
        _path: string,
        _actorId: number,
        onEvent: (event: Record<string, unknown>) => void,
      ) => {
        onEvent({ sequence_no: 1, event_type: "task.started", stage: "preparing" });
        onEvent({ sequence_no: 2, event_type: "task.succeeded", stage: "completed" });
      },
    );
    const ticks = vi.fn();

    await expect(waitForTask(7, 31, ticks)).resolves.toBe(succeeded);
    expect(streamTaskEventsMock).toHaveBeenCalledWith(
      "/projects/7/tasks/31/stream",
      expect.any(Number),
      expect.any(Function),
      expect.any(AbortSignal),
      0,
    );
    expect(ticks).toHaveBeenCalledWith(expect.objectContaining({ status: "running" }));
    expect(ticks).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
  });

  it("coalesces adjacent step events into one authoritative component refresh", async () => {
    const queued = taskView({ status: "queued", stage: "queued" });
    const running = taskView({ status: "running", stage: "domain_drafting" });
    running.component_steps = [
      {
        step_run_id: 81,
        attempt_no: 1,
        component_id: "story_world",
        parent_component_id: "domain_drafters",
        execution_no: 1,
        status: "running",
        schema_id: "story-world-ir-v2",
        input_hash: "story-input",
        output_hash: null,
        failure_layer: null,
        issues: [],
        recoverable: false,
        resumed_from_step_run_id: null,
      },
    ];
    const succeeded = taskView({ status: "succeeded", stage: "completed" });
    apiRequestMock
      .mockResolvedValueOnce(queued)
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(succeeded);
    streamTaskEventsMock.mockImplementationOnce(
      async (
        _path: string,
        _actorId: number,
        onEvent: (event: Record<string, unknown>) => void,
      ) => {
        onEvent({ sequence_no: 1, event_type: "agent.step.started", stage: "domain_drafting" });
        onEvent({ sequence_no: 2, event_type: "agent.step.completed", stage: "domain_drafting" });
      },
    );
    const ticks = vi.fn();

    await expect(waitForTask(7, 31, ticks)).resolves.toBe(succeeded);

    expect(apiRequestMock).toHaveBeenCalledTimes(3);
    expect(ticks).toHaveBeenCalledWith(
      expect.objectContaining({ component_steps: running.component_steps }),
    );
  });

  it("exposes user cancellation as a recognizable non-failure task result", async () => {
    const cancelled = taskView({ status: "cancelled", stage: "failed" });
    apiRequestMock.mockResolvedValue(cancelled);

    const error = await waitForTask(7, 31).catch((caught) => caught);

    expect(error).toBeInstanceOf(TaskCancelledError);
    expect(isTaskCancelledError(error)).toBe(true);
    expect(error).toMatchObject({
      failureCode: "task_cancelled",
      task: cancelled,
    });
  });

  it("keeps a failed terminal task classified as a real failure", async () => {
    const failed = {
      ...taskView({ status: "failed", stage: "failed" }),
      failure: {
        code: "candidate_validation_failed",
        message: "候选结构校验失败",
      },
    };
    apiRequestMock.mockResolvedValue(failed);

    const error = await waitForTask(7, 31).catch((caught) => caught);

    expect(error).toBeInstanceOf(CaseSessionError);
    expect(error).not.toBeInstanceOf(TaskCancelledError);
    expect(isTaskCancelledError(error)).toBe(false);
    expect(error).toMatchObject({
      message: "候选结构校验失败",
      failureCode: "candidate_validation_failed",
    });
  });

  it("reconnects SSE with the last observed sequence after an interruption", async () => {
    apiRequestMock
      .mockResolvedValueOnce(taskView({ status: "queued", stage: "queued" }))
      .mockResolvedValueOnce(taskView({ status: "running", stage: "generating" }))
      .mockResolvedValueOnce(taskView({ status: "running", stage: "generating" }))
      .mockResolvedValueOnce(taskView({ status: "succeeded", stage: "completed" }));
    streamTaskEventsMock
      .mockImplementationOnce(
        async (
          _path: string,
          _actorId: number,
          onEvent: (event: Record<string, unknown>) => void,
        ) => {
          onEvent({ sequence_no: 7, event_type: "agent.step.started", stage: "generating" });
          throw new DOMException("connection closed", "AbortError");
        },
      )
      .mockResolvedValueOnce(undefined);

    await expect(waitForTask(7, 31)).resolves.toMatchObject({ status: "succeeded" });
    expect(streamTaskEventsMock.mock.calls[1][4]).toBe(7);
  });

  it("honors an already-aborted wait signal without polling", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(waitForTask(7, 31, undefined, controller.signal)).rejects.toMatchObject({
      failureCode: "task_wait_aborted",
    });
    expect(apiRequestMock).not.toHaveBeenCalled();
  });

  it("localizes an abort that happens during the authoritative task poll", async () => {
    const controller = new AbortController();
    apiRequestMock.mockImplementationOnce(async () => {
      controller.abort();
      throw new DOMException("request aborted", "AbortError");
    });

    await expect(
      waitForTask(7, 31, undefined, controller.signal),
    ).rejects.toMatchObject({
      message: "已停止等待任务结果。",
      failureCode: "task_wait_aborted",
    });
  });

  it("loads a candidate through the read-only preview endpoint", async () => {
    const preview = { task_run_id: 31, preview: true, read_only: true };
    apiRequestMock.mockResolvedValue(preview);

    await expect(fetchDraftCandidatePreview(7, 31)).resolves.toBe(preview);
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/projects/7/draft-candidates/31",
      { actorId: expect.any(Number) },
    );
  });

  it("reconciles a lost adoption response against candidates and Current Draft", async () => {
    const responseError = new Error("adoption response lost");
    const candidates = [
      { task_run_id: 31, is_current: true },
      { task_run_id: 32, is_current: false },
    ];
    const draft = { revision: 5 };
    apiRequestMock
      .mockRejectedValueOnce(responseError)
      .mockResolvedValueOnce(candidates)
      .mockResolvedValueOnce(draft);

    await expect(
      adoptDraftCandidateWithReconciliation(7, 31, 4),
    ).resolves.toEqual({
      adoption: null,
      facts: { candidates, draft, targetIsCurrent: true },
      error: null,
    });
    expect(apiRequestMock).toHaveBeenNthCalledWith(
      1,
      "/projects/7/draft-candidates/31/adopt",
      expect.objectContaining({
        method: "POST",
        body: { expected_current_draft_id: 4 },
      }),
    );
    expect(apiRequestMock).toHaveBeenNthCalledWith(
      2,
      "/projects/7/draft-candidates",
      { actorId: expect.any(Number) },
    );
    expect(apiRequestMock).toHaveBeenNthCalledWith(
      3,
      "/projects/7/draft",
      { actorId: expect.any(Number) },
    );
  });

  it("preserves the adoption error when another candidate is Current", async () => {
    const responseError = new Error("adoption rejected");
    const candidates = [
      { task_run_id: 31, is_current: false },
      { task_run_id: 32, is_current: true },
    ];
    const draft = { revision: 5 };
    apiRequestMock
      .mockRejectedValueOnce(responseError)
      .mockResolvedValueOnce(candidates)
      .mockResolvedValueOnce(draft);

    const result = await adoptDraftCandidateWithReconciliation(7, 31, 4);

    expect(result.facts).toEqual({ candidates, draft, targetIsCurrent: false });
    expect(result.error).toBe(responseError);
  });

  it("reports an unconfirmed adoption when authoritative reconciliation fails", async () => {
    apiRequestMock
      .mockRejectedValueOnce(new Error("adoption response lost"))
      .mockRejectedValueOnce(new Error("candidate read failed"))
      .mockResolvedValueOnce({ revision: 5 });

    await expect(
      adoptDraftCandidateWithReconciliation(7, 31, 4),
    ).rejects.toMatchObject({
      failureCode: "draft_candidate_adoption_unconfirmed",
    });
    expect(apiRequestMock).toHaveBeenNthCalledWith(
      2,
      "/projects/7/draft-candidates",
      { actorId: expect.any(Number) },
    );
    expect(apiRequestMock).toHaveBeenNthCalledWith(
      3,
      "/projects/7/draft",
      { actorId: expect.any(Number) },
    );
  });

  it("reads the latest task for project recovery", async () => {
    const task = {
      task_run_id: 21,
      task_type: "brief_intake_questions",
      status: "running",
    };
    apiRequestMock.mockResolvedValue(task);

    await expect(fetchLatestTask(7, "brief_intake_questions")).resolves.toBe(task);
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/projects/7/tasks/latest?task_type=brief_intake_questions",
      { actorId: expect.any(Number) },
    );
  });

  it("starts strategy analysis with the frozen Brief and preserves explicit refresh", async () => {
    const task = {
      task_run_id: 19,
      task_type: "brief_strategy_options",
      status: "queued",
      result: null,
    };
    apiRequestMock.mockResolvedValue(task);

    await expect(
      startStrategyOptionsTask(7, 13, "deepseek", true),
    ).resolves.toBe(task);
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/projects/7/tasks/brief-strategy-options",
      expect.objectContaining({
        method: "POST",
        body: {
          brief_version_id: 13,
          provider: "deepseek",
          refresh: true,
        },
      }),
    );
  });

  it("starts an author-answer suggestion without changing the extraction task boundary", async () => {
    const task = {
      task_run_id: 22,
      task_type: "brief_anchor_extract",
      status: "queued",
      result: null,
    };
    apiRequestMock.mockResolvedValue(task);

    await expect(
      startAnchorExtractTask(7, 17, "openai", "suggest_author_answer"),
    ).resolves.toBe(task);
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/projects/7/tasks/brief-anchor-extract",
      expect.objectContaining({
        method: "POST",
        body: {
          expected_brief_revision: 17,
          provider: "openai",
          mode: "suggest_author_answer",
        },
      }),
    );
  });

  it("forwards the current draft snapshot for author-answer suggestions", async () => {
    const task = {
      task_run_id: 23,
      task_type: "brief_anchor_extract",
      status: "queued",
      result: null,
    };
    const content: BriefContent = {
      source_record_ids: [3],
      creative_intent: "失真的时间档案",
      reasoning_proposition: "三份记录为何指向不存在的时间？",
      resolution_mode: "agent_proposed",
      conclusion_mode: "unique",
      author_answer: null,
      author_anchors: [],
      boundary_text: null,
      creative_constraints: [],
    };
    apiRequestMock.mockResolvedValue(task);

    await expect(
      startAnchorExtractTask(7, 17, "openai", "suggest_author_answer", content),
    ).resolves.toBe(task);
    expect(apiRequestMock).toHaveBeenCalledWith(
      "/projects/7/tasks/brief-anchor-extract",
      expect.objectContaining({
        body: expect.objectContaining({ content }),
      }),
    );
  });

  it("reads only a complete strategy result", () => {
    const result = {
      input_hash: "h",
      strategy_version: "candidate-strategy-v1" as const,
      options: [],
      recommended_strategy: "reasoning_first" as const,
      recommendation_reason: "适配推理命题。",
    };
    expect(strategyOptionsResult({ result } as never)).toBe(result);
    expect(strategyOptionsResult({ result: null } as never)).toBeNull();
  });

  it("falls back to the next configured provider on authentication failure", async () => {
    apiRequestMock.mockImplementation(async (path: string) =>
      path.includes("deepseek")
        ? {
            provider: "deepseek",
            model_id: "deepseek-v4-flash",
            model_is_custom: false,
            config_version: 3,
            credential_status: "unverified",
            masked_api_key: "••••••••68c1",
            validated_at: null,
            validation_error_code: null,
            default_budget: {},
          }
        : {
            provider: "openai",
            model_id: "gpt-5.6-sol",
            model_is_custom: false,
            config_version: 1,
            credential_status: "unverified",
            masked_api_key: "••••••••flow",
            validated_at: null,
            validation_error_code: null,
            default_budget: {},
          },
    );

    const operation = vi.fn(async (provider: string) => {
      if (provider === "openai") {
        throw new CaseSessionError("模型服务认证失败", "provider_authentication_failed");
      }
      return `ok-${provider}`;
    });

    const { provider, result } = await runTaskWithProviderFallback(operation);

    expect(operation).toHaveBeenCalledTimes(2);
    expect(operation.mock.calls[0][0]).toBe("openai");
    expect(operation.mock.calls[1][0]).toBe("deepseek");
    expect(provider).toBe("deepseek");
    expect(result).toBe("ok-deepseek");
  });

  it("propagates non-authentication failures without falling back", async () => {
    apiRequestMock.mockImplementation(async (path: string) =>
      path.includes("deepseek") ? { credential_status: "unverified" } : { credential_status: "unverified" },
    );

    const operation = vi.fn(async () => {
      throw new CaseSessionError("候选结构校验失败", "candidate_validation_failed");
    });

    await expect(runTaskWithProviderFallback(operation)).rejects.toMatchObject({
      failureCode: "candidate_validation_failed",
    });
    expect(operation).toHaveBeenCalledTimes(1);
  });

  it("fails with a setup hint when no provider is configured", async () => {
    apiRequestMock.mockResolvedValue(null);

    await expect(
      runTaskWithProviderFallback(async () => "unused"),
    ).rejects.toThrow("请先在左上角设置入口配置模型服务。");
  });

  it("classifies only authentication failures as auth failures", () => {
    expect(
      isProviderAuthFailure(
        new CaseSessionError("模型服务认证失败", "provider_authentication_failed"),
      ),
    ).toBe(true);
    expect(
      isProviderAuthFailure(new CaseSessionError("候选结构校验失败", "candidate_validation_failed")),
    ).toBe(false);
    expect(isProviderAuthFailure(new Error("other"))).toBe(false);
  });

  it("classifies Brief Intake revision conflicts without matching other errors", () => {
    expect(
      isBriefIntakeRevisionConflict(
        new ApiErrorMock(409, {
          code: "brief_intake_revision_conflict",
          message: "Brief Intake revision is stale",
        }),
      ),
    ).toBe(true);
    expect(
      isBriefIntakeRevisionConflict(
        new ApiErrorMock(409, {
          code: "resource_conflict",
          message: "Resource is stale",
        }),
      ),
    ).toBe(false);
  });
});

function taskView(overrides: Pick<TaskView, "status" | "stage">): TaskView {
  return {
    task_run_id: 31,
    project_id: 7,
    task_type: "brief_to_draft",
    provider: "deepseek",
    model_id: "deepseek-v4-flash",
    input_draft_revision: 1,
    input_brief_revision: 1,
    input_source_record_id: null,
    input_brief_intake_id: null,
    input_brief_intake_revision: null,
    base_brief_intake_candidate_id: null,
    agent_thread_id: null,
    input_message_id: null,
    output_message_id: null,
    input_hash: "hash",
    candidate_strategy: "structure_first",
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
