"use client";

import {
  createContext,
  type Dispatch,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
} from "react";

import {
  type AgentThread,
  type AgentThreadTaskType,
  type BriefTextField,
  canCompilePrototype,
  createDefaultPrototypeState,
  type DraftEvent,
  type PrototypeState,
  isTerminalAgentThread,
  isDraftReadOnly,
} from "@/lib/prototype-model";
import {
  buildReasoningFixture,
  createDefaultReasoningState,
  getSelectedReasoningChanges,
  type PrototypeReasoningState,
  type ReasoningMode,
  type ReasoningNodePosition,
} from "@/lib/reasoning-prototype";

const STORAGE_KEY = "casefile.prototype.v4";
const LEGACY_STORAGE_KEY = "casefile.prototype.v3";

export type PrototypeAction =
  | { type: "hydrate"; state: PrototypeState }
  | { type: "set-idea-original"; value: string }
  | { type: "generate-suggestion" }
  | { type: "adopt-suggestion" }
  | { type: "reject-suggestion" }
  | { type: "update-brief"; field: BriefTextField; value: string }
  | { type: "toggle-decision"; id: string }
  | { type: "approve-brief" }
  | { type: "select-event"; id: string }
  | {
      type: "update-event";
      id: string;
      field: keyof Pick<
        DraftEvent,
        | "time"
        | "title"
        | "description"
        | "location"
        | "phase"
        | "participants"
        | "visibility"
        | "importance"
      >;
      value: string;
    }
  | { type: "save-event" }
  | {
      type: "prepare-agent-task";
      label: string;
      instruction: string;
      mutationTask: boolean;
      taskType: AgentThreadTaskType;
      sourceThreadId?: string;
    }
  | { type: "start-agent-task" }
  | {
      type: "update-agent-task";
      progress: number;
      stage: string;
      readObjectIds: string[];
    }
  | { type: "complete-agent-task" }
  | { type: "cancel-agent-task" }
  | { type: "toggle-agent-change"; id: string }
  | { type: "select-all-agent-changes"; selected: boolean }
  | { type: "reject-agent-changes" }
  | { type: "rebase-agent-task" }
  | { type: "apply-agent-changes" }
  | { type: "finish-agent-validation" }
  | { type: "reset-agent-session" }
  | { type: "toggle-agent-thread-favorite"; id: string }
  | { type: "archive-agent-thread"; id: string }
  | { type: "restore-agent-thread"; id: string }
  | { type: "rename-agent-thread"; id: string; label: string }
  | { type: "apply-patch" }
  | { type: "start-validation" }
  | { type: "complete-validation" }
  | {
      type: "set-compiler-profile";
      profile: PrototypeState["compiler"]["profile"];
    }
  | { type: "toggle-artifact"; id: string }
  | { type: "start-compile" }
  | { type: "complete-compile" }
  | { type: "set-reasoning-mode"; mode: ReasoningMode }
  | { type: "start-reasoning-run" }
  | { type: "update-reasoning-run"; progress: number; stage: string }
  | { type: "complete-reasoning-run" }
  | { type: "cancel-reasoning-run" }
  | { type: "fail-reasoning-run"; message: string }
  | { type: "open-reasoning-overview" }
  | { type: "select-reasoning-path"; id: string }
  | { type: "select-reasoning-node"; id: string }
  | { type: "select-reasoning-proposal"; id: string }
  | { type: "toggle-reasoning-proposal"; id: string }
  | { type: "select-all-reasoning-proposals"; selected: boolean }
  | { type: "reject-reasoning-proposal"; id: string }
  | { type: "apply-reasoning-proposals" }
  | { type: "toggle-reasoning-bundle"; id: string }
  | {
      type: "set-reasoning-positions";
      positions: Record<string, ReasoningNodePosition>;
    }
  | {
      type: "set-reasoning-node-position";
      id: string;
      position: ReasoningNodePosition;
    }
  | { type: "add-user-reasoning-node"; pathId: string }
  | {
      type: "connect-reasoning-nodes";
      pathId: string;
      source: string;
      target: string;
    }
  | { type: "rename-reasoning-node"; id: string; label: string }
  | { type: "reset-reasoning" }
  | { type: "reset" };

type LegacyPrototypeState = Omit<
  PrototypeState,
  "storageVersion" | "reasoning"
> & {
  storageVersion: 3;
};

function isStateRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function hydratePrototypeState(value: unknown): PrototypeState | null {
  if (!isStateRecord(value) || !("storageVersion" in value)) return null;

  if (
    value.storageVersion === 4 &&
    "agent" in value &&
    "reasoning" in value &&
    "draft" in value
  ) {
    return value as unknown as PrototypeState;
  }

  if (
    value.storageVersion === 3 &&
    "agent" in value &&
    "draft" in value &&
    isStateRecord(value.draft) &&
    typeof value.draft.revision === "number"
  ) {
    const legacy = value as unknown as LegacyPrototypeState;
    return {
      ...legacy,
      storageVersion: 4,
      reasoning: createDefaultReasoningState(legacy.draft.revision),
    };
  }

  return null;
}

