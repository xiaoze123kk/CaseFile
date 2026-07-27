export type CaseStage = "idea" | "brief" | "draft" | "validated" | "compiled";

export type SuggestionStatus = "idle" | "pending" | "adopted" | "rejected";

export type ValidationStatus = "fresh" | "stale" | "running";

export type IssueStatus = "open" | "pending-revalidation" | "resolved";

export type CompilerStatus = "blocked" | "idle" | "building" | "completed";

export type BriefTextField =
  | "oneLineConcept"
  | "coreMystery"
  | "playerGoal"
  | "gameplayLoop";

export interface DraftEvent {
  id: string;
  time: string;
  title: string;
  description: string;
  location: string;
  phase: string;
  participants: string;
  visibility: string;
  importance: string;
  refCount: number;
  tags: string[];
}

export interface PrototypeIssue {
  id: string;
  ruleId: string;
  severity: Extract<ValidationIssue["severity"], "S1" | "S2">;
  title: string;
  objectId: string;
  evidenceId: string;
  explanation: string;
  fixHint: string;
  status: IssueStatus;
  detectionType: Extract<
    ValidationIssue["detection_type"],
    "deterministic" | "graph"
  >;
}

export interface CompilerArtifact {
  id: string;
  name: string;
  description: string;
  size: string;
  selected: boolean;
}

export interface PrototypeState {
  storageVersion: 1;
  project: {
    projectId: string;
    displayName: string;
    casefileTitle: string;
    version: string;
  };
  idea: {
    original: string;
    working: string;
    suggestion: string;
    suggestionStatus: SuggestionStatus;
  };
  brief: {
    oneLineConcept: string;
    coreMystery: string;
    playerGoal: string;
    gameplayLoop: string;
    constraints: string[];
    openQuestions: string[];
    decisions: Array<{ id: string; label: string; checked: boolean }>;
    approved: boolean;
  };
  draft: {
    revision: number;
    selectedEventId: string;
    events: DraftEvent[];
    lastSavedAt: string;
  };
  validation: {
    status: ValidationStatus;
    runId: string;
    snapshotRevision: number;
    lastRunAt: string;
    issues: PrototypeIssue[];
    patchDecision: "pending" | "approved";
  };
  compiler: {
    profile: "standard" | "developer" | "casefile";
    status: CompilerStatus;
    artifacts: CompilerArtifact[];
  };
}

const defaultEvents: DraftEvent[] = [
  {
    id: "EVL-1800",
    time: "18:00",
    title: "实验启动",
    description: "科研团队进入量子引擎稳定性实验，AI 接入站内控制。",
    location: "实验区 / LAB",
    phase: "阶段 01 · 实验启动",
    participants: "林望 + 秦彻 + AI 核心",
    visibility: "全部角色",
    importance: "普通事件",
    refCount: 3,
    tags: ["真实事件", "实验区", "角色 04"],
  },
  {
    id: "EVL-1812",
    time: "18:20",
    title: "反应堆异常",
    description: "能源曲线突然偏离安全区间，维护日志出现十二秒空白。",
    location: "反应堆 / REACTOR",
    phase: "阶段 02 · 异常出现",
    participants: "林望 + 维护组",
    visibility: "林望 + 秦彻",
    importance: "重要事件",
    refCount: 5,
    tags: ["真实事件", "反应堆", "信息 05"],
  },
  {
    id: "EVL-1823",
    time: "18:23",
    title: "AI 启动保护协议",
    description: "AI 启用受限协议 v2.1，并将第五人的权限记录标记为不可见。",
    location: "主控室 / BRIDGE",
    phase: "阶段 03 · 保护协议",
    participants: "AI 核心 + 林望 + 秦彻",
    visibility: "AI 核心 + 全部角色",
    importance: "关键事件",
    refCount: 7,
    tags: ["关键事件", "主控室", "AI 核心"],
  },
  {
    id: "EVL-1825",
    time: "18:25",
    title: "空间站状态回滚",
    description: "系统回退至 18:00，部分记忆被写入隔离存储。",
    location: "全站 / STATION",
    phase: "阶段 04 · 循环重启",
    participants: "全体角色",
    visibility: "AI 核心 + 秦彻",
    importance: "关键事件",
    refCount: 9,
    tags: ["循环事件", "全站", "信息 09"],
  },
];

