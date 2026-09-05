import type { PublicAgentEvent, PublicAgentRun, PublicGoalSession } from "@casefile/contracts";

export type ActivityDetail = Extract<PublicAgentEvent, { event: "run.activity_detail" }>;
export interface RunFeedback {
  sequence: number;
  activities: ActivityDetail[];
  context: Extract<PublicAgentEvent, { event: "run.context" }>["context_state"] | null;
  verification: Extract<PublicAgentEvent, { event: "run.verification" }> | null;
  preview: { sequence: number; text: string; invalidated: boolean; discarded: boolean; ready: boolean } | null;
  gap: boolean;
}
export const emptyFeedback = (): RunFeedback => ({ sequence: 0, activities: [], context: null, verification: null, preview: null, gap: false });

export function reduceFeedback(state: RunFeedback, event: PublicAgentEvent): RunFeedback {
  if (event.sequence <= state.sequence) return state;
  const next = { ...state, sequence: event.sequence };
  switch (event.event) {
    case "run.completed":
    case "run.failed":
    case "run.cancelled":
      next.activities = state.activities.map((item) => item.status === "started" ? { ...item, status: "failed" } : item);
      break;
    case "run.activity_detail":
      next.activities = [...state.activities.filter((item) => item.activity_id !== event.activity_id), event];
      break;
    case "run.context": next.context = event.context_state; break;
    case "run.verification":
      if (state.verification?.verification_status !== "blocked" || event.verification_status === "blocked") next.verification = event;
      break;
    case "message.preview_started":
      next.preview = { sequence: event.sequence, text: "", invalidated: false, discarded: false, ready: false };
      next.gap = false;
      break;
    case "message.preview_delta":
      if (!state.preview || state.preview.sequence !== event.preview_sequence ||
          Array.from(state.preview.text).length !== event.offset || state.preview.invalidated) {
        next.gap = true;
      } else {
        next.preview = { ...state.preview, text: state.preview.text + event.text, ready: event.final ?? false };
      }
      break;
    case "message.preview_invalidated":
      if (state.preview) next.preview = { ...state.preview, invalidated: true, discarded: event.discard, text: event.discard ? "" : state.preview.text };
      break;
  }
  return next;
}

export const activityLabels: Record<NonNullable<PublicAgentRun["activity"]>, string> = {
  understanding: "理解你的要求", reading: "阅读卷宗", checking: "检查前后一致性",
  preparing_changes: "整理修改建议", finalizing: "整理回答",
};

export function goalLabel(goal: PublicGoalSession): string {
  const labels: Record<PublicGoalSession["status"], string> = {
    interpreting: "正在理解目标", running: "正在处理当前目标",
    waiting_clarification: "等待你补充说明", waiting_patch_review: "等待你审阅修改建议",
    stale: "工作稿已变化，需要确认后继续", completed: "目标已完成",
    cancelled: "目标已停止", superseded: "已替换为新目标", failed: "目标未能完成",
  };
  return labels[goal.status];
}

export function activeFeedbackRefs(feedback: RunFeedback | undefined, draftId: number, revision: number): string[] {
  const activity = feedback?.activities.findLast((item) => item.object_ids.length > 0);
  return activity && activity.status !== "failed" && activity.draft_id === draftId &&
    activity.draft_revision === revision ? activity.object_ids : [];
}
