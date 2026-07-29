import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAgentPatchSet,
  executionStageLabel,
  listAgentMessages,
  sendAgentMessage,
} from "@/features/workflow/workbench-api";
import type { TaskEventView } from "@/lib/api-client";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("workbench Agent API adapter", () => {
  it("normalizes nested task, object references and patch sets from messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse([
          {
            message_id: 12,
            role: "assistant",
            status: "completed",
            content: "我找到了一个时间断点。",
            task: {
              task_run_id: 88,
              stage: "completed",
              status: "succeeded",
            },
            referenced_object_ids: ["evt_01"],
            patch_set: {
              patch_set_id: 31,
              reason_summary: "补充事件说明",
              status: "pending",
              base_draft_revision: 7,
              applied_to_revision: 8,
              is_stale: false,
              operations: [
                {
                  operation_id: 41,
                  object_ref: {
                    object_type: "event",
                    object_id: "evt_01",
                  },
                  field_path: "/description",
                  field_label: "事件说明",
                  old_value: "",
                  new_value: "渡轮在灯塔熄灭后改变航线。",
                  decision: "pending",
                },
              ],
              validator_issues: [],
            },
          },
        ]),
      ),
    );

    const messages = await listAgentMessages(3, 5, 9);

    expect(messages[0].task?.task_run_id).toBe(88);
    expect(messages[0].references).toEqual([{ object_id: "evt_01" }]);
    expect(messages[0].patch_set).toMatchObject({
      reason_summary: "补充事件说明",
      base_draft_revision: 7,
      applied_to_revision: 8,
      operations: [
        {
          operation_id: 41,
          field_label: "事件说明",
          decision: "pending",
        },
      ],
    });
  });

  it("uses a Chinese-safe label for an unknown execution stage", () => {
    const event = {
      stage: "preparing_internal_context",
    } as TaskEventView;

    expect(executionStageLabel(event)).toBe("正在处理卷宗");
    expect(executionStageLabel(event)).not.toContain(
      "preparing_internal_context",
    );
  });

  it("reads the task id from send response task instead of a legacy top-level field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          thread: {
            thread_id: 9,
            title: "检查时间线",
            is_pinned: false,
            status: "active",
          },
          user_message: {
            message_id: 11,
            role: "user",
            status: "completed",
            content: "检查时间线",
            task: null,
            referenced_object_ids: [],
            patch_set: null,
          },
          assistant_message: {
            message_id: 12,
            role: "assistant",
            status: "pending",
            content: "",
            task: { task_run_id: 88, stage: "queued" },
            referenced_object_ids: [],
            patch_set: null,
          },
          task: { task_run_id: 88, stage: "queued", status: "queued" },
        }),
      ),
    );

    const result = await sendAgentMessage(
      3,
      5,
      9,
      "检查时间线",
      "deepseek",
    );

    expect(result.task.task_run_id).toBe(88);
    expect(result.assistant_message.task?.stage).toBe("queued");
  });

  it("sends an empty operation list as an explicit reject-all decision", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        patch_set_id: 31,
        reason_summary: "补充事件说明",
        status: "rejected",
        base_draft_revision: 7,
        applied_to_revision: null,
        is_stale: false,
        operations: [],
        validator_issues: [],
        draft_revision: 7,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await applyAgentPatchSet(3, 5, 31, [], 7);
    const request = fetchMock.mock.calls[0][1] as RequestInit;

    expect(JSON.parse(String(request.body))).toEqual({
      operation_ids: [],
      expected_revision: 7,
    });
    expect(result.status).toBe("rejected");
    expect(result.draft_revision).toBe(7);
  });
});