function markReasoningStale(
  reasoning: PrototypeReasoningState,
  nextRevision: number,
): PrototypeReasoningState {
  if (
    reasoning.status === "idle" ||
    reasoning.status === "cancelled" ||
    reasoning.status === "failed" ||
    reasoning.baseRevision >= nextRevision
  ) {
    return reasoning;
  }
  return {
    ...reasoning,
    status: "stale",
    stage: `推理图基于 REV.${reasoning.baseRevision}，当前 Draft 已到 REV.${nextRevision}`,
  };
}

function nextRunId(current: string): string {
  const numeric = Number.parseInt(current.replace(/\D/g, ""), 10);
  return `VAL-${String((Number.isFinite(numeric) ? numeric : 18) + 1).padStart(4, "0")}`;
}

function buildAgentChanges(state: PrototypeState) {
  const protectedEvent = state.draft.events.find(
    (event) => event.id === "EVL-1823",
  );
  const reactorEvent = state.draft.events.find(
    (event) => event.id === "EVL-1812",
  );
  const rollbackEvent = state.draft.events.find(
    (event) => event.id === "EVL-1825",
  );

  return [
    {
      id: "AGT-CHG-01",
      objectId: "EVL-1823",
      objectLabel: "AI 启动保护协议",
      field: "visibility" as const,
      before: protectedEvent?.visibility ?? "AI 核心 + 全部角色",
      after: "AI 核心 + 秦彻",
      rationale: "阻止林望在获得第五人权限记录前读取该事实。",
      selected: true,
      status: "pending" as const,
    },
    {
      id: "AGT-CHG-02",
      objectId: "EVL-1812",
      objectLabel: "反应堆异常",
      field: "phase" as const,
      before: reactorEvent?.phase ?? "阶段 02 · 异常出现",
      after: "阶段 03 · 异常升级",
      rationale: "让真实时间锚点与叙事阶段转换保持一致。",
      selected: true,
      status: "pending" as const,
    },
    {
      id: "AGT-CHG-03",
      objectId: "EVL-1825",
      objectLabel: "空间站状态回滚",
      field: "description" as const,
      before: rollbackEvent?.description ?? "",
      after:
        "系统回退至 18:00，部分记忆被写入隔离存储；第五人权限记录随回滚写入主控室审计链。",
      rationale: "为第五人权限线索补上明确的回收事件。",
      selected: true,
      status: "pending" as const,
    },
  ];
}

function nextAgentSequence(history: AgentThread[]): number {
  return (
    history.reduce((highest, thread) => {
      const numeric = Number.parseInt(thread.id.replace(/\D/g, ""), 10);
      return Number.isFinite(numeric) ? Math.max(highest, numeric) : highest;
    }, 41) + 1
  );
}

function nextThreadOrder(history: AgentThread[]): number {
  return (
    history.reduce(
      (highest, thread) => Math.max(highest, thread.updatedOrder),
      41,
    ) + 1
  );
}

function upsertAgentThread(
  history: AgentThread[],
  thread: AgentThread,
): AgentThread[] {
  return [thread, ...history.filter((item) => item.id !== thread.id)];
}

function updateAgentThread(
  history: AgentThread[],
  id: string,
  update: (thread: AgentThread) => AgentThread,
): AgentThread[] {
  return history.map((thread) => (thread.id === id ? update(thread) : thread));
}

function updateActiveReasoningRun(
  reasoning: PrototypeReasoningState,
  update: (
    run: PrototypeReasoningState["runs"][number],
  ) => PrototypeReasoningState["runs"][number],
): PrototypeReasoningState["runs"] {
  const activeId = `RLG-${String(reasoning.runSequence).padStart(4, "0")}`;
  return reasoning.runs.map((run) =>
    run.id === activeId ? update(run) : run,
  );
}

function reasoningEditState(
  state: PrototypeState,
  reasoning: PrototypeReasoningState,
  nextRevision: number,
): PrototypeState {
  return {
    ...state,
    draft: {
      ...state.draft,
      revision: nextRevision,
      lastSavedAt: "刚刚",
    },
    validation: {
      ...state.validation,
      status: "stale",
    },
    compiler: {
      ...state.compiler,
      status: "blocked",
    },
    reasoning: {
      ...reasoning,
      status: "ready",
      baseRevision: nextRevision,
      outcomeRevision: nextRevision,
      stage: `人工画布编辑已写入 REV.${nextRevision}`,
    },
  };
}

