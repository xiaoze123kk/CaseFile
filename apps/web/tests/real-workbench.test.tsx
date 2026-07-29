import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AgentPatchSuggestionCard,
  agentReferenceTarget,
  recoverActiveTaskRunId,
} from "@/features/workflow/agent-workspace";
import { FactTimeline } from "@/features/workflow/fact-timeline";
import { ObjectTree } from "@/features/workflow/object-tree";
import {
  resolveObjectRef,
  timelineEntries,
  type WorkbenchObject,
} from "@/features/workflow/workbench-model";
import type { CaseFileDocument } from "@/lib/api-client";
import type {
  AgentMessageView,
  AgentPatchSetView,
} from "@/features/workflow/workbench-api";

function caseFileDocument(): CaseFileDocument {
  return {
    resolution_specs: [
      {
        id: "res_internal_01",
        object_type: "resolution_spec",
        title: "真正的航线改变者",
        description: "用于约束最终答案。",
      },
    ],
    entities: [
      {
        id: "ent_internal_01",
        object_type: "entity",
        name: "摆渡人",
        description: "午夜航班的值守人员。",
      },
    ],
    relationships: [],
    locations: [],
    events: [
      {
        id: "evt_late",
        object_type: "event",
        title: "航线发生偏移",
        description: "渡轮驶离固定航道。",
        time: {
          start: "2026-07-29T00:30:00+08:00",
          precision: "minute",
        },
      },
      {
        id: "evt_unknown",
        object_type: "event",
        title: "灯塔熄灭",
        description: "具体时刻尚待确认。",
        time: { precision: "unknown" },
      },
      {
        id: "evt_early",
        object_type: "event",
        title: "乘客登船",
        description: "最后一名乘客完成登船。",
        time: {
          start: "2026-07-28T23:50:00+08:00",
          precision: "minute",
        },
      },
    ],
    information_units: [],
    claims: [],
    hypotheses: [],
    reasoning_paths: [],
    constraints: [],
    structure_locks: [],
  } as unknown as CaseFileDocument;
}

function patchSet(
  overrides: Partial<AgentPatchSetView> = {},
): AgentPatchSetView {
  return {
    patch_set_id: 31,
    reason_summary: "补充事件说明",
    status: "pending",
    base_draft_revision: 7,
    applied_to_revision: null,
    is_stale: false,
    operations: [
      {
        operation_id: 41,
        object_ref: {
          object_type: "event",
          object_id: "evt_late",
        },
        field_path: "/description",
        old_value: "",
        new_value: "渡轮驶离固定航道。",
        decision: "pending",
      },
    ],
    validator_issues: [],
    ...overrides,
  };
}

describe("real workbench", () => {
  it("sorts factual events chronologically and keeps unknown time last", () => {
    const document = caseFileDocument();
    const entries = timelineEntries(
      document.events as unknown as WorkbenchObject[],
    );

    expect(entries.map(({ event }) => event.id)).toEqual([
      "evt_early",
      "evt_late",
      "evt_unknown",
    ]);
    expect(entries.at(-1)?.timeLabel).toBe("时间待定");
  });

  it("resolves object references to business labels instead of exposing ids", () => {
    const document = caseFileDocument();

    expect(
      resolveObjectRef(document, {
        object_type: "entity",
        object_id: "ent_internal_01",
      })?.label,
    ).toBe("摆渡人");
  });

  it("opens an id-only Agent event reference in the factual timeline", () => {
    const target = agentReferenceTarget(caseFileDocument(), {
      object_id: "evt_late",
    });

    expect(target).toEqual({
      selection: {
        collection: "events",
        objectId: "evt_late",
      },
      preferTimeline: true,
    });
  });

  it("recovers only queued or running tasks from loaded thread messages", () => {
    const messages = [
      {
        message_id: 1,
        status: "completed",
        task: { task_run_id: 71, status: "succeeded" },
      },
      {
        message_id: 2,
        status: "pending",
        task: { task_run_id: 72, status: "running" },
      },
    ] as AgentMessageView[];

    expect(recoverActiveTaskRunId(messages)).toBe(72);
    expect(
      recoverActiveTaskRunId([
        {
          ...messages[0],
          task: { task_run_id: 71, status: "succeeded" },
        },
      ]),
    ).toBeNull();
  });

  it("only offers patch-set undo at its exact applied draft revision", () => {
    const commonProps = {
      actorId: 5,
      document: caseFileDocument(),
      onDraftChanged: vi.fn(),
      onRegenerate: vi.fn(),
      onRequestRepair: vi.fn(),
      patchSet: patchSet({
        status: "applied",
        applied_to_revision: 8,
      }),
      projectId: 3,
    };
    const { rerender } = render(
      <AgentPatchSuggestionCard {...commonProps} currentRevision={8} />,
    );

    expect(
      screen.getByRole("button", { name: "撤销本批修改" }),
    ).toBeInTheDocument();

    rerender(
      <AgentPatchSuggestionCard {...commonProps} currentRevision={9} />,
    );

    expect(
      screen.queryByRole("button", { name: "撤销本批修改" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("卷宗已有后续修改，不能直接撤销本批"),
    ).toBeInTheDocument();
  });

  it("never exposes an unmapped snake-case patch field name", () => {
    render(
      <AgentPatchSuggestionCard
        actorId={5}
        currentRevision={7}
        document={caseFileDocument()}
        onDraftChanged={vi.fn()}
        onRegenerate={vi.fn()}
        onRequestRepair={vi.fn()}
        patchSet={patchSet({
          operations: [
            {
              operation_id: 42,
              object_ref: {
                object_type: "event",
                object_id: "evt_late",
              },
              field_path: "/preparing_internal_context",
              old_value: "",
              new_value: "新值",
              decision: "pending",
            },
          ],
        })}
        projectId={3}
      />,
    );

    expect(screen.getByText("对象字段")).toBeInTheDocument();
    expect(
      screen.queryByText("preparing_internal_context"),
    ).not.toBeInTheDocument();
  });

  it("shows objects below their collection and selects the business object", () => {
    const document = caseFileDocument();
    const onSelect = vi.fn();

    render(
      <ObjectTree
        document={document}
        onSelect={onSelect}
        selected={{
          collection: "resolution_specs",
          objectId: "res_internal_01",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /摆渡人/ }));

    expect(onSelect).toHaveBeenCalledWith({
      collection: "entities",
      objectId: "ent_internal_01",
    });
    expect(screen.queryByText("ent_internal_01")).not.toBeInTheDocument();
  });

  it("connects factual timeline records to selection and Agent discussion", () => {
    const document = caseFileDocument();
    const events = document.events as unknown as WorkbenchObject[];
    const onSelect = vi.fn();
    const onDiscuss = vi.fn();

    render(
      <FactTimeline
        events={events}
        onDiscuss={onDiscuss}
        onSelect={onSelect}
        selectedObjectId={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /乘客登船/ }));
    fireEvent.click(
      screen.getAllByRole("button", { name: /与 Agent 讨论/ })[0],
    );

    expect(onSelect).toHaveBeenCalledWith({
      collection: "events",
      objectId: "evt_early",
    });
    expect(onDiscuss).toHaveBeenCalledWith(
      expect.objectContaining({ id: "evt_early" }),
    );
  });
});
