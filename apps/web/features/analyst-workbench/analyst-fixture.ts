export type ReasoningOutcome = "supported" | "contested" | "eliminated";

export interface ReasoningStep {
  id: string;
  verb: string;
  claim: string;
  evidenceIds: string[];
}

export interface ReasoningPath {
  id: string;
  question: string;
  evidenceIds: string[];
  steps: ReasoningStep[];
  conclusion: string;
  outcome: ReasoningOutcome;
  hypothesisId: string;
}

export type ReasoningAssessmentEffect =
  | "supports"
  | "contradicts"
  | "neutral"
  | "unassessed";

export type ReasoningAssessmentStrength = "weak" | "moderate" | "strong";

export interface WorkbenchReasoningAssessment {
  hypothesisId: string;
  informationId: string;
  effect: Exclude<ReasoningAssessmentEffect, "unassessed">;
  strength: ReasoningAssessmentStrength;
  rationale: string;
}

export interface WorkbenchReasoningGroup {
  resolutionSpecId: string;
  question: string;
  hypotheses: Array<{
    id: string;
    title: string;
    outcome: ReasoningOutcome;
  }>;
  information: Array<{
    id: string;
    title: string;
    reliability: string;
  }>;
  assessments: WorkbenchReasoningAssessment[];
  conclusion?: import("./workbench-real-data-types").WorkbenchConclusion;
}

export type InspectorTab = "object" | "issues" | "sources" | "patch" | "audit";

export type ObjectKind =
  | "resolution_spec"
  | "entity"
  | "information"
  | "person"
  | "evidence"
  | "event"
  | "location"
  | "hypothesis";

export type IssueStatus = "open" | "patch-ready" | "resolved" | "exception";

export interface CaseObject {
  id: string;
  kind: ObjectKind;
  label: string;
  code: string;
  meta: string;
  subtype?: string;
  relatedEventIds: string[];
}

export interface TimelineEvent {
  id: string;
  time: string;
  label: string;
  location: string;
  summary: string;
  relatedObjectIds: string[];
  issueIds: string[];
}

export interface ValidationIssue {
  id: string;
  severity: "S0" | "S1" | "error";
  title: string;
  summary: string;
  eventId: string | null;
  rule: string;
  evidenceIds: string[];
  beforeKnowledge: string;
  eventClaim: string;
  afterKnowledge: string;
  patchBefore: string;
  patchAfter: string;
  source?: "fixture" | "validator";
  targetObjectId?: string | null;
  targetObjectType?: string | null;
  fieldPath?: string;
}

export interface SourceItem {
  id: string;
  kind: "audio" | "transcript" | "record" | "retrieval";
  label: string;
  meta: string;
  excerpt: string;
  eventId: string;
  evidenceObjectId?: string;
}

export interface GraphNode {
  objectId: string;
  x: number;
  y: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  label: string;
}

export interface WorkbenchMapMarker {
  eventId: string;
  label: string;
  x: number;
  y: number;
}

export interface WorkbenchMapLabel {
  label: string;
  x: number;
  y: number;
}

export interface WorkbenchAuditEntry {
  id: string;
  time: string;
  actor: string;
  action: string;
  detail: string;
}

export interface WorkbenchDrawerCopy {
  audioTitle: string;
  audioDuration: string;
  audioProgress: string;
  keyTime: string;
  keyExcerpt: string;
  transcript: string;
  logs: Array<{ time: string; actor: string; detail: string }>;
}

export interface WorkbenchCaseMeta {
  title: string;
  monogram: string;
  subtitle: string;
  revision: string;
  timelineTitle: string;
  timelineMeta: string;
  mapTitle: string;
  mapMeta: string;
  mapNote: string;
  relationshipSummary: string;
  exportTitle: string;
  exportCode: string;
  exportSubtitle: string;
  dossierVisibleRoles: string;
  branchLabel: string;
  protagonist: string;
}

export interface WorkbenchSeed {
  id: string;
  caseMeta: WorkbenchCaseMeta;
  caseObjects: CaseObject[];
  timelineEvents: TimelineEvent[];
  validationIssues: ValidationIssue[];
  sourceItems: SourceItem[];
  graphNodes: GraphNode[];
  graphEdges: GraphEdge[];
  reasoningPaths: ReasoningPath[];
  reasoningGroups?: WorkbenchReasoningGroup[];
  conclusions?: import("./workbench-real-data-types").WorkbenchConclusion[];
  mapMarkers: WorkbenchMapMarker[];
  mapLabels: WorkbenchMapLabel[];
  drawer: WorkbenchDrawerCopy;
  initialAuditEntries: WorkbenchAuditEntry[];
  defaultEventId: string | null;
  defaultObjectId: string | null;
  defaultIssueId: string | null;
}

export type WorkbenchCandidateFocus = "structure" | "atmosphere" | "reasoning";

export interface WorkbenchCandidate {
  id: string;
  briefVersion: number;
  candidateStrategy?:
    | "balanced"
    | "structure_first"
    | "atmosphere_first"
    | "reasoning_first";
  focus: WorkbenchCandidateFocus;
  focusLabel: string;
  title: string;
  summary: string;
  reasoningQuestion: string;
  objectCounts: Record<string, number>;
  constraintStatements: string[];
  strengths: string[];
  tradeoffs: string[];
  workbenchSeed: WorkbenchSeed;
}

export interface CandidateBriefInput {
  creativeIntent: string;
  reasoningProposition: string;
  authorAnswer: string;
  constraints: string[];
}

export const objectKindLabels: Record<ObjectKind, string> = {
  resolution_spec: "核心问题",
  entity: "实体",
  information: "信息",
  person: "人物",
  evidence: "证据",
  event: "事件",
  location: "地点",
  hypothesis: "假设",
};