export function prototypeReducer(
  state: PrototypeState,
  action: PrototypeAction,
): PrototypeState {
  switch (action.type) {
    case "hydrate":
      return action.state;
    case "set-idea-original":
      return {
        ...state,
        idea: {
          ...state.idea,
          original: action.value,
          suggestionStatus: "idle",
        },
      };
    case "generate-suggestion":
      return {
        ...state,
        idea: { ...state.idea, suggestionStatus: "pending" },
      };
    case "adopt-suggestion":
      return {
        ...state,
        idea: {
          ...state.idea,
          working: state.idea.suggestion,
          suggestionStatus: "adopted",
        },
        brief: {
          ...state.brief,
          oneLineConcept: state.idea.suggestion,
        },
      };
    case "reject-suggestion":
      return {
        ...state,
        idea: { ...state.idea, suggestionStatus: "rejected" },
      };
    case "update-brief":
      return {
        ...state,
        brief: {
          ...state.brief,
          [action.field]: action.value,
          approved: false,
        },
      };
    case "toggle-decision":
      return {
        ...state,
        brief: {
          ...state.brief,
          approved: false,
          decisions: state.brief.decisions.map((decision) =>
            decision.id === action.id
              ? { ...decision, checked: !decision.checked }
              : decision,
          ),
        },
      };
    case "approve-brief":
      return {
        ...state,
        brief: { ...state.brief, approved: true },
      };
    case "select-event":
      return {
        ...state,
        draft: { ...state.draft, selectedEventId: action.id },
      };
    case "update-event": {
      if (isDraftReadOnly(state)) return state;
      const nextRevision = state.draft.revision + 1;
      return {
        ...state,
        draft: {
          ...state.draft,
          revision: nextRevision,
          lastSavedAt: "待保存",
          events: state.draft.events.map((event) =>
            event.id === action.id
              ? { ...event, [action.field]: action.value }
              : event,
          ),
        },
        validation: {
          ...state.validation,
          status: "stale",
        },
        compiler: {
          ...state.compiler,
          status: "blocked",
        },
        reasoning: markReasoningStale(state.reasoning, nextRevision),
      };
    }
    case "save-event":
      if (isDraftReadOnly(state)) return state;
      return {
        ...state,
        draft: { ...state.draft, lastSavedAt: "刚刚" },
      };
    case "prepare-agent-task": {
      const sequence = nextAgentSequence(state.agent.history);
      return {
        ...state,
        agent: {
          ...state.agent,
          status: "preview",
          taskId: `AGT-${String(sequence).padStart(4, "0")}`,
          threadId: `THREAD-${String(sequence).padStart(4, "0")}`,
          taskLabel: action.label,
          instruction: action.instruction,
          taskType: action.taskType,
          sourceThreadId: action.sourceThreadId ?? "",
          mutationTask: action.mutationTask,
          baseRevision: state.draft.revision,
          progress: 0,
          stage: "等待确认",
          readObjectIds: [],
          findings: [],
          changes: [],
        },
      };
    }
    case "start-agent-task":
      if (state.agent.status !== "preview") return state;
      return {
        ...state,
        agent: {
          ...state.agent,
          status: "running",
          progress: 12,
          stage: "建立 Draft 全局索引",
          readObjectIds: ["CF-017", "DRAFT-CURRENT"],
          history: upsertAgentThread(state.agent.history, {
            id: state.agent.threadId,
            label: state.agent.taskLabel,
            taskType: state.agent.taskType,
            instruction: state.agent.instruction,
            baseRevision: state.agent.baseRevision,
            status: "running",
            summary: "Agent 正在建立 Draft 全局索引。",
            findings: [],
            objectIds: ["CF-017", "DRAFT-CURRENT"],
            changeCount: 0,
            createdAt: "刚刚",
            updatedAt: "刚刚",
            updatedOrder: nextThreadOrder(state.agent.history),
            favorite: false,
            archived: false,
            sourceThreadId: state.agent.sourceThreadId || undefined,
          }),
        },
      };
    case "update-agent-task":
      return state.agent.status === "running"
        ? {
            ...state,
            agent: {
              ...state.agent,
              progress: action.progress,
              stage: action.stage,
              readObjectIds: action.readObjectIds,
              history: updateAgentThread(
                state.agent.history,
                state.agent.threadId,
                (thread) => ({
                  ...thread,
                  summary: action.stage,
                  objectIds: action.readObjectIds,
                  updatedAt: "刚刚",
                }),
              ),
            },
          }
        : state;
    case "complete-agent-task": {
      if (state.agent.status !== "running") return state;
      const findings = [
        "第五人权限记录在获得前被林望读取，形成 S1 知识泄露。",
        "反应堆异常的真实时间与叙事阶段缺少同一锚点。",
        "状态回滚尚未明确消费第五人权限线索。",
      ];
      if (!state.agent.mutationTask) {
        const readObjectIds = [
          "CF-017",
          "DRAFT-CURRENT",
          ...state.draft.events.map((event) => event.id),
          "INFO-2107",
          "AI-7712",
          "BR-1800",
          "VAL-KNOW-001",
          "VAL-TIME-006",
          "VAL-CLUE-014",
        ];
        return {
          ...state,
          agent: {
            ...state.agent,
            status: "completed",
            progress: 100,
            stage: "只读分析完成",
            readObjectIds,
            findings,
            history: updateAgentThread(
              state.agent.history,
              state.agent.threadId,
              (thread) => ({
                ...thread,
                status: "completed",
                summary: `只读分析完成，记录 ${findings.length} 项全局发现。`,
                findings,
                objectIds: readObjectIds,
                updatedAt: "刚刚",
              }),
            ),
          },
        };
      }
      const readObjectIds = [
        "CF-017",
        "DRAFT-CURRENT",
        ...state.draft.events.map((event) => event.id),
        "INFO-2107",
        "AI-7712",
        "BR-1800",
        "VAL-KNOW-001",
        "VAL-TIME-006",
        "VAL-CLUE-014",
      ];
      const changes = buildAgentChanges(state);
      return {
        ...state,
        agent: {
          ...state.agent,
          status: "review",
          progress: 100,
          stage: "变更集等待审阅",
          readObjectIds,
          findings,
          changes,
          history: updateAgentThread(
            state.agent.history,
            state.agent.threadId,
            (thread) => ({
              ...thread,
              status: "review",
              summary: `${changes.length} 项结构化变更等待人工审阅。`,
              findings,
              objectIds: readObjectIds,
              changeCount: changes.length,
              updatedAt: "刚刚",
            }),
          ),
        },
      };
    }
    case "cancel-agent-task":
      return state.agent.status === "running"
        ? {
            ...state,
            agent: {
              ...state.agent,
              status: "idle",
              progress: 0,
              stage: "任务已取消",
              readObjectIds: [],
              history: updateAgentThread(
                state.agent.history,
                state.agent.threadId,
                (thread) => ({
                  ...thread,
                  status: "cancelled",
                  summary: "用户在生成阶段取消了任务，未产生任何草稿修改。",
                  objectIds: state.agent.readObjectIds,
                  updatedAt: "刚刚",
                }),
              ),
            },
          }
        : state;
    case "toggle-agent-change":
      return state.agent.status === "review"
        ? {
            ...state,
            agent: {
              ...state.agent,
              changes: state.agent.changes.map((change) =>
                change.id === action.id
                  ? { ...change, selected: !change.selected }
                  : change,
              ),
            },
          }
        : state;
    case "select-all-agent-changes":
      return state.agent.status === "review"
        ? {
            ...state,
            agent: {
              ...state.agent,
              changes: state.agent.changes.map((change) => ({
                ...change,
                selected: action.selected,
              })),
            },
          }
        : state;
    case "reject-agent-changes":
      return state.agent.status === "review"
        ? {
            ...state,
            agent: {
              ...state.agent,
              status: "completed",
              stage: "变更集已拒绝",
              changes: state.agent.changes.map((change) => ({
                ...change,
                selected: false,
                status: "rejected",
              })),
              history: updateAgentThread(
                state.agent.history,
                state.agent.threadId,
                (thread) => ({
                  ...thread,
                  status: "completed",
                  summary: "全局分析已保留，变更集由用户拒绝。",
                  updatedAt: "刚刚",
                }),
              ),
            },
          }
        : state;
    case "rebase-agent-task":
      return state.agent.status === "stale"
        ? {
            ...state,
            agent: {
              ...state.agent,
              status: "preview",
              baseRevision: state.draft.revision,
              progress: 0,
              stage: "等待重新确认",
              readObjectIds: [],
              changes: [],
              history: updateAgentThread(
                state.agent.history,
                state.agent.threadId,
                (thread) => ({
                  ...thread,
                  status: "running",
                  baseRevision: state.draft.revision,
                  summary: "Draft 已变化，等待基于当前 Revision 重新运行。",
                  updatedAt: "刚刚",
                }),
              ),
            },
          }
        : state;
    case "apply-agent-changes": {
      if (state.agent.status !== "review") return state;
      if (state.agent.baseRevision !== state.draft.revision) {
        return {
          ...state,
          agent: {
            ...state.agent,
            status: "stale",
            stage: "Draft 已变化，需要重新基准化",
          },
        };
      }
      const selectedChanges = state.agent.changes.filter(
        (change) => change.selected,
      );
      if (selectedChanges.length === 0) return state;
      const nextRevision = state.draft.revision + 1;
      return {
        ...state,
        draft: {
          ...state.draft,
          revision: nextRevision,
          lastSavedAt: "刚刚",
          events: state.draft.events.map((event) => {
            const eventChanges = selectedChanges.filter(
              (change) => change.objectId === event.id,
            );
            return eventChanges.reduce<DraftEvent>(
              (nextEvent, change) => ({
                ...nextEvent,
                [change.field]: change.after,
              }),
              event,
            );
          }),
        },
        validation: {
          ...state.validation,
          status: "running",
          issues: state.validation.issues.map((issue) =>
            issue.id === "VAL-KNOW-001" || issue.id === "VAL-TIME-006"
              ? { ...issue, status: "pending-revalidation" }
              : issue,
          ),
        },
        compiler: { ...state.compiler, status: "blocked" },
        reasoning: markReasoningStale(state.reasoning, nextRevision),
        agent: {
          ...state.agent,
          status: "validating",
          stage: "确定性 Validator 正在检查新 Revision",
          changes: state.agent.changes.map((change) => ({
            ...change,
            status: change.selected ? "applied" : "rejected",
          })),
          history: updateAgentThread(
            state.agent.history,
            state.agent.threadId,
            (thread) => ({
              ...thread,
              status: "running",
              outcomeRevision: nextRevision,
              summary: "变更已写入，等待确定性 Validator 检查。",
              changeCount: selectedChanges.length,
              updatedAt: "刚刚",
            }),
          ),
        },
      };
    }
    case "finish-agent-validation":
      return state.agent.status === "validating"
        ? {
            ...state,
            agent: {
              ...state.agent,
              status: "completed",
              stage: "变更已写入，确定性验证完成",
              history: updateAgentThread(
                state.agent.history,
                state.agent.threadId,
                (thread) => ({
                  ...thread,
                  status: "completed",
                  outcomeRevision: state.draft.revision,
                  summary: `已写入 REV.${state.draft.revision}，Validator 已完成检查。`,
                  validatorRunId: state.validation.runId,
                  updatedAt: "刚刚",
                }),
              ),
            },
          }
        : state;
    case "reset-agent-session":
      return {
        ...state,
        agent: {
          ...state.agent,
          status: "idle",
          taskId: "",
          threadId: "",
          taskLabel: "",
          instruction: "",
          taskType: "custom",
          sourceThreadId: "",
          mutationTask: false,
          baseRevision: state.draft.revision,
          progress: 0,
          stage: "等待任务",
          readObjectIds: [],
          findings: [],
          changes: [],
        },
      };
    case "toggle-agent-thread-favorite":
      return {
        ...state,
        agent: {
          ...state.agent,
          history: updateAgentThread(
            state.agent.history,
            action.id,
            (thread) => ({ ...thread, favorite: !thread.favorite }),
          ),
        },
      };
    case "archive-agent-thread":
      return {
        ...state,
        agent: {
          ...state.agent,
          history: updateAgentThread(
            state.agent.history,
            action.id,
            (thread) =>
              isTerminalAgentThread(thread)
                ? { ...thread, archived: true, updatedAt: "刚刚" }
                : thread,
          ),
        },
      };
    case "restore-agent-thread":
      return {
        ...state,
        agent: {
          ...state.agent,
          history: updateAgentThread(
            state.agent.history,
            action.id,
            (thread) => ({ ...thread, archived: false, updatedAt: "刚刚" }),
          ),
        },
      };
    case "rename-agent-thread": {
      const label = action.label.trim();
      if (!label) return state;
      return {
        ...state,
        agent: {
          ...state.agent,
          history: updateAgentThread(
            state.agent.history,
            action.id,
            (thread) => ({ ...thread, label, updatedAt: "刚刚" }),
          ),
        },
      };
    }
    case "apply-patch": {
      const nextRevision = state.draft.revision + 1;
      return {
        ...state,
        draft: {
          ...state.draft,
          revision: nextRevision,
          lastSavedAt: "刚刚",
          events: state.draft.events.map((event) =>
            event.id === "EVL-1823"
              ? { ...event, visibility: "AI 核心 + 秦彻" }
              : event,
          ),
        },
        validation: {
          ...state.validation,
          status: "stale",
          patchDecision: "approved",
          issues: state.validation.issues.map((issue) =>
            issue.id === "VAL-KNOW-001"
              ? { ...issue, status: "pending-revalidation" }
              : issue,
          ),
        },
        compiler: { ...state.compiler, status: "blocked" },
        reasoning: markReasoningStale(state.reasoning, nextRevision),
      };
    }
    case "start-validation":
      return {
        ...state,
        validation: { ...state.validation, status: "running" },
      };
    case "complete-validation": {
      const protectedEvent = state.draft.events.find(
        (event) => event.id === "EVL-1823",
      );
      const reactorEvent = state.draft.events.find(
        (event) => event.id === "EVL-1812",
      );
      const knowledgeIssueResolved =
        protectedEvent?.visibility === "AI 核心 + 秦彻";
      const timelineIssueResolved =
        reactorEvent?.phase === "阶段 03 · 异常升级";
      const nextState: PrototypeState = {
        ...state,
        validation: {
          ...state.validation,
          status: "fresh",
          runId: nextRunId(state.validation.runId),
          snapshotRevision: state.draft.revision,
          lastRunAt: "刚刚",
          issues: state.validation.issues.map((issue) =>
            issue.id === "VAL-KNOW-001"
              ? {
                  ...issue,
                  status: knowledgeIssueResolved ? "resolved" : "open",
                }
              : issue.id === "VAL-TIME-006"
                ? {
                    ...issue,
                    status: timelineIssueResolved ? "resolved" : "open",
                  }
              : issue,
          ),
        },
      };
      return {
        ...nextState,
        compiler: {
          ...nextState.compiler,
          status: canCompilePrototype(nextState) ? "idle" : "blocked",
        },
      };
    }
    case "set-compiler-profile":
      return {
        ...state,
        compiler: { ...state.compiler, profile: action.profile },
      };
    case "toggle-artifact":
      return {
        ...state,
        compiler: {
          ...state.compiler,
          artifacts: state.compiler.artifacts.map((artifact) =>
            artifact.id === action.id
              ? { ...artifact, selected: !artifact.selected }
              : artifact,
          ),
        },
      };
    case "start-compile":
      return canCompilePrototype(state)
        ? {
            ...state,
            compiler: { ...state.compiler, status: "building" },
          }
        : {
            ...state,
            compiler: { ...state.compiler, status: "blocked" },
          };
    case "complete-compile":
      return state.compiler.status === "building"
        ? {
            ...state,
            compiler: { ...state.compiler, status: "completed" },
          }
        : state;
    case "set-reasoning-mode":
      return state.reasoning.status === "running"
        ? state
        : {
            ...state,
            reasoning: {
              ...state.reasoning,
              mode: action.mode,
            },
          };
    case "start-reasoning-run": {
      if (state.reasoning.status === "running") return state;
      const runSequence = state.reasoning.runSequence + 1;
      const runId = `RLG-${String(runSequence).padStart(4, "0")}`;
      return {
        ...state,
        reasoning: {
          ...createDefaultReasoningState(state.draft.revision),
          mode: state.reasoning.mode,
          status: "running",
          baseRevision: state.draft.revision,
          progress: 8,
          stage: "固定 Draft Revision 与对象清单",
          runSequence,
          runs: [
            {
              id: runId,
              mode: state.reasoning.mode,
              baseRevision: state.draft.revision,
              status: "running",
              summary: "正在固定 Draft Revision 与对象清单。",
              startedAt: "刚刚",
            },
            ...state.reasoning.runs,
          ],
        },
      };
    }
    case "update-reasoning-run":
      return state.reasoning.status === "running"
        ? {
            ...state,
            reasoning: {
              ...state.reasoning,
              progress: action.progress,
              stage: action.stage,
              runs: updateActiveReasoningRun(state.reasoning, (run) => ({
                ...run,
                summary: action.stage,
              })),
            },
          }
        : state;
    case "complete-reasoning-run": {
      if (state.reasoning.status !== "running") return state;
      const fixture = buildReasoningFixture(state.reasoning.mode);
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          ...fixture,
          status: "review",
          view: "overview",
          progress: 100,
          stage: `${fixture.proposals.length} 项结构化候选等待人工审阅`,
          activePathId: fixture.paths[0]?.id ?? "",
          selectedNodeId: "",
          selectedProposalId: fixture.proposals[0]?.id ?? "",
          runs: updateActiveReasoningRun(state.reasoning, (run) => ({
            ...run,
            status: "review",
            summary: `整卷推理已生成，${fixture.proposals.length} 项候选等待审阅。`,
          })),
        },
      };
    }
    case "cancel-reasoning-run":
      return state.reasoning.status === "running"
        ? {
            ...state,
            reasoning: {
              ...state.reasoning,
              status: "cancelled",
              progress: 0,
              stage: "用户已取消，本次运行未产生候选图",
              runs: updateActiveReasoningRun(state.reasoning, (run) => ({
                ...run,
                status: "cancelled",
                summary: "用户取消任务，未产生候选图。",
              })),
            },
          }
        : state;
    case "fail-reasoning-run":
      return state.reasoning.status === "running"
        ? {
            ...state,
            reasoning: {
              ...state.reasoning,
              status: "failed",
              progress: 0,
              stage: "模拟生成失败",
              failureMessage: action.message,
              runs: updateActiveReasoningRun(state.reasoning, (run) => ({
                ...run,
                status: "failed",
                summary: action.message,
              })),
            },
          }
        : state;
    case "open-reasoning-overview":
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          view: "overview",
          selectedNodeId: "",
        },
      };
    case "select-reasoning-path": {
      const path = state.reasoning.paths.find((item) => item.id === action.id);
      if (!path) return state;
      const selectedProposal = state.reasoning.proposals.find((proposal) => {
        const node = state.reasoning.nodes.find(
          (item) => item.id === proposal.targetId,
        );
        const edge = state.reasoning.edges.find(
          (item) => item.id === proposal.targetId,
        );
        return node?.pathId === path.id || edge?.pathId === path.id;
      });
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          view: "path",
          activePathId: path.id,
          selectedNodeId: path.nodeIds[0] ?? "",
          selectedProposalId:
            selectedProposal?.id ?? state.reasoning.selectedProposalId,
        },
      };
    }
    case "select-reasoning-node": {
      const node = state.reasoning.nodes.find((item) => item.id === action.id);
      if (!node) return state;
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          selectedNodeId: node.id,
          selectedProposalId:
            node.proposalId ?? state.reasoning.selectedProposalId,
        },
      };
    }
    case "select-reasoning-proposal":
      return state.reasoning.proposals.some(
        (proposal) => proposal.id === action.id,
      )
        ? {
            ...state,
            reasoning: {
              ...state.reasoning,
              selectedProposalId: action.id,
            },
          }
        : state;
    case "toggle-reasoning-proposal":
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          proposals: state.reasoning.proposals.map((proposal) =>
            proposal.id === action.id && proposal.status === "pending"
              ? { ...proposal, selected: !proposal.selected }
              : proposal,
          ),
        },
      };
    case "select-all-reasoning-proposals":
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          proposals: state.reasoning.proposals.map((proposal) =>
            proposal.status === "pending"
              ? { ...proposal, selected: action.selected }
              : proposal,
          ),
        },
      };
    case "reject-reasoning-proposal": {
      const proposal = state.reasoning.proposals.find(
        (item) => item.id === action.id,
      );
      if (!proposal || proposal.status !== "pending") return state;
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          proposals: state.reasoning.proposals.map((item) =>
            item.id === action.id
              ? { ...item, selected: false, status: "rejected" }
              : item,
          ),
          nodes: state.reasoning.nodes.map((node) =>
            node.id === proposal.targetId
              ? { ...node, status: "excluded" }
              : node,
          ),
          edges: state.reasoning.edges.map((edge) =>
            edge.id === proposal.targetId
              ? { ...edge, status: "excluded" }
              : edge,
          ),
        },
      };
    }
    case "apply-reasoning-proposals": {
      if (state.reasoning.status !== "review") return state;
      if (state.reasoning.baseRevision !== state.draft.revision) {
        return {
          ...state,
          reasoning: markReasoningStale(
            state.reasoning,
            state.draft.revision,
          ),
        };
      }
      const selected = getSelectedReasoningChanges(state.reasoning);
      if (selected.length === 0) return state;
      const selectedIds = new Set(selected.map((proposal) => proposal.id));
      const pendingIds = new Set(
        state.reasoning.proposals
          .filter((proposal) => proposal.status === "pending")
          .map((proposal) => proposal.id),
      );
      const nextRevision = state.draft.revision + 1;
      return {
        ...state,
        draft: {
          ...state.draft,
          revision: nextRevision,
          lastSavedAt: "刚刚",
        },
        validation: {
          ...state.validation,
          status: "stale",
        },
        compiler: {
          ...state.compiler,
          status: "blocked",
        },
        reasoning: {
          ...state.reasoning,
          status: "ready",
          baseRevision: nextRevision,
          outcomeRevision: nextRevision,
          stage: `${selected.length} 项推理变更已写入 REV.${nextRevision}`,
          nodes: state.reasoning.nodes.map((node) =>
            node.proposalId && selectedIds.has(node.proposalId)
              ? { ...node, status: "confirmed" }
              : node.proposalId && pendingIds.has(node.proposalId)
                ? { ...node, status: "excluded" }
                : node,
          ),
          edges: state.reasoning.edges.map((edge) =>
            edge.proposalId && selectedIds.has(edge.proposalId)
              ? { ...edge, status: "confirmed" }
              : edge.proposalId && pendingIds.has(edge.proposalId)
                ? { ...edge, status: "excluded" }
                : edge,
          ),
          proposals: state.reasoning.proposals.map((proposal) =>
            proposal.status === "pending"
              ? {
                  ...proposal,
                  status: selectedIds.has(proposal.id)
                    ? "applied"
                    : "rejected",
                  selected: false,
                }
              : proposal,
          ),
          runs: updateActiveReasoningRun(state.reasoning, (run) => ({
            ...run,
            status: "ready",
            outcomeRevision: nextRevision,
            summary: `${selected.length} 项候选已批准并写入 REV.${nextRevision}。`,
          })),
        },
      };
    }
    case "toggle-reasoning-bundle":
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          expandedBundleIds: state.reasoning.expandedBundleIds.includes(
            action.id,
          )
            ? state.reasoning.expandedBundleIds.filter(
                (id) => id !== action.id,
              )
            : [...state.reasoning.expandedBundleIds, action.id],
        },
      };
    case "set-reasoning-positions":
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          positions: { ...state.reasoning.positions, ...action.positions },
        },
      };
    case "set-reasoning-node-position":
      return {
        ...state,
        reasoning: {
          ...state.reasoning,
          positions: {
            ...state.reasoning.positions,
            [action.id]: action.position,
          },
        },
      };
    case "add-user-reasoning-node": {
      if (state.reasoning.status !== "ready") return state;
      const path = state.reasoning.paths.find(
        (item) => item.id === action.pathId,
      );
      if (!path) return state;
      const nextRevision = state.draft.revision + 1;
      const nodeId = `user-hypothesis-${nextRevision}`;
      const reasoning: PrototypeReasoningState = {
        ...state.reasoning,
        nodes: [
          ...state.reasoning.nodes,
          {
            id: nodeId,
            pathId: path.id,
            kind: "hypothesis",
            status: "confirmed",
            label: "新的人工假设",
            statement: "由作者在推理画布中新增，等待补充具体内容。",
            sourceIds: [],
            tags: ["人工创建", `REV.${nextRevision}`],
            userEditable: true,
          },
        ],
        paths: state.reasoning.paths.map((item) =>
          item.id === path.id
            ? { ...item, nodeIds: [...item.nodeIds, nodeId] }
            : item,
        ),
        positions: {},
        selectedNodeId: nodeId,
      };
      return reasoningEditState(state, reasoning, nextRevision);
    }
    case "connect-reasoning-nodes": {
      if (
        state.reasoning.status !== "ready" ||
        action.source === action.target
      ) {
        return state;
      }
      const source = state.reasoning.nodes.find(
        (node) => node.id === action.source,
      );
      const target = state.reasoning.nodes.find(
        (node) => node.id === action.target,
      );
      if (
        !source ||
        !target ||
        source.pathId !== action.pathId ||
        target.pathId !== action.pathId ||
        state.reasoning.edges.some(
          (edge) =>
            edge.source === action.source && edge.target === action.target,
        )
      ) {
        return state;
      }
      const nextRevision = state.draft.revision + 1;
      const reasoning: PrototypeReasoningState = {
        ...state.reasoning,
        edges: [
          ...state.reasoning.edges,
          {
            id: `user-edge-${nextRevision}`,
            pathId: action.pathId,
            source: action.source,
            target: action.target,
            kind: "supports",
            status: "confirmed",
            label: "人工支持",
          },
        ],
      };
      return reasoningEditState(state, reasoning, nextRevision);
    }
    case "rename-reasoning-node": {
      if (state.reasoning.status !== "ready") return state;
      const label = action.label.trim();
      const node = state.reasoning.nodes.find((item) => item.id === action.id);
      if (!label || !node?.userEditable || label === node.label) return state;
      const nextRevision = state.draft.revision + 1;
      const reasoning: PrototypeReasoningState = {
        ...state.reasoning,
        nodes: state.reasoning.nodes.map((item) =>
          item.id === action.id ? { ...item, label } : item,
        ),
      };
      return reasoningEditState(state, reasoning, nextRevision);
    }
    case "reset-reasoning":
      return {
        ...state,
        reasoning: createDefaultReasoningState(state.draft.revision),
      };
    case "reset":
      return createDefaultPrototypeState();
    default:
      return state;
  }
}

