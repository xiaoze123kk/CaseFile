import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkbenchRoute from "@/app/workbench/page";

vi.mock("@/features/analyst-workbench/analyst-workbench", () => ({
  AnalystWorkbench: ({
    requestedProjectId,
    requestedPreviewTaskRunId,
    invalidProjectId,
    invalidPreviewTaskRunId,
  }: {
    requestedProjectId: number | null;
    requestedPreviewTaskRunId: number | null;
    invalidProjectId: boolean;
    invalidPreviewTaskRunId: boolean;
  }) => (
    <output
      data-invalid-preview={String(invalidPreviewTaskRunId)}
      data-invalid-project={String(invalidProjectId)}
      data-preview={String(requestedPreviewTaskRunId)}
      data-project={String(requestedProjectId)}
    >
      workbench route
    </output>
  ),
}));

afterEach(cleanup);

describe("workbench route candidate preview query", () => {
  it("passes strict positive project and preview ids to the workbench", async () => {
    render(
      await WorkbenchRoute({
        searchParams: Promise.resolve({ project: "42", preview: "73" }),
      }),
    );

    const route = screen.getByText("workbench route");
    expect(route).toHaveAttribute("data-project", "42");
    expect(route).toHaveAttribute("data-preview", "73");
    expect(route).toHaveAttribute("data-invalid-project", "false");
    expect(route).toHaveAttribute("data-invalid-preview", "false");
  });

  it("rejects decimal, signed, empty, and unsafe preview ids", async () => {
    for (const preview of ["0", "-1", "1.5", "", "9007199254740992"]) {
      const view = render(
        await WorkbenchRoute({
          searchParams: Promise.resolve({ project: "42", preview }),
        }),
      );

      const route = screen.getByText("workbench route");
      expect(route).toHaveAttribute("data-preview", "null");
      expect(route).toHaveAttribute("data-invalid-preview", "true");
      view.unmount();
    }
  });
});