export const caseObjects: CaseObject[] = [
  {
    id: "PER-001",
    kind: "person",
    label: "秦彻",
    code: "调查员 / 当前视角",
    meta: "已知 17 条 · 未知 3 条",
    relatedEventIds: ["EV-1812", "EV-1825"],
  },
  {
    id: "PER-004",
    kind: "person",
    label: "林岚",
    code: "失联工程师",
    meta: "最后出现 22:16",
    relatedEventIds: ["EV-1812", "EV-1823"],
  },
  {
    id: "PER-009",
    kind: "person",
    label: "唐默",
    code: "港务值班员",
    meta: "证词待复核",
    relatedEventIds: ["EV-1800", "EV-1825"],
  },
  {
    id: "EVD-071",
    kind: "evidence",
    label: "07 号门禁记录",
    code: "数字记录 / 已校验",
    meta: "22:31 · 第五人权限",
    relatedEventIds: ["EV-1825"],
  },
  {
    id: "EVD-113",
    kind: "evidence",
    label: "海关电台录音 A-13",
    code: "音频 / 已转写",
    meta: "03:42 · 可信度 0.92",
    relatedEventIds: ["EV-1823", "EV-1825"],
  },
  {
    id: "EVD-209",
    kind: "evidence",
    label: "检修通道监控",
    code: "视频帧 / 有缺口",
    meta: "缺失 6 分 12 秒",
    relatedEventIds: ["EV-1812"],
  },
  {
    id: "EV-1800",
    kind: "event",
    label: "码头监控断帧",
    code: "22:08 · 雾港三号码头",
    meta: "因果入口",
    relatedEventIds: ["EV-1800"],
  },
  {
    id: "EV-1812",
    kind: "event",
    label: "林岚进入检修通道",
    code: "22:16 · 检修区",
    meta: "1 个 S1 问题",
    relatedEventIds: ["EV-1812"],
  },
  {
    id: "EV-1823",
    kind: "event",
    label: "电台收到二次呼叫",
    code: "22:24 · 港务电台",
    meta: "录音可回放",
    relatedEventIds: ["EV-1823"],
  },
  {
    id: "EV-1825",
    kind: "event",
    label: "备用门禁被异常开启",
    code: "22:31 · 07 号通道",
    meta: "1 个 S0 问题",
    relatedEventIds: ["EV-1825"],
  },
  {
    id: "LOC-003",
    kind: "location",
    label: "雾港三号码头",
    code: "外部区域",
    meta: "3 个关联事件",
    relatedEventIds: ["EV-1800", "EV-1812", "EV-1823"],
  },
  {
    id: "LOC-007",
    kind: "location",
    label: "07 号检修通道",
    code: "受限区域",
    meta: "门禁级别 R4",
    relatedEventIds: ["EV-1812", "EV-1825"],
  },
  {
    id: "HYP-002",
    kind: "hypothesis",
    label: "内部接应者",
    code: "候选假设 / 62%",
    meta: "仍缺身份闭环",
    relatedEventIds: ["EV-1800", "EV-1825"],
  },
  {
    id: "HYP-001",
    kind: "hypothesis",
    label: "外部入侵者",
    code: "竞争假设 / 31%",
    meta: "无法解释 R4 覆盖",
    relatedEventIds: ["EV-1825"],
  },
];

export const timelineEvents: TimelineEvent[] = [
  {
    id: "EV-1800",
    time: "22:08",
    label: "码头监控断帧",
    location: "雾港三号码头",
    summary: "三号泊位的东侧监控开始循环播放旧画面。",
    relatedObjectIds: ["LOC-003", "PER-009", "HYP-002"],
    issueIds: [],
  },
  {
    id: "EV-1812",
    time: "22:16",
    label: "林岚进入检修通道",
    location: "07 号检修通道",
    summary: "林岚刷入检修区，但相邻摄像头仍记录她在码头。",
    relatedObjectIds: ["PER-004", "LOC-007", "EVD-209"],
    issueIds: ["ISSUE-TIME-006"],
  },
  {
    id: "EV-1823",
    time: "22:24",
    label: "电台收到二次呼叫",
    location: "港务电台",
    summary: "未知呼号提到“第五人权限”，录音在 02:18 处出现关键短句。",
    relatedObjectIds: ["PER-004", "EVD-113", "LOC-003"],
    issueIds: [],
  },
  {
    id: "EV-1825",
    time: "22:31",
    label: "备用门禁被异常开启",
    location: "07 号检修通道",
    summary: "秦彻将异常签名识别为第五人权限，但该信息此时尚未进入其知识状态。",
    relatedObjectIds: ["PER-001", "PER-009", "EVD-071", "EVD-113", "LOC-007", "HYP-002"],
    issueIds: ["ISSUE-KNOW-001"],
  },
];

export const validationIssues: ValidationIssue[] = [
  {
    id: "ISSUE-KNOW-001",
    severity: "S0",
    title: "角色提前知道“第五人权限”",
    summary: "事件中的判断使用了 9 分钟后才被证词确认的信息，破坏知识状态约束。",
    eventId: "EV-1825",
    rule: "KNOWLEDGE_STATE_BEFORE_EVENT",
    evidenceIds: ["EVD-071", "EVD-113"],
    beforeKnowledge: "22:31 前，秦彻只知道门禁出现未知 R4 覆盖；尚不知道其内部代号。",
    eventClaim: "22:31，秦彻立即认出这是“第五人权限”的签名。",
    afterKnowledge: "22:40，唐默在录音补充段首次说明该覆盖签名被称为“第五人权限”。",
    patchBefore: "秦彻看见覆盖码，立刻认出这是第五人权限的签名。",
    patchAfter: "秦彻记录下陌生的 R4 覆盖码；直到复听唐默的补充录音后，他才把它与“第五人权限”对应起来。",
  },
  {
    id: "ISSUE-TIME-006",
    severity: "S1",
    title: "林岚在两个地点同时出现",
    summary: "刷卡记录与监控时间重叠 6 分钟，需要解释监控循环或修订事件时间。",
    eventId: "EV-1812",
    rule: "TEMPORAL_EXCLUSIVITY",
    evidenceIds: ["EVD-209"],
    beforeKnowledge: "22:14 的监控帧显示林岚仍在三号码头东侧。",
    eventClaim: "22:16，林岚本人刷入相距 780 米的 07 号检修通道。",
    afterKnowledge: "监控文件的帧校验显示 22:11 起存在循环片段，但尚未被事件引用。",
    patchBefore: "22:16，林岚从码头步行进入检修通道。",
    patchAfter: "22:16，林岚的工牌刷入检修通道；码头监控中的身影随后被证实为循环画面。",
  },
];