interface PrototypeContextValue {
  state: PrototypeState;
  dispatch: Dispatch<PrototypeAction>;
  ready: boolean;
  reset: () => void;
}

const PrototypeContext = createContext<PrototypeContextValue | null>(null);

export function PrototypeProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    prototypeReducer,
    undefined,
    createDefaultPrototypeState,
  );
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try {
      const stored =
        window.localStorage.getItem(STORAGE_KEY) ??
        window.localStorage.getItem(LEGACY_STORAGE_KEY);
      if (stored) {
        const parsed: unknown = JSON.parse(stored);
        const hydrated = hydratePrototypeState(parsed);
        if (hydrated) {
          dispatch({ type: "hydrate", state: hydrated });
        }
      }
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    if (!ready) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  }, [ready, state]);

  const value = useMemo<PrototypeContextValue>(
    () => ({
      state,
      dispatch,
      ready,
      reset: () => {
        window.localStorage.removeItem(STORAGE_KEY);
        window.localStorage.removeItem(LEGACY_STORAGE_KEY);
        dispatch({ type: "reset" });
      },
    }),
    [ready, state],
  );

  return (
    <PrototypeContext.Provider value={value}>
      {children}
    </PrototypeContext.Provider>
  );
}

export function usePrototype(): PrototypeContextValue {
  const context = useContext(PrototypeContext);
  if (!context) {
    throw new Error("usePrototype must be used inside PrototypeProvider");
  }
  return context;
}
