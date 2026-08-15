import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DraftSwitcher,
  ProjectSwitcher,
} from "@/features/analyst-workbench/workbench-scope-switcher";
import {
  ApiError,
  type DraftSummaryView,
  type DraftView,
  type ProjectView,
} from "@/lib/api-client";

const mocks = vi.hoisted(() => ({
  activateDraft: vi.fn(),
  listDrafts: vi.fn(),
  listProjects: vi.fn(),
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    activateDraft: mocks.activateDraft,
    listDrafts: mocks.listDrafts,
    listProjects: mocks.listProjects,
  };
});

function project(id: number, title: string): ProjectView {
  return {
    id,
    title,
    description: null,
    profile: {},
    status: "active",
    archived_at: null,
    created_at: "2026-08-09T10:00:00+00:00",
    updated_at: "2026-08-09T11:00:00+00:00",
    casefile_id: id,
    current_draft_id: id * 10,
    draft: {
      id: id * 10,
      title: `${title}工作稿`,
      revision: 2,
      schema_version: "1.0",
      status: "active",
    },
  };
}

function draft(id: number, title: string): DraftView {
  return {
    project_id: 42,
    casefile_id: 5,
    draft_id: id,
    title,
    revision: 2,
    schema_version: "1.0",
    status: "active",
    document_status: "draft",
    brief_version_id: 7,
    created_at: "2026-08-09T10:00:00+00:00",
    updated_at: "2026-08-09T11:00:00+00:00",
    content: {} as DraftView["content"],
  };
}

