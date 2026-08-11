import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { defaultWorkbenchSeed, type WorkbenchSeed } from "@/features/analyst-workbench/analyst-fixture";
import { TimelineOverview } from "@/features/analyst-workbench/timeline/timeline-overview";
import type {
  TimelineTemporalPosition,
  TimelineTimePreviewView,
} from "@/lib/api-client";

afterEach(cleanup);

function timelineSeed(): WorkbenchSeed {
  const seed = JSON.parse(JSON.stringify(defaultWorkbenchSeed)) as WorkbenchSeed;
  seed.timelineEvents = seed.timelineEvents.map((event, index) => {
    const value = `2034-11-18T21:${String(4 + index * 6).padStart(2, "0")}`;
    return {
      ...event,
      start: value,
      end: null,
      precision: "minute",
      sortKey: `${value}:00.000000`,
      source: {
        time: { kind: "exact", value, precision: "minute" },
      },
    } as never;
  });
  return seed;
}

function preview(
  eventId: string,
  proposedTime: TimelineTemporalPosition,
  canConfirm = true,
): TimelineTimePreviewView {
  return {
    draft_id: 9,
    base_revision: 7,
    event_id: eventId,
    before_time: {
      kind: "exact",
      value: "2034-11-18T21:04",
      precision: "minute",
    },
    proposed_time: proposedTime,
    can_confirm: canConfirm,
    order_change: {
      from_index: 0,
      to_index: 1,
      crossed_event_ids: ["evt_gate_ping"],
    },
    relative_dependent_event_ids: [],
    affected_event_ids: [eventId, "evt_gate_ping"],
    validation: {
      status: canConfirm ? "passed" : "failed",
      issue_count: canConfirm ? 0 : 1,
      issues: canConfirm
        ? []
        : [{ code: "invalid_time_range", path: "/events/0/time/end", message: "结束时间不能早于开始时间" }],
    },
  };
}

describe("editable proportional timeline", () => {
  it("previews a keyboard move before confirming the Current Draft write", async () => {
    const seed = timelineSeed();
    const selected = seed.timelineEvents[0];
    const onPreviewTime = vi.fn(async (eventId, time) => preview(eventId, time));
    const onConfirmTime = vi.fn(async () => "saved" as const);
    render(
      <TimelineOverview
        editable
        issueStatuses={{}}
        onConfirmTime={onConfirmTime}
        onPreviewTime={onPreviewTime}
        onSelectEvent={vi.fn()}
        seed={seed}
        selectedEventId={selected.id}
        validationStatus="passed"
      />,
    );

    const axis = screen.getByTestId("timeline-proportional-axis");
    fireEvent.keyDown(
      within(axis).getByRole("button", { name: new RegExp(selected.label) }),
      { key: "ArrowRight" },
    );

    await waitFor(() => expect(onPreviewTime).toHaveBeenCalledTimes(1));
    expect(onPreviewTime.mock.calls[0][1]).toEqual({
      kind: "exact",
      value: "2034-11-18T21:05",
      precision: "minute",
    });
    expect(
      await screen.findByRole("dialog", { name: "时间修改影响预览" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认写入 Current Draft" }));
    await waitFor(() => expect(onConfirmTime).toHaveBeenCalledTimes(1));
  });

  it("edits unknown time without fabricating a placeholder date", async () => {
    const seed = timelineSeed();
    const selected = seed.timelineEvents[0];
    const onPreviewTime = vi.fn(async (eventId, time) => preview(eventId, time));
    render(
      <TimelineOverview
        editable
        issueStatuses={{}}
        onConfirmTime={vi.fn(async () => "saved" as const)}
        onPreviewTime={onPreviewTime}
        onSelectEvent={vi.fn()}
        seed={seed}
        selectedEventId={selected.id}
        validationStatus="passed"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑所选时间" }));
    fireEvent.change(screen.getByLabelText("语义类型"), {
      target: { value: "unknown" },
    });
    expect(screen.getByText(/未知时间不携带占位日期/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看影响" }));

    await waitFor(() =>
      expect(onPreviewTime).toHaveBeenCalledWith(selected.id, { kind: "unknown" }),
    );
  });
});
