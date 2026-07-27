import { describe, expect, it } from "vitest";

import {
  agentThreadMatchesQuery,
  canCompilePrototype,
  createDefaultPrototypeState,
  hasBlockingIssue,
  isDraftReadOnly,
  sortAgentThreads,
} from "@/lib/prototype-model";
import { prototypeReducer } from "@/store/prototype-store";

describe("prototype state flow", () => {
  it("keeps the original idea when adopting an Agent suggestion", () => {
    const initial = createDefaultPrototypeState();
    const withNewOriginal = prototypeReducer(initial, {
      type: "set-idea-original",
      value: "用户写下的原始创意",
    });
    const suggested = prototypeReducer(withNewOriginal, {
      type: "generate-suggestion",
    });
    const adopted = prototypeReducer(suggested, {
      type: "adopt-suggestion",
    });

    expect(adopted.idea.original).toBe("用户写下的原始创意");
    expect(adopted.idea.working).toBe(initial.idea.suggestion);
    expect(adopted.idea.suggestionStatus).toBe("adopted");
  });

  it("marks validation stale whenever an event is edited", () => {
    const initial = createDefaultPrototypeState();
    const edited = prototypeReducer(initial, {
      type: "update-event",
      id: "EVL-1823",
      field: "time",
      value: "18:24",
    });

    expect(edited.draft.revision).toBe(19);
    expect(edited.draft.lastSavedAt).toBe("待保存");
    expect(edited.validation.status).toBe("stale");
    expect(edited.compiler.status).toBe("blocked");
  });

  it("requires an approved patch and an explicit rerun before compilation", () => {
    const initial = createDefaultPrototypeState();
    expect(hasBlockingIssue(initial)).toBe(true);
    expect(canCompilePrototype(initial)).toBe(false);

    const patched = prototypeReducer(initial, { type: "apply-patch" });
    expect(patched.validation.status).toBe("stale");
    expect(
      patched.validation.issues.find((issue) => issue.id === "VAL-KNOW-001")
        ?.status,
    ).toBe("pending-revalidation");
    expect(canCompilePrototype(patched)).toBe(false);

    const running = prototypeReducer(patched, { type: "start-validation" });
    expect(running.validation.status).toBe("running");

    const validated = prototypeReducer(running, {
      type: "complete-validation",
    });
    expect(validated.validation.status).toBe("fresh");
    expect(
      validated.validation.issues.find(
        (issue) => issue.id === "VAL-KNOW-001",
      )?.status,
    ).toBe("resolved");
    expect(hasBlockingIssue(validated)).toBe(false);
    expect(canCompilePrototype(validated)).toBe(true);
    expect(validated.compiler.status).toBe("idle");

    const building = prototypeReducer(validated, { type: "start-compile" });
    expect(building.compiler.status).toBe("building");

    const completed = prototypeReducer(building, {
      type: "complete-compile",
    });
    expect(completed.compiler.status).toBe("completed");
  });

  it("locks the Draft while an Agent mutation task is running", () => {
    const initial = createDefaultPrototypeState();
    const preview = prototypeReducer(initial, {
      type: "prepare-agent-task",
      label: "补全缺口",
      instruction: "扫描整个 Draft 并生成结构化变更集。",
      mutationTask: true,
      taskType: "gaps",
    });
    const running = prototypeReducer(preview, { type: "start-agent-task" });

    expect(isDraftReadOnly(running)).toBe(true);
    expect(running.agent.baseRevision).toBe(18);

    const blockedEdit = prototypeReducer(running, {
      type: "update-event",
      id: "EVL-1823",
      field: "title",
      value: "不应写入",
    });
    expect(blockedEdit).toBe(running);

    const review = prototypeReducer(running, {
      type: "complete-agent-task",
    });
    expect(isDraftReadOnly(review)).toBe(false);
    expect(review.agent.status).toBe("review");
    expect(review.agent.changes).toHaveLength(3);
  });

  it("marks an Agent change set stale when its base revision has moved", () => {
    const initial = createDefaultPrototypeState();
    const preview = prototypeReducer(initial, {
      type: "prepare-agent-task",
      label: "生成变更集",
      instruction: "修复全局连贯性问题。",
      mutationTask: true,
      taskType: "patch",
    });
    const running = prototypeReducer(preview, { type: "start-agent-task" });
    const review = prototypeReducer(running, {
      type: "complete-agent-task",
    });
    const edited = prototypeReducer(review, {
      type: "update-event",
      id: "EVL-1800",
      field: "time",
      value: "18:01",
    });
    const stale = prototypeReducer(edited, {
      type: "apply-agent-changes",
    });

    expect(stale.agent.status).toBe("stale");
    expect(stale.draft.revision).toBe(19);
  });

  it("applies approved Agent changes and automatically validates the new revision", () => {
    const initial = createDefaultPrototypeState();
    const preview = prototypeReducer(initial, {
      type: "prepare-agent-task",
      label: "补全缺口",
      instruction: "补全知识状态与时间线缺口。",
      mutationTask: true,
      taskType: "gaps",
    });
    const running = prototypeReducer(preview, { type: "start-agent-task" });
    const review = prototypeReducer(running, {
      type: "complete-agent-task",
    });
    const validating = prototypeReducer(review, {
      type: "apply-agent-changes",
    });

    expect(validating.agent.status).toBe("validating");
    expect(validating.validation.status).toBe("running");
    expect(validating.draft.revision).toBe(19);

    const validated = prototypeReducer(validating, {
      type: "complete-validation",
    });
    const finished = prototypeReducer(validated, {
      type: "finish-agent-validation",
    });

    expect(finished.agent.status).toBe("completed");
    expect(finished.validation.status).toBe("fresh");
    expect(
      finished.validation.issues.find(
        (issue) => issue.id === "VAL-KNOW-001",
      )?.status,
    ).toBe("resolved");
    expect(
      finished.validation.issues.find(
        (issue) => issue.id === "VAL-TIME-006",
      )?.status,
    ).toBe("resolved");
  });

  it("creates an auditable thread as soon as an Agent task starts", () => {
    const initial = createDefaultPrototypeState();
    const preview = prototypeReducer(initial, {
      type: "prepare-agent-task",
      label: "全局审查",
      instruction: "检查谜底闭环。",
      mutationTask: false,
      taskType: "audit",
    });
    const running = prototypeReducer(preview, { type: "start-agent-task" });
    const thread = running.agent.history.find(
      (item) => item.id === running.agent.threadId,
    );

    expect(thread?.status).toBe("running");
    expect(thread?.instruction).toBe("检查谜底闭环。");

    const cancelled = prototypeReducer(running, {
      type: "cancel-agent-task",
    });
    expect(
      cancelled.agent.history.find((item) => item.id === running.agent.threadId)
        ?.status,
    ).toBe("cancelled");
  });

  it("only archives terminal Agent threads and restores them on demand", () => {
    const initial = createDefaultPrototypeState();
    const preview = prototypeReducer(initial, {
      type: "prepare-agent-task",
      label: "连贯性检查",
      instruction: "检查整个 Draft 的时间线。",
      mutationTask: false,
      taskType: "flow",
    });
    const running = prototypeReducer(preview, { type: "start-agent-task" });
    const blockedArchive = prototypeReducer(running, {
      type: "archive-agent-thread",
      id: running.agent.threadId,
    });
    expect(
      blockedArchive.agent.history.find(
        (item) => item.id === running.agent.threadId,
      )?.archived,
    ).toBe(false);

    const cancelled = prototypeReducer(running, {
      type: "cancel-agent-task",
    });
    const archived = prototypeReducer(cancelled, {
      type: "archive-agent-thread",
      id: running.agent.threadId,
    });
    expect(
      archived.agent.history.find((item) => item.id === running.agent.threadId)
        ?.archived,
    ).toBe(true);

    const restored = prototypeReducer(archived, {
      type: "restore-agent-thread",
      id: running.agent.threadId,
    });
    expect(
      restored.agent.history.find((item) => item.id === running.agent.threadId)
        ?.archived,
    ).toBe(false);
  });

  it("searches structured thread evidence and sorts attention before history", () => {
    const initial = createDefaultPrototypeState();
    const failed = initial.agent.history.find(
      (thread) => thread.status === "failed",
    );
    const favorite = initial.agent.history.find((thread) => thread.favorite);

    expect(failed && agentThreadMatchesQuery(failed, "AI-7712")).toBe(true);
    expect(favorite && agentThreadMatchesQuery(favorite, "VAL-0017")).toBe(
      true,
    );

    const sorted = sortAgentThreads(initial.agent.history);
    expect(sorted[0]?.status).toBe("failed");
    expect(sorted[1]?.favorite).toBe(true);
  });
});