export const sourceItems: SourceItem[] = [
  {
    id: "SRC-A13",
    kind: "audio",
    label: "海关电台录音 A-13",
    meta: "03:42 · 关键片段 02:18",
    excerpt: "……那个覆盖码，我们内部叫它第五人权限。别在公开频道提。",
    eventId: "EV-1823",
    evidenceObjectId: "EVD-113",
  },
  {
    id: "SRC-T13",
    kind: "transcript",
    label: "A-13 人工校订转写",
    meta: "校订者：秦彻 · 可信度高",
    excerpt: "首次明确出现“第五人权限”一词的时间为 22:40。",
    eventId: "EV-1823",
    evidenceObjectId: "EVD-113",
  },
  {
    id: "SRC-R71",
    kind: "record",
    label: "07 号门禁审计记录",
    meta: "签名校验通过 · R4 覆盖",
    excerpt: "22:31:08 使用未知 R4 权限打开备用门；记录未包含内部代号。",
    eventId: "EV-1825",
    evidenceObjectId: "EVD-071",
  },
  {
    id: "SRC-Q09",
    kind: "retrieval",
    label: "检索命中：内部权限词表",
    meta: "命中 3 / 47 · 不进入 Canon",
    excerpt: "“第五人权限”只在唐默补充录音和一份未采用备忘中出现。",
    eventId: "EV-1825",
    evidenceObjectId: "EVD-113",
  },
];

export const graphNodes: GraphNode[] = [
  { objectId: "PER-001", x: 16, y: 24 },
  { objectId: "PER-009", x: 79, y: 21 },
  { objectId: "EV-1825", x: 50, y: 45 },
  { objectId: "EVD-071", x: 19, y: 72 },
  { objectId: "EVD-113", x: 77, y: 75 },
  { objectId: "LOC-007", x: 49, y: 86 },
  { objectId: "HYP-002", x: 87, y: 49 },
  { objectId: "HYP-001", x: 88, y: 70 },
];

export const graphEdges: GraphEdge[] = [
  { from: "PER-001", to: "EV-1825", label: "目击" },
  { from: "PER-009", to: "EV-1825", label: "证词" },
  { from: "EVD-071", to: "EV-1825", label: "记录" },
  { from: "EVD-113", to: "EV-1825", label: "后知" },
  { from: "LOC-007", to: "EV-1825", label: "发生于" },
  { from: "HYP-002", to: "EV-1825", label: "支持" },
  { from: "HYP-001", to: "EV-1825", label: "反驳" },
];

export const initialAuditEntries = [
  {
    id: "AUD-120",
    time: "10:42",
    actor: "Validator",
    action: "发现知识状态冲突",
    detail: "ISSUE-KNOW-001 · S0 · REV.12",
  },
  {
    id: "AUD-119",
    time: "10:41",
    actor: "秦彻",
    action: "采用门禁记录引用",
    detail: "EVD-071 → EV-1825",
  },
  {
    id: "AUD-118",
    time: "10:36",
    actor: "Retrieval",
    action: "更新录音检索命中",
    detail: "A-13 · 3 个片段",
  },
] as const;

const defaultMapMarkers: WorkbenchMapMarker[] = [
  { eventId: "EV-1800", label: "监控断帧", x: 19, y: 63 },
  { eventId: "EV-1812", label: "进入通道", x: 54, y: 52 },
  { eventId: "EV-1823", label: "二次呼叫", x: 30, y: 25 },
  { eventId: "EV-1825", label: "门禁开启", x: 73, y: 70 },
];

const defaultMapLabels: WorkbenchMapLabel[] = [
  { label: "港务电台", x: 8, y: 11 },
  { label: "检修通道", x: 42, y: 42 },
  { label: "备用门", x: 70, y: 84 },
];

export const defaultWorkbenchSeed: WorkbenchSeed = {
  id: "default-fog-harbor",
  caseMeta: {
    title: "雾港失联案",
    monogram: "雾",
    subtitle: "一名工程师在互相矛盾的港区记录中失联。",
    revision: "REV.12",
    timelineTitle: "雾港失联前 34 分钟",
    timelineMeta: "4 EVENTS · 2 ISSUES",
    mapTitle: "雾港三号码头 / 检修区",
    mapMeta: "780 METER TRACE",
    mapNote:
      "文字替代：22:16 的检修通道与 22:14 的码头监控相距约 780 米，步行无法在两分钟内完成。",
    relationshipSummary:
      "以备用门禁异常事件为中心，连接秦彻、唐默、07 号门禁记录、海关电台录音、07 号检修通道和内部接应者假设。所有节点可通过下方关系表访问。",
    exportTitle: "调查剧本 / Canon 包",
    exportCode: "CASEFILE / CF-017",
    exportSubtitle: "调查剧本 · 作者卷宗 · 2026.08",
    dossierVisibleRoles: "秦彻、唐默",
    branchLabel: "调查主线",
    protagonist: "秦彻",
  },
  caseObjects,
  timelineEvents,
  validationIssues,
  sourceItems,
  graphNodes,
  graphEdges,
  reasoningPaths: [
    {
      id: "RP-01",
      question: "第五人权限如何进入码头",
      evidenceIds: ["EVD-071", "EVD-113"],
      steps: [
        {
          id: "RS-01",
          verb: "比对",
          claim: "门禁覆盖签名属于内部 R4 权限，未出现在公开名录",
          evidenceIds: ["EVD-071"],
        },
        {
          id: "RS-02",
          verb: "复听",
          claim: "录音 02:18 首次给出覆盖码内部代号",
          evidenceIds: ["EVD-113"],
        },
        {
          id: "RS-03",
          verb: "核验",
          claim: "秦彻 22:31 前尚不知此代号，知识状态存在冲突",
          evidenceIds: ["EVD-071", "EVD-113"],
        },
      ],
      conclusion: "内部接应者",
      outcome: "contested",
      hypothesisId: "HYP-002",
    },
  ],
  reasoningGroups: [
    {
      resolutionSpecId: "res_main",
      question: "第五人权限如何进入码头",
      hypotheses: [
        { id: "HYP-002", title: "内部接应者", outcome: "contested" },
        { id: "HYP-001", title: "外部入侵者", outcome: "eliminated" },
      ],
      information: [
        { id: "EVD-071", title: "07 号门禁记录", reliability: "high" },
        { id: "EVD-113", title: "海关电台录音 A-13", reliability: "high" },
        { id: "EVD-209", title: "检修通道监控", reliability: "medium" },
      ],
      assessments: [
        {
          hypothesisId: "HYP-002",
          informationId: "EVD-071",
          effect: "supports",
          strength: "strong",
          rationale: "门禁覆盖签名属于内部 R4 权限，与内部接应解释一致。",
        },
        {
          hypothesisId: "HYP-002",
          informationId: "EVD-113",
          effect: "supports",
          strength: "moderate",
          rationale: "录音首次给出内部代号，为内部接应提供术语闭环。",
        },
        {
          hypothesisId: "HYP-002",
          informationId: "EVD-209",
          effect: "contradicts",
          strength: "weak",
          rationale: "监控缺口无法直接证明内部接应者存在。",
        },
        {
          hypothesisId: "HYP-001",
          informationId: "EVD-071",
          effect: "contradicts",
          strength: "strong",
          rationale: "覆盖签名需要内部 R4 权限，外部入侵无法解释。",
        },
        {
          hypothesisId: "HYP-001",
          informationId: "EVD-113",
          effect: "neutral",
          strength: "weak",
          rationale: "录音未提及外部人员，无法支持外部入侵解释。",
        },
        {
          hypothesisId: "HYP-001",
          informationId: "EVD-209",
          effect: "supports",
          strength: "moderate",
          rationale: "监控缺口可能对应外部人员破坏设备。",
        },
      ],
    },
  ],
  mapMarkers: defaultMapMarkers,
  mapLabels: defaultMapLabels,
  drawer: {
    audioTitle: "海关电台录音 A-13",
    audioDuration: "03:42",
    audioProgress: "01:58 / 03:42",
    keyTime: "02:18",
    keyExcerpt: "那个覆盖码，我们内部叫它第五人权限。",
    transcript:
      "唐默随后要求不要在公开频道提及。此处是该术语第一次进入秦彻可用的知识状态。",
    logs: [
      { time: "10:42:11", actor: "Validator", detail: "比较 EV-1825 与 PER-001 的事件前知识状态。" },
      { time: "10:42:12", actor: "Retrieval", detail: "命中 A-13 02:18 与门禁审计记录 22:31。" },
      { time: "10:42:13", actor: "Validator", detail: "产生 S0 问题；未输出模型内部思维文本。" },
    ],
  },
  initialAuditEntries: [...initialAuditEntries],
  defaultEventId: "EV-1825",
  defaultObjectId: "EV-1825",
  defaultIssueId: "ISSUE-KNOW-001",
};

