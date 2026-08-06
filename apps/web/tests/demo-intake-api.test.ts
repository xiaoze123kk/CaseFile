import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DemoIntakeError,
  isDemoAuthFailure,
  runTaskWithProviderFallback,
} from "@/features/demo-prototype/demo-intake-api";

const { apiRequestMock } = vi.hoisted(() => ({ apiRequestMock: vi.fn() }));

vi.mock("@/lib/api-client", () => ({
  apiRequest: apiRequestMock,
}));

afterEach(() => {
  apiRequestMock.mockReset();
});

describe("demo intake provider fallback", () => {
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
        throw new DemoIntakeError("模型服务认证失败", "provider_authentication_failed");
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
      throw new DemoIntakeError("候选结构校验失败", "candidate_validation_failed");
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
      isDemoAuthFailure(
        new DemoIntakeError("模型服务认证失败", "provider_authentication_failed"),
      ),
    ).toBe(true);
    expect(
      isDemoAuthFailure(new DemoIntakeError("候选结构校验失败", "candidate_validation_failed")),
    ).toBe(false);
    expect(isDemoAuthFailure(new Error("other"))).toBe(false);
  });
});
