import { afterEach, describe, expect, it, vi } from "vitest";

import type { BriefContent } from "@/lib/api-client";

import {
  CaseSessionError,
  fetchLatestTask,
  isBriefIntakeRevisionConflict,
  isProviderAuthFailure,
  runTaskWithProviderFallback,
  startAnchorExtractTask,
  startStrategyOptionsTask,
  strategyOptionsResult,
} from "@/features/case-session/case-session-api";

const { apiRequestMock, ApiErrorMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
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
}));

afterEach(() => {
  apiRequestMock.mockReset();
});

describe("case session provider fallback", () => {
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
