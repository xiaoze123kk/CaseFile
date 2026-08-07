import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsDialog } from "@/features/settings/settings-dialog";
import type { ProviderSettingView } from "@/lib/api-client";

const savedSetting: ProviderSettingView = {
  provider: "deepseek",
  model_id: "deepseek-v4-flash",
  model_is_custom: false,
  config_version: 1,
  credential_status: "unverified",
  masked_api_key: "••••••••68c1",
  validated_at: null,
  validation_error_code: null,
  default_budget: {},
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsDialog actorId={7} onClose={vi.fn()} open />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(savedSetting)));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("API key management", () => {
  it("reveals only the key currently entered by the user", () => {
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/ }));
    const input = screen.getByLabelText("DeepSeek API 密钥");
    expect(input).toHaveAttribute("type", "password");

    fireEvent.change(input, { target: { value: "sk-visible-only-in-form" } });
    fireEvent.click(screen.getByRole("button", { name: "显示 API 密钥" }));
    expect(input).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "隐藏 API 密钥" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("requires confirmation, deletes the selected provider key, and refreshes status", async () => {
    let configured = true;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        configured = false;
        return jsonResponse(undefined, 204);
      }
      return jsonResponse(configured ? savedSetting : null);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderSettings();
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/ }));

    await screen.findByText(/••••••••68c1/);
    fireEvent.click(screen.getByRole("button", { name: "删除密钥" }));
    expect(screen.getByRole("region", { name: "确认删除 API 密钥" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/settings/provider?provider=deepseek"),
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
    expect(await screen.findByText("API 密钥已删除，可以随时重新添加。")).toBeVisible();
    expect(await screen.findByText("尚未配置")).toBeVisible();
  });
});