function summary(
  id: number,
  title: string,
  current: boolean,
  overrides: Partial<DraftSummaryView> = {},
): DraftSummaryView {
  return {
    draft_id: id,
    title,
    revision: 2,
    schema_version: "1.0",
    status: "active",
    document_status: "draft",
    brief_version_id: 7,
    brief_version_no: 3,
    has_content: true,
    is_current: current,
    created_at: "2026-08-09T10:00:00+00:00",
    updated_at: current
      ? "2026-08-09T11:00:00+00:00"
      : "2026-08-09T12:00:00+00:00",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("workbench scope switchers", () => {
  it("keeps the project switch in the global bar and restores focus on Escape", async () => {
    mocks.listProjects.mockResolvedValue([
      project(42, "午夜回航"),
      project(43, "封存室缺页"),
    ]);
    render(<ProjectSwitcher currentProjectId={42} fallbackTitle="载入中" />);

    const trigger = await screen.findByRole("button", { name: /项目.*午夜回航/u });
    fireEvent.click(trigger);
    expect(await screen.findByRole("menu", { name: "切换项目" })).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /封存室缺页.*当前工作稿 #430/u }),
    ).toHaveAttribute("href", "/workbench?project=43");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "切换项目" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("activates another Draft with the current pointer and exposes the generation return", async () => {
    const current = draft(11, "缺页校准稿");
    const activated = draft(12, "潮汐证词稿");
    mocks.listDrafts.mockResolvedValue([
      summary(11, "缺页校准稿", true),
      summary(12, "潮汐证词稿", false),
    ]);
    mocks.activateDraft.mockResolvedValue(activated);
    const onActivated = vi.fn();

    render(
      <DraftSwitcher
        currentDraft={current}
        onActivated={onActivated}
        projectId={42}
      />,
    );
    const trigger = screen.getByRole("button", {
      name: /当前工作稿.*缺页校准稿/u,
    });
    fireEvent.click(trigger);

    const target = await screen.findByRole("menuitem", {
      name: /潮汐证词稿.*工作稿 #12.*Brief V3.*R2/u,
    });
    expect(screen.getByRole("menuitem", { name: /生成新工作稿/u })).toHaveAttribute(
      "href",
      "/?project=42",
    );
    fireEvent.click(target);

    await waitFor(() =>
      expect(mocks.activateDraft).toHaveBeenCalledWith(1, 42, 12, 11),
    );
    expect(onActivated).toHaveBeenCalledWith(activated);
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes on an outside pointer and gives an actionable empty state", async () => {
    mocks.listDrafts.mockResolvedValue([]);
    render(
      <div>
        <DraftSwitcher
          currentDraft={draft(11, "缺页校准稿")}
          onActivated={vi.fn()}
          projectId={42}
        />
        <button type="button">外部按钮</button>
      </div>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /当前工作稿.*缺页校准稿/u }),
    );
    expect(await screen.findByText("还没有已生成的工作稿")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /生成新工作稿/u })).toBeInTheDocument();

    fireEvent.pointerDown(screen.getByRole("button", { name: "外部按钮" }));
    expect(screen.queryByRole("menu", { name: "切换工作稿" })).not.toBeInTheDocument();
  });

  it("keeps a failed Draft list recoverable inside the menu", async () => {
    mocks.listDrafts
      .mockRejectedValueOnce(new Error("draft list unavailable"))
      .mockResolvedValueOnce([summary(11, "缺页校准稿", true)]);
    render(
      <DraftSwitcher
        currentDraft={draft(11, "缺页校准稿")}
        onActivated={vi.fn()}
        projectId={42}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /当前工作稿.*缺页校准稿/u }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "工作稿操作未完成",
    );
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));

    expect(
      await screen.findByRole("menuitem", {
        name: /缺页校准稿.*工作稿 #11.*Brief V3.*R2/u,
      }),
    ).toHaveAttribute("aria-current", "page");
    expect(mocks.listDrafts).toHaveBeenCalledTimes(2);
  });

  it("blocks project navigation when the parent reports unsaved edits", async () => {
    mocks.listProjects.mockResolvedValue([
      project(42, "午夜回航"),
      project(43, "封存室缺页"),
    ]);
    const onBeforeSwitch = vi.fn(() => false);
    render(
      <ProjectSwitcher
        currentProjectId={42}
        fallbackTitle="午夜回航"
        onBeforeSwitch={onBeforeSwitch}
      />,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /项目.*午夜回航/u }),
    );
    const target = screen.getByRole("menuitem", { name: /封存室缺页/u });

    expect(fireEvent.click(target)).toBe(false);
    expect(onBeforeSwitch).toHaveBeenCalledWith(
      expect.objectContaining({ id: 43 }),
    );
    expect(window.location.pathname).toBe("/");
  });

  it("blocks Draft activation when the parent reports unsaved edits", async () => {
    mocks.listDrafts.mockResolvedValue([
      summary(11, "缺页校准稿", true),
      summary(12, "潮汐证词稿", false),
    ]);
    const onBeforeSwitch = vi.fn(() => false);
    render(
      <DraftSwitcher
        currentDraft={draft(11, "缺页校准稿")}
        onActivated={vi.fn()}
        onBeforeSwitch={onBeforeSwitch}
        projectId={42}
      />,
    );

    const trigger = screen.getByRole("button", {
      name: /当前工作稿.*缺页校准稿/u,
    });
    fireEvent.click(trigger);
    fireEvent.click(
      await screen.findByRole("menuitem", { name: /潮汐证词稿/u }),
    );

    expect(onBeforeSwitch).toHaveBeenCalledWith(
      expect.objectContaining({ draft_id: 12 }),
    );
    expect(mocks.activateDraft).not.toHaveBeenCalled();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("refreshes the authoritative Current Draft after an activation race", async () => {
    const refreshed = draft(13, "服务端已切换稿");
    const onCurrentDraftChanged = vi.fn();
    mocks.listDrafts.mockResolvedValue([
      summary(11, "缺页校准稿", true),
      summary(12, "潮汐证词稿", false),
      summary(13, "服务端已切换稿", false),
    ]);
    mocks.activateDraft.mockRejectedValueOnce(
      new ApiError(409, {
        code: "current_draft_changed",
        message: "当前工作稿已在其他位置切换。",
        details: { current_draft_id: 13 },
      }),
    );

    function ConflictHarness() {
      const [current, setCurrent] = useState(draft(11, "缺页校准稿"));
      return (
        <DraftSwitcher
          currentDraft={current}
          onActivated={setCurrent}
          onCurrentDraftChanged={async () => {
            onCurrentDraftChanged();
            setCurrent(refreshed);
          }}
          projectId={42}
        />
      );
    }

    render(<ConflictHarness />);
    fireEvent.click(
      screen.getByRole("button", { name: /当前工作稿.*缺页校准稿/u }),
    );
    fireEvent.click(
      await screen.findByRole("menuitem", { name: /潮汐证词稿/u }),
    );

    expect(
      await screen.findByRole("button", {
        name: /当前工作稿.*服务端已切换稿/u,
      }),
    ).toBeInTheDocument();
    expect(onCurrentDraftChanged).toHaveBeenCalledTimes(1);
  });

  it("marks locked Drafts as unavailable before activation", async () => {
    mocks.listDrafts.mockResolvedValue([
      summary(11, "缺页校准稿", true),
      summary(12, "已封存工作稿", false, { status: "locked" }),
    ]);
    render(
      <DraftSwitcher
        currentDraft={draft(11, "缺页校准稿")}
        onActivated={vi.fn()}
        projectId={42}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /当前工作稿.*缺页校准稿/u }),
    );
    const locked = await screen.findByRole("menuitem", {
      name: /已封存工作稿.*已锁定/u,
    });

    expect(locked).toBeDisabled();
    fireEvent.click(locked);
    expect(mocks.activateDraft).not.toHaveBeenCalled();
  });

  it("renders both scope entrances at a mobile viewport", () => {
    const previousWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    render(
      <div>
        <ProjectSwitcher currentProjectId={42} fallbackTitle="午夜回航" />
        <DraftSwitcher
          currentDraft={draft(11, "缺页校准稿")}
          onActivated={vi.fn()}
          projectId={42}
        />
      </div>,
    );

    expect(screen.getByRole("button", { name: /项目.*午夜回航/u })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /当前工作稿.*缺页校准稿/u }),
    ).toBeInTheDocument();
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: previousWidth,
    });
  });
});
