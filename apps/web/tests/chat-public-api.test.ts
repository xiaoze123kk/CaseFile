import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyAgentPatchSet,
  cancelAgentRun,
  getAgentRun,
  listAgentMessages,
  listAgentRunEvents,
  sendAgentRoutingFeedback,
  simulateAgentPatchSet,
  streamAgentRunEvents,
} from "@/lib/api-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("chat public API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses public message, run and event endpoints", async () => {
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(request);
      if (url.endsWith("/messages?after_sequence=0")) return jsonResponse([]);
      if (url.endsWith("/cancel")) {
        return jsonResponse({
          run_id: 8,
          status: "cancelling",
          activity: "finalizing",
          cancellable: false,
          failure: null,
        });
      }
      if (url.endsWith("/events?after_sequence=3")) return jsonResponse([]);
      expect(init?.method).toBeUndefined();
      return jsonResponse({
        run_id: 8,
        status: "running",
        activity: "reading",
        cancellable: true,
        failure: null,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await listAgentMessages(1, 2, 3);
    await getAgentRun(1, 2, 8);
    await cancelAgentRun(1, 2, 8);
    await listAgentRunEvents(1, 2, 8, 3);

    const urls = fetchMock.mock.calls.map(([request]) => String(request));
    expect(urls[0]).toContain(
      "/projects/2/agent/threads/3/messages?after_sequence=0",
    );
    expect(urls[1]).toContain("/projects/2/agent/runs/8");
    expect(urls[2]).toContain("/projects/2/agent/runs/8/cancel");
    expect(urls[3]).toContain("/projects/2/agent/runs/8/events?after_sequence=3");
  });

  it("sends only public patch and interpretation request fields", async () => {
    const fetchMock = vi.fn(async (request: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(request);
      if (url.endsWith("/routing-feedback")) {
        return jsonResponse({
          message_id: 9,
          acknowledged: true,
          interpretation: "change_request",
        });
      }
      if (url.endsWith("/simulate")) {
        return jsonResponse({
          patch_id: 12,
          can_apply: true,
          blockers: [],
          warnings: [],
          requires_author_confirmation: false,
          confirmation_token: null,
        });
      }
      return jsonResponse({
        patch: {
          patch_id: 12,
          title: "修改建议",
          summary: "你要求的修改 1 项。",
          status: "applied",
          review_rule: "atomic",
          base_revision: 4,
          impact: {
            summary: "共涉及 1 项卷宗修改。",
            affected_change_count: 1,
            has_deletions: false,
          },
          changes: [],
          actions: { can_simulate: false, can_undo: true, can_redo: false },
        },
        review: {
          patch_id: 12,
          can_apply: true,
          blockers: [],
          warnings: [],
          requires_author_confirmation: false,
          confirmation_token: null,
        },
        revision: 5,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await sendAgentRoutingFeedback(1, 2, 3, 9, "change_request");
    await simulateAgentPatchSet(1, 2, 12, 4, 4, [21], ["warning_1"], "已确认");
    await applyAgentPatchSet(
      1,
      2,
      12,
      4,
      4,
      [21],
      "confirmation_1",
      ["warning_1"],
      "已确认",
    );

    const bodies = fetchMock.mock.calls.map(([, init]) =>
      JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>,
    );
    expect(bodies).toEqual([
      { interpretation: "change_request" },
      {
        expected_draft_id: 4,
        base_revision: 4,
        change_ids: [21],
        accepted_warning_ids: ["warning_1"],
        confirmation_note: "已确认",
      },
      {
        expected_draft_id: 4,
        expected_revision: 4,
        change_ids: [21],
        confirmation_token: "confirmation_1",
        accepted_warning_ids: ["warning_1"],
        confirmation_note: "已确认",
      },
    ]);
    const serialized = JSON.stringify(bodies);
    expect(serialized).not.toMatch(
      /operation_ids|target_finding_ids|accepted_debt|impact_hash|route_source/,
    );
  });

  it("resumes enhanced SSE with Last-Event-ID and deduplicates replay", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'id: 4\nevent: run.activity\ndata: {"sequence":4,"event":"run.activity","activity":"reading"}\n\n',
          ),
        );
        controller.enqueue(
          encoder.encode(
            'id: 7\nevent: run.activity\ndata: {"sequence":7,"event":"run.activity","activity":"reading"}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.fn(
      async (request: RequestInfo | URL, init?: RequestInit) => {
        void request;
        void init;
        return new Response(body, { status: 200 });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];

    const cursor = await streamAgentRunEvents(
      1,
      2,
      8,
      (event) => events.push(event),
      new AbortController().signal,
      4,
    );

    expect(cursor).toBe(7);
    expect(events).toEqual([
      { sequence: 7, event: "run.activity", activity: "reading" },
    ]);
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers["Last-Event-ID"]).toBe("4");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("feedback_version=2");
    expect(JSON.stringify(events)).not.toContain("canary");
  });
  it("fails closed on unknown SSE events", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('data: {"sequence":5,"event":"internal.debug"}\n\n')));
    const onEvent = vi.fn();
    await expect(streamAgentRunEvents(1, 2, 8, onEvent, new AbortController().signal)).rejects.toThrow("无法识别");
    expect(onEvent).not.toHaveBeenCalled();
  });
});