interface CandidateBlueprint {
  focus: WorkbenchCandidateFocus;
  focusLabel: string;
  title: string;
  caseTitle: string;
  monogram: string;
  summary: string;
  timelineTitle: string;
  mapTitle: string;
  mapMeta: string;
  mapNote: string;
  protagonist: string;
  subject: string;
  witness: string;
  mainLocation: string;
  restrictedLocation: string;
  evidence: [string, string, string];
  hypothesis: string;
  events: Array<{
    time: string;
    label: string;
    location: string;
    summary: string;
  }>;
  primaryIssue: {
    title: string;
    summary: string;
    before: string;
    claim: string;
    after: string;
    patchBefore: string;
    patchAfter: string;
  };
  secondaryIssue: {
    title: string;
    summary: string;
    before: string;
    claim: string;
    after: string;
    patchBefore: string;
    patchAfter: string;
  };
  keyTerm: string;
  audioTitle: string;
  audioExcerpt: string;
  strengths: string[];
  tradeoffs: string[];
  reasoningPaths: ReasoningPath[];
}

const candidateBlueprints: CandidateBlueprint[] = [
  {
    focus: "structure",
    focusLabel: "结构优先",
    title: "缺页校准稿",
    caseTitle: "缺页校准案",
    monogram: "校",
    summary: "把三份可靠记录拆成四个可核验节点，让每一次信息进入都有明确时间锚。",
    timelineTitle: "封存前 39 分钟的校准链",
    mapTitle: "中央档案馆 / 校准廊道",
    mapMeta: "4 CHECKPOINTS",
    mapNote: "文字替代：交接台与校准室相隔两个权限门，22:12 的两次签名不可能由同一人完成。",
    protagonist: "沈砚",
    subject: "顾遥",
    witness: "周既白",
    mainLocation: "中央档案馆",
    restrictedLocation: "七号校准室",
    evidence: ["三卷一致性校验单", "封存钟偏移日志", "交接台铅字校样"],
    hypothesis: "共享校准层",
    events: [
      { time: "21:40", label: "三卷校验同时通过", location: "中央档案馆", summary: "三份独立档案获得相同校验值，但纸张纤维批次并不一致。" },
      { time: "22:05", label: "封存钟缺失十一分钟", location: "七号校准室", summary: "主时钟连续运行，三个副本却同时跳过了十一分钟。" },
      { time: "22:12", label: "顾遥提交交接记录", location: "交接台", summary: "顾遥的签名出现在两个权限门内侧，形成不可并存的路径。" },
      { time: "22:19", label: "修订指纹被识别", location: "七号校准室", summary: "沈砚发现三卷都带有同一枚只存在于内部校准层的修订指纹。" },
    ],
    primaryIssue: {
      title: "沈砚提前认出“共享校准层”",
      summary: "事件使用了稍后才由校验单证明的内部术语，破坏信息进入顺序。",
      before: "22:19 前，沈砚只看见三卷拥有相同修订指纹。",
      claim: "22:19，沈砚立即断定指纹来自共享校准层。",
      after: "22:28，周既白交出的旧校验单首次解释共享校准层。",
      patchBefore: "沈砚一眼认出共享校准层留下的指纹。",
      patchAfter: "沈砚先记录陌生指纹；读到旧校验单后，才确认它来自共享校准层。",
    },
    secondaryIssue: {
      title: "顾遥在两个权限门内同时签名",
      summary: "两处签名只相差一分钟，需要说明复写机制或修订事件时间。",
      before: "22:11，顾遥在交接台完成纸本登记。",
      claim: "22:12，顾遥本人又在校准室内签字。",
      after: "铅字校样显示第二处签名由自动复写板转印。",
      patchBefore: "顾遥离开交接台后进入校准室签字。",
      patchAfter: "顾遥留在交接台；校准室签名随后被证实为复写板转印。",
    },
    keyTerm: "共享校准层",
    audioTitle: "交接台口述记录 C-07",
    audioExcerpt: "那枚指纹只会在共享校准层里出现。",
    strengths: ["事件因果最清楚", "每条证据都有进入时间"],
    tradeoffs: ["场景气氛较克制", "人物关系留白较多"],
    reasoningPaths: [
      {
        id: "RP-01",
        question: "共享校准层是否真实存在",
        evidenceIds: ["EVD-071", "EVD-113", "EVD-209"],
        steps: [
          {
            id: "RS-01",
            verb: "比对",
            claim: "三卷独立档案共享同一枚修订指纹",
            evidenceIds: ["EVD-071", "EVD-113"],
          },
          {
            id: "RS-02",
            verb: "溯源",
            claim: "该指纹只存在于内部校准层",
            evidenceIds: ["EVD-209"],
          },
          {
            id: "RS-03",
            verb: "锚定",
            claim: "指纹进入时间晚于事件文本声称",
            evidenceIds: ["EVD-113"],
          },
        ],
        conclusion: "共享校准层",
        outcome: "supported",
        hypothesisId: "HYP-002",
      },
    ],
  },
  {
    focus: "atmosphere",
    focusLabel: "氛围优先",
    title: "封存室夜班稿",
    caseTitle: "封存室夜班",
    monogram: "夜",
    summary: "把不存在的时间藏进夜班声场、纸页温度和熄灯次序，让调查像逐层揭开描图纸。",
    timelineTitle: "白噪停止前 27 分钟",
    mapTitle: "旧馆夜班区 / 负一层封存室",
    mapMeta: "3 SOUND ZONES",
    mapNote: "文字替代：负一层封存室与抄录间只有一条回声廊，录音中的脚步方向与纸本登记相反。",
    protagonist: "沈砚",
    subject: "林雾",
    witness: "陈序",
    mainLocation: "旧馆夜班区",
    restrictedLocation: "负一层封存室",
    evidence: ["白噪机磁带 N-4", "低温封存纸温记录", "夜班抄录本"],
    hypothesis: "回声记忆诱导",
    events: [
      { time: "23:10", label: "白噪机突然停转", location: "旧馆夜班区", summary: "持续十年的背景噪声中断，所有值班员都记得灯光先熄灭。" },
      { time: "23:17", label: "封存纸出现余温", location: "负一层封存室", summary: "三份声称从未打开的档案仍保留手掌温度。" },
      { time: "23:25", label: "抄录本写下空白时段", location: "夜班抄录间", summary: "林雾记录了一段无人经历、却被三人共同描述的七分钟。" },
      { time: "23:37", label: "第四段呼吸被听见", location: "回声廊", summary: "沈砚在白噪磁带里听见第四个人的呼吸和翻页声。" },
    ],
    primaryIssue: {
      title: "沈砚提前知道录音里有第四个人",
      summary: "第四段呼吸要到频谱校正后才能辨认，事件文本却提前给出结论。",
      before: "23:37 前，沈砚只知道磁带比值班人数多出一组呼吸节奏。",
      claim: "23:37，她立即认出那是第四个人。",
      after: "23:46，陈序提供轮班表后，第四组呼吸才具备身份含义。",
      patchBefore: "沈砚听见第四个人藏在白噪后呼吸。",
      patchAfter: "沈砚先标记多出的一组呼吸；拿到轮班表后，才确认现场存在第四个人。",
    },
    secondaryIssue: {
      title: "林雾在封存室和抄录间同时出现",
      summary: "纸温记录与抄录签名重叠，需要区分本人活动与预先留下的纸页。",
      before: "23:23，门锁记录林雾仍在负一层。",
      claim: "23:25，林雾在楼上的抄录本签名。",
      after: "墨迹干燥度显示签名早于标注时间二十分钟。",
      patchBefore: "林雾赶回抄录间写下七分钟空白。",
      patchAfter: "抄录本在 23:25 被发现；签名其实早在夜班开始前就已写下。",
    },
    keyTerm: "第四段呼吸",
    audioTitle: "白噪机磁带 N-4",
    audioExcerpt: "停机前有四组呼吸，最后一组伴随翻页声。",
    strengths: ["场景记忆点强", "来源形式更丰富"],
    tradeoffs: ["时间线需要更仔细阅读", "真相解释更含蓄"],
    reasoningPaths: [
      {
        id: "RP-01",
        question: "封存纸为何留有手掌余温",
        evidenceIds: ["EVD-113", "EVD-209"],
        steps: [
          {
            id: "RS-01",
            verb: "读取",
            claim: "纸温记录显示纸页在最近十分钟被触碰",
            evidenceIds: ["EVD-113"],
          },
          {
            id: "RS-02",
            verb: "对照",
            claim: "抄录本显示该时段无人值守",
            evidenceIds: ["EVD-209"],
          },
          {
            id: "RS-03",
            verb: "推断",
            claim: "有人活动却被集体遗忘，记忆存在诱导",
            evidenceIds: ["EVD-113", "EVD-209"],
          },
        ],
        conclusion: "回声记忆诱导",
        outcome: "supported",
        hypothesisId: "HYP-002",
      },
      {
        id: "RP-02",
        question: "空白七分钟是否真实存在",
        evidenceIds: ["EVD-209"],
        steps: [
          {
            id: "RS-04",
            verb: "检验",
            claim: "抄录本签名墨迹早于标注时间约二十分钟",
            evidenceIds: ["EVD-209"],
          },
          {
            id: "RS-05",
            verb: "质疑",
            claim: "记录可能预先写下，而非共同经历",
            evidenceIds: ["EVD-209"],
          },
        ],
        conclusion: "空白时段为事后伪造",
        outcome: "contested",
        hypothesisId: "HYP-002",
      },
    ],
  },
  {
    focus: "reasoning",
    focusLabel: "推理优先",
    title: "第七码互证稿",
    caseTitle: "第七码互证案",
    monogram: "证",
    summary: "让三份可靠记录分别证明彼此错误，并保留两条竞争解释直到最后一次校验。",
    timelineTitle: "第七码启用后的 31 分钟",
    mapTitle: "索引塔 / 互证机房",
    mapMeta: "2 COMPETING PATHS",
    mapNote: "文字替代：索引塔与互证机房共享同一报码总线，三份记录可能同时被第四条隐藏索引重写。",
    protagonist: "沈砚",
    subject: "黎衡",
    witness: "许岑",
    mainLocation: "索引塔",
    restrictedLocation: "互证机房",
    evidence: ["第七码索引卡", "互证机房运算带", "三方签章底片"],
    hypothesis: "隐藏的第四索引",
    events: [
      { time: "20:48", label: "三方签章完成互证", location: "索引塔", summary: "三份来源互相引用并通过校验，形成看似不可推翻的闭环。" },
      { time: "21:02", label: "第七码首次被调用", location: "互证机房", summary: "不存在于公开目录的索引码进入运算带。" },
      { time: "21:11", label: "黎衡否认写入索引", location: "索引塔", summary: "黎衡的权限签章有效，但知识记录显示他从未见过第七码。" },
      { time: "21:19", label: "第四索引浮出闭环", location: "互证机房", summary: "沈砚发现三份记录并非彼此证明，而是共同引用了一条被隐藏的母索引。" },
    ],
    primaryIssue: {
      title: "沈砚提前排除黎衡",
      summary: "黎衡的知识记录尚未核验，事件却已经把他从候选解释中移除。",
      before: "21:19 前，只能确认黎衡的权限签章出现在运算带。",
      claim: "21:19，沈砚断定黎衡不可能写入第七码。",
      after: "21:27，三方签章底片才证明他的签章被母索引复用。",
      patchBefore: "沈砚确认黎衡无辜，转向隐藏索引。",
      patchAfter: "沈砚暂时保留黎衡与隐藏索引两条解释，直到签章底片完成互证。",
    },
    secondaryIssue: {
      title: "第七码在创建前已被引用",
      summary: "运算带的调用时间早于索引卡登记时间，需要解释回写或调整记录。",
      before: "20:55 的目录快照中尚无第七码。",
      claim: "21:02，运算带调用已经存在的第七码。",
      after: "索引卡背面的压力痕显示，它在 20:40 已完成但未登记。",
      patchBefore: "系统调用了刚被创建的第七码。",
      patchAfter: "系统调用了一张尚未登记、但已在 20:40 制成的第七码索引卡。",
    },
    keyTerm: "隐藏的第四索引",
    audioTitle: "互证机房报码 R-7",
    audioExcerpt: "三份记录没有互相作证，它们都在引用第四条索引。",
    strengths: ["竞争假设最完整", "验证问题密度最高"],
    tradeoffs: ["认知负荷最高", "需要更多图谱对照"],
    reasoningPaths: [
      {
        id: "RP-01",
        question: "第七码由谁写入",
        evidenceIds: ["EVD-071", "EVD-113"],
        steps: [
          {
            id: "RS-01",
            verb: "调取",
            claim: "运算带调用时间早于索引卡登记",
            evidenceIds: ["EVD-113"],
          },
          {
            id: "RS-02",
            verb: "检验",
            claim: "索引卡压力痕显示 20:40 已制成",
            evidenceIds: ["EVD-071"],
          },
        ],
        conclusion: "隐藏的第四索引",
        outcome: "supported",
        hypothesisId: "HYP-002",
      },
      {
        id: "RP-02",
        question: "黎衡能否被完全排除",
        evidenceIds: ["EVD-071", "EVD-113", "EVD-209"],
        steps: [
          {
            id: "RS-03",
            verb: "核验",
            claim: "黎衡签章有效，但知识记录从未出现第七码",
            evidenceIds: ["EVD-113", "EVD-071"],
          },
          {
            id: "RS-04",
            verb: "对照",
            claim: "底片显示签章被母索引复用",
            evidenceIds: ["EVD-209"],
          },
        ],
        conclusion: "两条竞争解释并存",
        outcome: "contested",
        hypothesisId: "HYP-002",
      },
      {
        id: "RP-03",
        question: "三份记录是否彼此独立互证",
        evidenceIds: ["EVD-209"],
        steps: [
          {
            id: "RS-05",
            verb: "比对",
            claim: "三方签章共同引用同一母索引",
            evidenceIds: ["EVD-209"],
          },
          {
            id: "RS-06",
            verb: "排除",
            claim: "闭环并非三份独立来源的交叉证明",
            evidenceIds: ["EVD-209"],
          },
        ],
        conclusion: "三份记录彼此独立",
        outcome: "eliminated",
        hypothesisId: "HYP-002",
      },
    ],
  },
];

