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
  it("switches between person and location lanes with certainty and issue overlays", () => {
    const seed = timelineSeed();
    const onSelectEvent = vi.fn();
    const { container } = render(
      <TimelineOverview
        issueStatuses={{ "ISSUE-TIME-006": "open" }}
        onSelectEvent={onSelectEvent}
        seed={seed}
        selectedEventId="EV-1812"
        validationStatus="failed"
      />,
    );

    expect(screen.getByRole("list", { name: "时间确定性诊断" })).toHaveTextContent(
      "准确4",
    );
    fireEvent.click(screen.getByRole("button", { name: "人物泳道" }));

    const personLane = screen.getByTestId("timeline-lane-PER-004");
    expect(personLane).toHaveAttribute("data-selected", "true");
    expect(personLane).toHaveTextContent("林岚");
    expect(
      container.querySelector('[data-issue="ISSUE-TIME-006"]'),
    ).not.toBeNull();
    fireEvent.click(
      within(personLane).getByRole("button", { name: /林岚，林岚进入检修通道/ }),
    );
    expect(onSelectEvent).toHaveBeenCalledWith("EV-1812");

    fireEvent.click(screen.getByRole("button", { name: "地点泳道" }));
    expect(screen.getByTestId("timeline-lane-LOC-007")).toHaveTextContent(
      "07 号检修通道",
    );
    fireEvent.click(screen.getByRole("button", { name: "确定性叠层" }));
    expect(
      screen.queryByRole("list", { name: "时间确定性诊断" }),
    ).not.toBeInTheDocument();
  });

  it("shows complete event readings without exposing timezone or seconds", () => {
    const seed = timelineSeed();
    seed.timelineEvents[0] = {
      ...seed.timelineEvents[0],
      time: "2034-11-18T09:00:00+08:00",
    };

    render(
      <TimelineOverview
        issueStatuses={{}}
        onSelectEvent={vi.fn()}
        seed={seed}
        selectedEventId={seed.timelineEvents[0].id}
        validationStatus="passed"
      />,
    );

    expect(screen.getAllByText("11-18 09:00").length).toBeGreaterThan(0);
    expect(screen.queryByText(/09:00:00\+08:00/)).not.toBeInTheDocument();
    expect(screen.getByText("事件发生时间")).toBeInTheDocument();
    expect(screen.getByText("故事发生时间轴")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("timeline-proportional-axis")).getByRole(
        "button",
        { name: new RegExp(`${seed.timelineEvents[0].label}，11-18 09:00`, "u") },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        new RegExp(`${seed.timelineEvents[0].label} · 11-18 09:00 ·`, "u"),
      ),
    ).toBeInTheDocument();
  });

  it("uses date-only ticks when a multi-day axis lands on midnight", () => {
    const seed = timelineSeed();
    seed.timelineEvents = seed.timelineEvents.map((event, index) => {
      const value = `2034-11-${String(18 + index * 3).padStart(2, "0")}T00:00`;
      return {
        ...event,
        time: value,
        start: value,
        sortKey: `${value}:00.000000`,
        source: {
          time: { kind: "exact", value, precision: "minute" },
        },
      } as never;
    });

    render(
      <TimelineOverview
        issueStatuses={{}}
        onSelectEvent={vi.fn()}
        seed={seed}
        selectedEventId={seed.timelineEvents[0].id}
        validationStatus="passed"
      />,
    );

    const ticks = screen.getAllByTestId("timeline-axis-tick");
    expect(ticks.length).toBeGreaterThan(1);
    expect(ticks.every((tick) => /^11-\d{2}$/.test(tick.textContent ?? ""))).toBe(
      true,
    );
  });

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

  it("keeps every event visible on desktop when an older draft has no resolvable time anchor", () => {
    const seed = timelineSeed();
    seed.timelineEvents = seed.timelineEvents.map((event) => ({
      ...event,
      time: "时间未定",
      start: null,
      end: null,
      sortKey: null,
      timeProjection: "unresolved",
      source: { time: { kind: "unknown" } },
    })) as never;

    render(
      <TimelineOverview
        issueStatuses={{}}
        onSelectEvent={vi.fn()}
        seed={seed}
        selectedEventId={seed.timelineEvents[0].id}
        validationStatus="unavailable"
      />,
    );

    expect(screen.getByText("没有可放入比例轴的绝对时间")).toBeInTheDocument();
    expect(screen.getByText(/缺少可解析到作品内壁钟时间的锚点/)).toBeInTheDocument();
    const unresolvedList = screen.getByRole("list", { name: "未解析事件清单" });
    for (const event of seed.timelineEvents) {
      expect(unresolvedList).toHaveTextContent(event.label);
    }
  });
});
