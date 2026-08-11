import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { defaultWorkbenchSeed } from "@/features/analyst-workbench/analyst-fixture";
import {
  ExposurePlanEditor,
  moveExposureEntry,
  workingExposureEntries,
} from "@/features/analyst-workbench/timeline/exposure-plan-editor";
import type { TimelineDisplayEvent } from "@/features/analyst-workbench/timeline/timeline-lanes";
import { ApiError, type ExposurePlanView } from "@/lib/api-client";

const api = vi.hoisted(() => ({
  fetchExposurePlan: vi.fn(),
  putExposurePlan: vi.fn(),
}));

vi.mock("@/features/case-session/case-session-api", () => api);

const events = defaultWorkbenchSeed.timelineEvents.slice(0, 3) as TimelineDisplayEvent[];

function plan(
  revision = 0,
  orderedEvents: TimelineDisplayEvent[] = [],
): ExposurePlanView {
  return {
    plan_id: 17,
    draft_id: 9,
    revision,
    updated_at: "2026-08-11T13:30:00+08:00",
    entries: orderedEvents.map((event, index) => ({
      entry_key: `exposure_${event.id}`,
      sequence_no: index,
      title: event.label,
      note: null,
      refs: [{ object_type: "event", object_id: event.id }],
    })),
  };
}

function renderEditor() {
  return render(
    <ExposurePlanEditor
      draftId={9}
      editable
      events={events}
      onSelectEvent={vi.fn()}
      projectId={42}
      selectedEventId={events[0].id}
    />,
  );
}

beforeEach(() => {
  api.fetchExposurePlan.mockReset().mockResolvedValue(plan());
  api.putExposurePlan.mockReset();
});

afterEach(cleanup);

describe("exposure plan working order", () => {
  it("prepares an unsaved factual-order draft for revision zero", () => {
    const initial = workingExposureEntries(plan(), events);

    expect(initial.map((entry) => entry.refs[0].object_id)).toEqual(
      events.map((event) => event.id),
    );
    expect(plan().entries).toHaveLength(0);
    expect(moveExposureEntry(initial, 1, -1).map((entry) => entry.entry_key)).toEqual([
      initial[1].entry_key,
      initial[0].entry_key,
      initial[2].entry_key,
    ]);
    expect(moveExposureEntry(initial, 0, -1)).toBe(initial);
  });
});

describe("exposure plan editor", () => {
  it("shows the revision-zero factual order without persisting it", async () => {
    renderEditor();

    await screen.findByRole("button", { name: "披露计划R0" });
    fireEvent.click(screen.getByRole("button", { name: "披露计划R0" }));

    const list = screen.getByRole("list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(events.length);
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent(events[0].label);
    expect(screen.getByText("未保存")).toBeInTheDocument();
    expect(api.putExposurePlan).not.toHaveBeenCalled();
  });

  it("moves entries and saves against the plan revision only", async () => {
    api.putExposurePlan.mockResolvedValueOnce(plan(1, [events[1], events[0], events[2]]));
    renderEditor();

    fireEvent.click(await screen.findByRole("button", { name: "披露计划R0" }));
    fireEvent.click(screen.getByRole("button", { name: `上移 ${events[1].label}` }));
    fireEvent.change(screen.getByLabelText(`${events[1].label}的披露说明`), {
      target: { value: "先让读者看到异常" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存披露顺序" }));

    await waitFor(() => expect(api.putExposurePlan).toHaveBeenCalledTimes(1));
    expect(api.putExposurePlan).toHaveBeenCalledWith(
      42,
      9,
      0,
      expect.arrayContaining([
        expect.objectContaining({
          entry_key: `exposure_${events[1].id}`,
          note: "先让读者看到异常",
        }),
      ]),
    );
    expect(
      api.putExposurePlan.mock.calls[0][3].map(
        (entry: { entry_key: string }) => entry.entry_key,
      ),
    ).toEqual([
      `exposure_${events[1].id}`,
      `exposure_${events[0].id}`,
      `exposure_${events[2].id}`,
    ]);
    expect(await screen.findByRole("button", { name: "披露计划R1" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Draft revision 保持不变",
    );
  });

  it("reloads the authoritative plan after a revision conflict", async () => {
    api.fetchExposurePlan
      .mockResolvedValueOnce(plan(1, events))
      .mockResolvedValueOnce(plan(2, [events[2], events[0], events[1]]));
    api.putExposurePlan.mockRejectedValueOnce(
      new ApiError(409, {
        code: "exposure_plan_revision_conflict",
        message: "Exposure Plan revision is stale",
        details: {},
      }),
    );
    renderEditor();

    fireEvent.click(await screen.findByRole("button", { name: "披露计划R1" }));
    fireEvent.click(screen.getByRole("button", { name: `下移 ${events[0].label}` }));
    fireEvent.click(screen.getByRole("button", { name: "保存披露顺序" }));

    await waitFor(() => expect(api.fetchExposurePlan).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", { name: "披露计划R2" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("已载入最新版");
    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent(events[2].label);
  });

  it("keeps the plan entry and move actions available at a 390px viewport", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    renderEditor();

    const trigger = await screen.findByRole("button", { name: "披露计划R0" });
    fireEvent.click(trigger);

    expect(screen.getByRole("dialog", { name: "编辑披露计划" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `下移 ${events[0].label}` })).toBeEnabled();
    expect(screen.getByRole("button", { name: "保存披露顺序" })).toBeEnabled();
  });
});