function buildCandidateSeed(
  blueprint: CandidateBlueprint,
  brief: CandidateBriefInput,
  briefVersion: number,
): WorkbenchSeed {
  const [eventA, eventB, eventC, eventD] = blueprint.events;
  const objects: CaseObject[] = [
    { id: "PER-001", kind: "person", label: blueprint.protagonist, code: "档案修复师 / 当前视角", meta: "已知 12 条 · 未知 4 条", relatedEventIds: ["EV-1800", "EV-1825"] },
    { id: "PER-004", kind: "person", label: blueprint.subject, code: "关键当事人", meta: `最后记录 ${eventC.time}`, relatedEventIds: ["EV-1812", "EV-1823"] },
    { id: "PER-009", kind: "person", label: blueprint.witness, code: "记录保管人", meta: "证词待复核", relatedEventIds: ["EV-1800", "EV-1825"] },
    { id: "EVD-071", kind: "evidence", label: blueprint.evidence[0], code: "纸本记录 / 已校验", meta: `${eventD.time} · 关键锚点`, relatedEventIds: ["EV-1825"] },
    { id: "EVD-113", kind: "evidence", label: blueprint.evidence[1], code: "录音或日志 / 已转写", meta: "可信度 0.91", relatedEventIds: ["EV-1823", "EV-1825"] },
    { id: "EVD-209", kind: "evidence", label: blueprint.evidence[2], code: "底片 / 有时间缺口", meta: "缺口待解释", relatedEventIds: ["EV-1812"] },
    { id: "EV-1800", kind: "event", label: eventA.label, code: `${eventA.time} · ${eventA.location}`, meta: "因果入口", relatedEventIds: ["EV-1800"] },
    { id: "EV-1812", kind: "event", label: eventB.label, code: `${eventB.time} · ${eventB.location}`, meta: "1 个 S1 问题", relatedEventIds: ["EV-1812"] },
    { id: "EV-1823", kind: "event", label: eventC.label, code: `${eventC.time} · ${eventC.location}`, meta: "来源可展开", relatedEventIds: ["EV-1823"] },
    { id: "EV-1825", kind: "event", label: eventD.label, code: `${eventD.time} · ${eventD.location}`, meta: "1 个 S0 问题", relatedEventIds: ["EV-1825"] },
    { id: "LOC-003", kind: "location", label: blueprint.mainLocation, code: "主要区域", meta: "3 个关联事件", relatedEventIds: ["EV-1800", "EV-1812", "EV-1823"] },
    { id: "LOC-007", kind: "location", label: blueprint.restrictedLocation, code: "受限区域", meta: "权限级别 R4", relatedEventIds: ["EV-1812", "EV-1825"] },
    { id: "HYP-002", kind: "hypothesis", label: blueprint.hypothesis, code: "候选假设 / 仍待互证", meta: brief.authorAnswer || "尚未锁定最终答案", relatedEventIds: ["EV-1800", "EV-1825"] },
  ];
  const events: TimelineEvent[] = [
    { id: "EV-1800", ...eventA, relatedObjectIds: ["LOC-003", "PER-009", "HYP-002"], issueIds: [] },
    { id: "EV-1812", ...eventB, relatedObjectIds: ["PER-004", "LOC-007", "EVD-209"], issueIds: ["ISSUE-TIME-006"] },
    { id: "EV-1823", ...eventC, relatedObjectIds: ["PER-004", "EVD-113", "LOC-003"], issueIds: [] },
    { id: "EV-1825", ...eventD, relatedObjectIds: ["PER-001", "PER-009", "EVD-071", "EVD-113", "LOC-007", "HYP-002"], issueIds: ["ISSUE-KNOW-001"] },
  ];
  const issues: ValidationIssue[] = [
    { id: "ISSUE-KNOW-001", severity: "S0", title: blueprint.primaryIssue.title, summary: blueprint.primaryIssue.summary, eventId: "EV-1825", rule: "KNOWLEDGE_STATE_BEFORE_EVENT", evidenceIds: ["EVD-071", "EVD-113"], beforeKnowledge: blueprint.primaryIssue.before, eventClaim: blueprint.primaryIssue.claim, afterKnowledge: blueprint.primaryIssue.after, patchBefore: blueprint.primaryIssue.patchBefore, patchAfter: blueprint.primaryIssue.patchAfter },
    { id: "ISSUE-TIME-006", severity: "S1", title: blueprint.secondaryIssue.title, summary: blueprint.secondaryIssue.summary, eventId: "EV-1812", rule: "TEMPORAL_EXCLUSIVITY", evidenceIds: ["EVD-209"], beforeKnowledge: blueprint.secondaryIssue.before, eventClaim: blueprint.secondaryIssue.claim, afterKnowledge: blueprint.secondaryIssue.after, patchBefore: blueprint.secondaryIssue.patchBefore, patchAfter: blueprint.secondaryIssue.patchAfter },
  ];
  const sources: SourceItem[] = [
    { id: "SRC-A13", kind: "audio", label: blueprint.audioTitle, meta: `关键片段 ${eventC.time}`, excerpt: blueprint.audioExcerpt, eventId: "EV-1823", evidenceObjectId: "EVD-113" },
    { id: "SRC-T13", kind: "transcript", label: `${blueprint.audioTitle}人工校订`, meta: `校订者：${blueprint.protagonist} · 可信度高`, excerpt: `${blueprint.keyTerm}首次获得可验证含义。`, eventId: "EV-1823", evidenceObjectId: "EVD-113" },
    { id: "SRC-R71", kind: "record", label: blueprint.evidence[0], meta: "签章校验通过", excerpt: eventD.summary, eventId: "EV-1825", evidenceObjectId: "EVD-071" },
    { id: "SRC-Q09", kind: "retrieval", label: `检索命中：${blueprint.keyTerm}`, meta: "命中 3 / 29 · 不进入当前事实", excerpt: `该词只在校订转写与一份未采用备忘中出现。`, eventId: "EV-1825", evidenceObjectId: "EVD-113" },
  ];
  const candidateGraphNodes: GraphNode[] = [
    { objectId: "PER-001", x: 16, y: 24 }, { objectId: "PER-009", x: 79, y: 21 },
    { objectId: "EV-1825", x: 50, y: 45 }, { objectId: "EVD-071", x: 19, y: 72 },
    { objectId: "EVD-113", x: 77, y: 75 }, { objectId: "LOC-007", x: 49, y: 86 },
    { objectId: "HYP-002", x: 87, y: 49 },
  ];
  const candidateGraphEdges: GraphEdge[] = [
    { from: "PER-001", to: "EV-1825", label: "发现" }, { from: "PER-009", to: "EV-1825", label: "证词" },
    { from: "EVD-071", to: "EV-1825", label: "记录" }, { from: "EVD-113", to: "EV-1825", label: "后知" },
    { from: "LOC-007", to: "EV-1825", label: "发生于" }, { from: "HYP-002", to: "EV-1825", label: "解释" },
  ];
  return {
    id: `brief-${briefVersion}-${blueprint.focus}`,
    caseMeta: {
      title: blueprint.caseTitle,
      monogram: blueprint.monogram,
      subtitle: brief.creativeIntent,
      revision: `简报 V${String(briefVersion).padStart(2, "0")} · ${blueprint.focusLabel}`,
      timelineTitle: blueprint.timelineTitle,
      timelineMeta: `4 EVENTS · 2 ISSUES`,
      mapTitle: blueprint.mapTitle,
      mapMeta: blueprint.mapMeta,
      mapNote: blueprint.mapNote,
      relationshipSummary: `以“${eventD.label}”为中心，连接${blueprint.protagonist}、${blueprint.witness}、两份关键记录、${blueprint.restrictedLocation}与“${blueprint.hypothesis}”假设。所有节点均可通过关系表访问。`,
      exportTitle: "候选调查卷 / 开发包",
      exportCode: `CASEFILE / B${briefVersion}-${blueprint.focus.toUpperCase()}`,
      exportSubtitle: `${blueprint.focusLabel}候选 · 前端 Fixture`,
      dossierVisibleRoles: `${blueprint.protagonist}、${blueprint.witness}`,
      branchLabel: `${blueprint.focusLabel}主线`,
      protagonist: blueprint.protagonist,
    },
    caseObjects: objects,
    timelineEvents: events,
    validationIssues: issues,
    sourceItems: sources,
    graphNodes: candidateGraphNodes,
    graphEdges: candidateGraphEdges,
    reasoningPaths: blueprint.reasoningPaths,
    mapMarkers: events.map((event, index) => ({ eventId: event.id, label: event.label, x: [19, 54, 30, 73][index], y: [63, 52, 25, 70][index] })),
    mapLabels: [
      { label: blueprint.mainLocation, x: 8, y: 11 },
      { label: blueprint.restrictedLocation, x: 42, y: 42 },
      { label: "关键记录点", x: 70, y: 84 },
    ],
    drawer: {
      audioTitle: blueprint.audioTitle,
      audioDuration: "03:42",
      audioProgress: "01:58 / 03:42",
      keyTime: "02:18",
      keyExcerpt: blueprint.audioExcerpt,
      transcript: `该片段是“${blueprint.keyTerm}”第一次进入${blueprint.protagonist}可用知识状态的来源。`,
      logs: [
        { time: "10:42:11", actor: "Validator", detail: `比较 ${eventD.label} 与${blueprint.protagonist}的事件前知识状态。` },
        { time: "10:42:12", actor: "Retrieval", detail: `命中${blueprint.audioTitle}与${blueprint.evidence[0]}。` },
        { time: "10:42:13", actor: "Validator", detail: "产生 S0 问题；未输出模型内部思维文本。" },
      ],
    },
    initialAuditEntries: [
      { id: "AUD-120", time: "10:42", actor: "Validator", action: "发现知识状态冲突", detail: "ISSUE-KNOW-001 · S0 · 候选校验" },
      { id: "AUD-119", time: "10:41", actor: blueprint.protagonist, action: "采用关键记录引用", detail: "EVD-071 → EV-1825" },
      { id: "AUD-118", time: "10:36", actor: "Retrieval", action: "更新来源检索命中", detail: `${blueprint.audioTitle} · 3 个片段` },
    ],
    defaultEventId: "EV-1825",
    defaultObjectId: "EV-1825",
    defaultIssueId: "ISSUE-KNOW-001",
  };
}