const defaultIssues: PrototypeIssue[] = [
  {
    id: "VAL-KNOW-001",
    ruleId: "knowledge.visibility.before_acquire",
    severity: "S1",
    title: "角色知识泄露",
    objectId: "EVL-1823",
    evidenceId: "INFO-2107",
    explanation:
      "事件发生时，角色“林望”尚未获得第五人权限记录，但当前可见范围允许其读取该事实。",
    fixHint: "将可见角色缩小为“AI 核心 + 秦彻”。",
    status: "open",
    detectionType: "deterministic",
  },
  {
    id: "VAL-TIME-006",
    ruleId: "timeline.phase.anchor_required",
    severity: "S2",
    title: "时间窗缺少锚点",
    objectId: "EVL-1812",
    evidenceId: "PHASE-03",
    explanation: "反应堆异常已进入阶段 03，但叙事时间缺少明确转换锚点。",
    fixHint: "确认阶段 03 与真实时间 18:20 的转换关系。",
    status: "open",
    detectionType: "graph",
  },
  {
    id: "VAL-CLUE-014",
    ruleId: "information.consumer.required",
    severity: "S2",
    title: "线索缺少回收事件",
    objectId: "INFO-4402",
    evidenceId: "EVL-1825",
    explanation: "舱外脚印被声明为可发现线索，但没有事件或结论消费该信息。",
    fixHint: "把线索关联到状态回滚事件，或明确标记为干扰信息。",
    status: "resolved",
    detectionType: "graph",
  },
];

export function createDefaultPrototypeState(): PrototypeState {
  return {
    storageVersion: 1,
    project: {
      projectId: "CF-017",
      displayName: "空间站不断重启",
      casefileTitle: "第七次重启",
      version: "V0.3.2",
    },
    idea: {
      original:
        "一座空间站每隔二十五分钟就会重启，所有人只记得最近一次循环，但站内 AI 似乎保留了更早的记录。",
      working:
        "一座空间站每隔二十五分钟就会重启，所有人只记得最近一次循环，但站内 AI 似乎保留了更早的记录。",
      suggestion:
        "每次重启都会抹除乘员记忆，唯独受限 AI 保存着前六次失败。四名玩家必须在第七次循环结束前判断：重启究竟是事故，还是一套正在保护他们的协议。",
      suggestionStatus: "idle",
    },
    brief: {
      oneLineConcept:
        "四名玩家在不断重启的空间站中追查事故真相，却发现重启本身可能是最后一道保护。",
      coreMystery: "谁触发了循环重启，以及被隐藏的第五人权限记录属于谁？",
      playerGoal: "在第七次循环结束前重建真实时间线，决定是否终止保护协议。",
      gameplayLoop: "调查场景 → 交换受限信息 → 提交假设 → 验证矛盾 → 决策。",
      constraints: [
        "核心因果必须唯一可验证",
        "四名角色的信息获取路径必须不同",
        "所有关键结论至少由两条独立信息支持",
      ],
      openQuestions: [
        "AI 的初始可信度应如何呈现？",
        "第五人是现实角色还是权限幽灵？",
        "终止协议是否必须付出永久失忆的代价？",
      ],
      decisions: [
        { id: "D-01", label: "采用四人标准配置", checked: true },
        { id: "D-02", label: "采用唯一根因结论", checked: false },
        { id: "D-03", label: "允许双结局但共用事实层", checked: false },
      ],
      approved: false,
    },
    draft: {
      revision: 18,
      selectedEventId: "EVL-1823",
      events: defaultEvents.map((event) => ({ ...event, tags: [...event.tags] })),
      lastSavedAt: "刚刚",
    },
    validation: {
      status: "fresh",
      runId: "VAL-0018",
      snapshotRevision: 18,
      lastRunAt: "12 秒前",
      issues: defaultIssues.map((issue) => ({ ...issue })),
      patchDecision: "pending",
    },
    compiler: {
      profile: "standard",
      status: "blocked",
      artifacts: [
        {
          id: "facilitator",
          name: "facilitator-guide.md",
          description: "主持人手册 / Markdown",
          size: "128 KB",
          selected: true,
        },
        {
          id: "players",
          name: "player-packets.pdf",
          description: "玩家材料 × 4 / PDF",
          size: "2.4 MB",
          selected: true,
        },
        {
          id: "canon",
          name: "canon.json",
          description: "稳定事实与结论 / JSON",
          size: "64 KB",
          selected: true,
        },
        {
          id: "source-map",
          name: "source-map.json",
          description: "产物到对象追踪 / JSON",
          size: "92 KB",
          selected: true,
        },
      ],
    },
  };
}

export function hasBlockingIssue(state: PrototypeState): boolean {
  return state.validation.issues.some(
    (issue) =>
      issue.severity === "S1" &&
      (issue.status === "open" || issue.status === "pending-revalidation"),
  );
}

export function canCompilePrototype(state: PrototypeState): boolean {
  return state.validation.status === "fresh" && !hasBlockingIssue(state);
}
import type { ValidationIssue } from "@casefile/contracts";