export function buildWorkbenchCandidates(
  brief: CandidateBriefInput,
  briefVersion: number,
): WorkbenchCandidate[] {
  return candidateBlueprints.map((blueprint) => {
    const seed = buildCandidateSeed(blueprint, brief, briefVersion);
    return {
      id: seed.id,
      briefVersion,
      focus: blueprint.focus,
      focusLabel: blueprint.focusLabel,
      title: blueprint.title,
      summary: blueprint.summary,
      reasoningQuestion:
        brief.reasoningProposition || "三份可靠记录为何会共同证明一段不存在的时间？",
      objectCounts: {
        entities: seed.caseObjects.filter((object) => object.kind === "person" || object.kind === "location").length,
        events: seed.timelineEvents.length,
        information_units: seed.caseObjects.filter((object) => object.kind === "evidence").length,
        reasoning_paths: blueprint.reasoningPaths.length,
      },
      constraintStatements: brief.constraints,
      strengths: blueprint.strengths,
      tradeoffs: blueprint.tradeoffs,
      workbenchSeed: seed,
    };
  });
}

export function validateWorkbenchSeed(seed: WorkbenchSeed) {
  const errors: string[] = [];
  const objectIds = new Set(seed.caseObjects.map((object) => object.id));
  const eventIds = new Set(seed.timelineEvents.map((event) => event.id));
  const issueIds = new Set(seed.validationIssues.map((issue) => issue.id));
  for (const event of seed.timelineEvents) {
    for (const objectId of event.relatedObjectIds) {
      if (!objectIds.has(objectId)) errors.push(`${event.id} 引用未知对象 ${objectId}`);
    }
    for (const issueId of event.issueIds) {
      if (!issueIds.has(issueId)) errors.push(`${event.id} 引用未知问题 ${issueId}`);
    }
  }
  for (const issue of seed.validationIssues) {
    if (issue.eventId && !eventIds.has(issue.eventId)) {
      errors.push(`${issue.id} 引用未知事件 ${issue.eventId}`);
    }
    for (const evidenceId of issue.evidenceIds) {
      if (!objectIds.has(evidenceId)) errors.push(`${issue.id} 引用未知证据 ${evidenceId}`);
    }
  }
  for (const source of seed.sourceItems) {
    if (!eventIds.has(source.eventId)) errors.push(`${source.id} 引用未知事件 ${source.eventId}`);
    if (source.evidenceObjectId && !objectIds.has(source.evidenceObjectId)) errors.push(`${source.id} 引用未知证据 ${source.evidenceObjectId}`);
  }
  for (const edge of seed.graphEdges) {
    if (!objectIds.has(edge.from) || !objectIds.has(edge.to)) errors.push(`关系 ${edge.from} → ${edge.to} 存在未知端点`);
  }
  for (const path of seed.reasoningPaths) {
    for (const evidenceId of [...path.evidenceIds, ...path.steps.flatMap((step) => step.evidenceIds)]) {
      if (!objectIds.has(evidenceId)) errors.push(`推理 ${path.id} 引用未知证据 ${evidenceId}`);
    }
    if (!objectIds.has(path.hypothesisId)) errors.push(`推理 ${path.id} 引用未知假设 ${path.hypothesisId}`);
  }
  if (seed.defaultEventId !== null && !eventIds.has(seed.defaultEventId)) errors.push("默认事件不存在");
  if (seed.defaultObjectId !== null && !objectIds.has(seed.defaultObjectId)) errors.push("默认对象不存在");
  if (seed.defaultIssueId !== null && !issueIds.has(seed.defaultIssueId)) errors.push("默认问题不存在");
  return errors;
}

export function getObject(seed: WorkbenchSeed, objectId: string | null) {
  return seed.caseObjects.find((item) => item.id === objectId);
}

export function getEvent(seed: WorkbenchSeed, eventId: string | null) {
  return seed.timelineEvents.find((item) => item.id === eventId);
}
