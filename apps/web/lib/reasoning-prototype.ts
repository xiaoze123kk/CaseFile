export type ReasoningMode = "organize" | "explore";

export type ReasoningStatus =
  | "idle"
  | "running"
  | "review"
  | "ready"
  | "stale"
  | "failed"
  | "cancelled";

export type ReasoningView = "overview" | "path";

export type ReasoningPathKind = "primary" | "alternative" | "excluded";

export type ReasoningNodeKind =
  | "source-bundle"
  | "claim"
  | "hypothesis"
  | "conclusion"
  | "gap";

export type ReasoningNodeStatus =
  | "existing"
  | "candidate"
  | "confirmed"
  | "excluded"
  | "conflict";

export type ReasoningEdgeKind =
  | "supports"
  | "refutes"
  | "explains"
  | "requires";

export type ReasoningProposalStatus = "pending" | "applied" | "rejected";

export interface ReasoningPath {
  id: string;
  code: string;
  title: string;
  question: string;
  summary: string;
  kind: ReasoningPathKind;
  reasoningType: "deductive" | "inductive" | "abductive" | "mixed";
  confidence: number;
  sourceCoverage: number;
  gapCount: number;
  conflictCount: number;
  nodeIds: string[];
  sharedSourceIds: string[];
}

export interface ReasoningNode {
  id: string;
  pathId: string;
  kind: ReasoningNodeKind;
  status: ReasoningNodeStatus;
  label: string;
  statement: string;
  sourceIds: string[];
  confidence?: number;
  tags: string[];
  proposalId?: string;
  userEditable?: boolean;
}

export interface ReasoningEdge {
  id: string;
  pathId: string;
  source: string;
  target: string;
  kind: ReasoningEdgeKind;
  status: ReasoningNodeStatus;
  label: string;
  confidence?: number;
  proposalId?: string;
}

export interface ReasoningNodePosition {
  x: number;
  y: number;
}

export interface ReasoningProposalChange {
  id: string;
  targetId: string;
  targetType: "node" | "edge";
  action: "create" | "connect" | "classify";
  label: string;
  description: string;
  rationale: string;
  sourceIds: string[];
  confidence?: number;
  selected: boolean;
  status: ReasoningProposalStatus;
}

export interface ReasoningRunRecord {
  id: string;
  mode: ReasoningMode;
  baseRevision: number;
  outcomeRevision?: number;
  status: Exclude<ReasoningStatus, "idle">;
  summary: string;
  startedAt: string;
}

export interface PrototypeReasoningState {
  status: ReasoningStatus;
  mode: ReasoningMode;
  view: ReasoningView;
  baseRevision: number;
  outcomeRevision?: number;
  progress: number;
  stage: string;
  activePathId: string;
  selectedNodeId: string;
  selectedProposalId: string;
  paths: ReasoningPath[];
  nodes: ReasoningNode[];
  edges: ReasoningEdge[];
  proposals: ReasoningProposalChange[];
  positions: Record<string, ReasoningNodePosition>;
  expandedBundleIds: string[];
  runs: ReasoningRunRecord[];
  runSequence: number;
  failureMessage: string;
}

export interface ReasoningSourceObject {
  id: string;
  type: "brief" | "event" | "information" | "phase" | "constraint";
  label: string;
  meta: string;
  targetEventId?: string;
}

export const reasoningSourceCatalog: ReasoningSourceObject[] = [
  {
    id: "BR-1800",
    type: "brief",
    label: "核心谜题",
    meta: "谁触发循环重启，第五人权限属于谁",
  },
  {
    id: "EVL-1812",
    type: "event",
    label: "反应堆异常",
    meta: "18:20 · 维护日志出现十二秒空白",
    targetEventId: "EVL-1812",
  },
  {
    id: "EVL-1823",
    type: "event",
    label: "AI 启动保护协议",
    meta: "18:23 · 第五人权限记录被隐藏",
    targetEventId: "EVL-1823",
  },
  {
    id: "EVL-1825",
    type: "event",
    label: "空间站状态回滚",
    meta: "18:25 · 系统回退至 18:00",
    targetEventId: "EVL-1825",
  },
  {
    id: "INFO-2107",
    type: "information",
    label: "第五人权限记录",
    meta: "受限信息 · 可见范围存在争议",
    targetEventId: "EVL-1823",
  },
  {
    id: "INFO-4402",
    type: "information",
    label: "舱外脚印",
    meta: "可发现线索 · 尚未完成回收",
    targetEventId: "EVL-1825",
  },
  {
    id: "LOG-1819",
    type: "information",
    label: "访问日志",
    meta: "18:19 · 内部凭据访问主控协议",
    targetEventId: "EVL-1812",
  },
  {
    id: "PHASE-03",
    type: "phase",
    label: "阶段 03 · 保护协议",
    meta: "时间锚点仍需确认",
    targetEventId: "EVL-1823",
  },
  {
    id: "CON-ROOT-01",
    type: "constraint",
    label: "唯一根因约束",
    meta: "核心因果必须唯一可验证",
  },
];

const basePaths: ReasoningPath[] = [
  {
    id: "path-root",
    code: "PATH-01",
    title: "保护协议触发循环",
    question: "为什么空间站反复重启？",
    summary: "当前主路径认为重启是保护协议主动执行，而非普通事故。",
    kind: "primary",
    reasoningType: "abductive",
    confidence: 0.86,
    sourceCoverage: 0.91,
    gapCount: 0,
    conflictCount: 0,
    nodeIds: [
      "root-sources",
      "claim-protocol",
      "hyp-protection",
      "conclusion-root",
    ],
    sharedSourceIds: ["EVL-1823", "EVL-1825", "CON-ROOT-01"],
  },
  {
    id: "path-identity",
    code: "PATH-02",
    title: "第五人身份归属",
    question: "被隐藏的第五人权限属于谁？",
    summary: "脚印、访问日志与权限记录共同指向具备内部凭据的人。",
    kind: "alternative",
    reasoningType: "inductive",
    confidence: 0.72,
    sourceCoverage: 0.78,
    gapCount: 1,
    conflictCount: 0,
    nodeIds: [
      "identity-sources",
      "claim-fifth-person",
      "gap-credential-owner",
      "hyp-insider",
      "conclusion-identity",
    ],
    sharedSourceIds: ["INFO-2107", "INFO-4402", "LOG-1819"],
  },
  {
    id: "path-leak",
    code: "PATH-03",
    title: "权限记录泄露",
    question: "林望为何提前获得受限信息？",
    summary: "可见范围与知识获得顺序不一致，形成独立的信息泄露支线。",
    kind: "alternative",
    reasoningType: "deductive",
    confidence: 0.79,
    sourceCoverage: 0.84,
    gapCount: 1,
    conflictCount: 1,
    nodeIds: [
      "leak-sources",
      "claim-early-access",
      "gap-acquisition-event",
      "hyp-active-leak",
      "conclusion-leak",
    ],
    sharedSourceIds: ["EVL-1823", "INFO-2107", "PHASE-03"],
  },
  {
    id: "path-auto",
    code: "PATH-X1",
    title: "系统自动泄露",
    question: "权限是否由系统故障自动泄露？",
    summary: "该解释被访问日志与协议时间顺序共同反驳。",
    kind: "excluded",
    reasoningType: "deductive",
    confidence: 0.23,
    sourceCoverage: 0.88,
    gapCount: 0,
    conflictCount: 0,
    nodeIds: [
      "auto-sources",
      "claim-system-fault",
      "hyp-automatic-leak",
      "conclusion-auto",
    ],
    sharedSourceIds: ["EVL-1812", "LOG-1819", "EVL-1823"],
  },
];

const baseNodes: ReasoningNode[] = [
  {
    id: "root-sources",
    pathId: "path-root",
    kind: "source-bundle",
    status: "existing",
    label: "协议与回滚记录",
    statement: "保护协议、状态回滚与唯一根因约束。",
    sourceIds: ["EVL-1823", "EVL-1825", "CON-ROOT-01"],
    tags: ["来源包", "3 OBJECTS"],
  },
  {
    id: "claim-protocol",
    pathId: "path-root",
    kind: "claim",
    status: "existing",
    label: "重启由协议主动触发",
    statement: "回滚紧随保护协议发生，且不是反应堆异常的直接结果。",
    sourceIds: ["EVL-1823", "EVL-1825"],
    confidence: 0.88,
    tags: ["主张", "时间顺序"],
  },
  {
    id: "hyp-protection",
    pathId: "path-root",
    kind: "hypothesis",
    status: "confirmed",
    label: "重启本身是一道保护",
    statement: "系统通过循环隔离一次会造成更大损失的事件。",
    sourceIds: ["EVL-1823", "EVL-1825", "BR-1800"],
    confidence: 0.86,
    tags: ["假设", "人工确认"],
    userEditable: true,
  },
  {
    id: "conclusion-root",
    pathId: "path-root",
    kind: "conclusion",
    status: "confirmed",
    label: "保护协议是唯一根因",
    statement: "循环是系统为阻止失控事件执行的主动保护。",
    sourceIds: ["CON-ROOT-01", "EVL-1823", "EVL-1825"],
    confidence: 0.86,
    tags: ["结论", "主路径"],
    userEditable: true,
  },
  {
    id: "identity-sources",
    pathId: "path-identity",
    kind: "source-bundle",
    status: "existing",
    label: "身份痕迹",
    statement: "第五人权限、舱外脚印与内部访问日志。",
    sourceIds: ["INFO-2107", "INFO-4402", "LOG-1819"],
    tags: ["来源包", "3 OBJECTS"],
  },
  {
    id: "claim-fifth-person",
    pathId: "path-identity",
    kind: "claim",
    status: "existing",
    label: "第五人拥有内部访问能力",
    statement: "相关痕迹无法由四名已知角色的公开权限解释。",
    sourceIds: ["INFO-2107", "LOG-1819"],
    confidence: 0.81,
    tags: ["主张", "身份"],
  },
  {
    id: "gap-credential-owner",
    pathId: "path-identity",
    kind: "gap",
    status: "conflict",
    label: "待求证：凭据最初归属",
    statement: "当前没有事件记录内部凭据第一次由谁创建或领取。",
    sourceIds: ["INFO-2107"],
    tags: ["缺口", "待求证"],
  },
  {
    id: "hyp-insider",
    pathId: "path-identity",
    kind: "hypothesis",
    status: "candidate",
    label: "第五人是被抹除的内部成员",
    statement: "第五人并非权限幽灵，而是被回滚机制从公开记录中移除的人。",
    sourceIds: ["INFO-2107", "INFO-4402", "LOG-1819"],
    confidence: 0.72,
    tags: ["AI 候选", "身份"],
    proposalId: "PROP-NODE-01",
    userEditable: true,
  },
  {
    id: "conclusion-identity",
    pathId: "path-identity",
    kind: "conclusion",
    status: "candidate",
    label: "第五人曾属于维护组",
    statement: "现有证据更接近内部成员，而非系统生成的权限实体。",
    sourceIds: ["INFO-4402", "LOG-1819"],
    confidence: 0.68,
    tags: ["AI 候选", "结论"],
    proposalId: "PROP-NODE-02",
    userEditable: true,
  },
  {
    id: "leak-sources",
    pathId: "path-leak",
    kind: "source-bundle",
    status: "existing",
    label: "可见范围与阶段",
    statement: "保护协议事件、权限记录与阶段时间锚点。",
    sourceIds: ["EVL-1823", "INFO-2107", "PHASE-03"],
    tags: ["来源包", "3 OBJECTS"],
  },
  {
    id: "claim-early-access",
    pathId: "path-leak",
    kind: "claim",
    status: "existing",
    label: "林望提前读取受限记录",
    statement: "知识状态与事件可见范围不一致。",
    sourceIds: ["EVL-1823", "INFO-2107"],
    confidence: 0.94,
    tags: ["主张", "S1"],
  },
  {
    id: "gap-acquisition-event",
    pathId: "path-leak",
    kind: "gap",
    status: "conflict",
    label: "待求证：获得事件缺失",
    statement: "没有事件说明林望如何在保护协议前获得权限记录。",
    sourceIds: ["INFO-2107", "PHASE-03"],
    tags: ["缺口", "待求证"],
  },
  {
    id: "hyp-active-leak",
    pathId: "path-leak",
    kind: "hypothesis",
    status: "candidate",
    label: "存在主动信息泄露",
    statement: "有人在保护协议生效前主动扩大了权限记录的可见范围。",
    sourceIds: ["EVL-1823", "INFO-2107", "LOG-1819"],
    confidence: 0.79,
    tags: ["AI 候选", "泄露"],
    proposalId: "PROP-NODE-03",
    userEditable: true,
  },
  {
    id: "conclusion-leak",
    pathId: "path-leak",
    kind: "conclusion",
    status: "candidate",
    label: "权限链需要补写获得节点",
    statement: "在谜底冻结前必须补上权限获得、隐藏与回收事件。",
    sourceIds: ["EVL-1823", "INFO-2107", "PHASE-03"],
    confidence: 0.83,
    tags: ["AI 候选", "设计结论"],
    proposalId: "PROP-NODE-04",
    userEditable: true,
  },
  {
    id: "auto-sources",
    pathId: "path-auto",
    kind: "source-bundle",
    status: "excluded",
    label: "异常与访问日志",
    statement: "反应堆异常、内部访问和保护协议时间顺序。",
    sourceIds: ["EVL-1812", "LOG-1819", "EVL-1823"],
    tags: ["来源包", "反证"],
  },
  {
    id: "claim-system-fault",
    pathId: "path-auto",
    kind: "claim",
    status: "excluded",
    label: "日志空白来自系统故障",
    statement: "如果系统自动泄露，内部凭据访问不应早于保护协议。",
    sourceIds: ["EVL-1812", "LOG-1819"],
    confidence: 0.31,
    tags: ["被反驳主张"],
  },
  {
    id: "hyp-automatic-leak",
    pathId: "path-auto",
    kind: "hypothesis",
    status: "excluded",
    label: "系统自动泄露权限",
    statement: "权限记录因反应堆异常自动进入公开可见范围。",
    sourceIds: ["EVL-1812", "EVL-1823"],
    confidence: 0.23,
    tags: ["已排除", "反证 2"],
  },
  {
    id: "conclusion-auto",
    pathId: "path-auto",
    kind: "conclusion",
    status: "excluded",
    label: "自动泄露解释不成立",
    statement: "访问顺序和保护协议均与自动泄露假设冲突。",
    sourceIds: ["LOG-1819", "EVL-1823"],
    confidence: 0.91,
    tags: ["排除结论"],
  },
];

const baseEdges: ReasoningEdge[] = [
  {
    id: "edge-root-1",
    pathId: "path-root",
    source: "root-sources",
    target: "claim-protocol",
    kind: "supports",
    status: "existing",
    label: "支持",
    confidence: 0.92,
  },
  {
    id: "edge-root-2",
    pathId: "path-root",
    source: "claim-protocol",
    target: "hyp-protection",
    kind: "explains",
    status: "confirmed",
    label: "解释",
    confidence: 0.86,
  },
  {
    id: "edge-root-3",
    pathId: "path-root",
    source: "hyp-protection",
    target: "conclusion-root",
    kind: "supports",
    status: "confirmed",
    label: "推出",
    confidence: 0.86,
  },
  {
    id: "edge-identity-1",
    pathId: "path-identity",
    source: "identity-sources",
    target: "claim-fifth-person",
    kind: "supports",
    status: "existing",
    label: "支持",
    confidence: 0.81,
  },
  {
    id: "edge-identity-gap",
    pathId: "path-identity",
    source: "gap-credential-owner",
    target: "hyp-insider",
    kind: "requires",
    status: "conflict",
    label: "待求证",
  },
  {
    id: "edge-identity-2",
    pathId: "path-identity",
    source: "claim-fifth-person",
    target: "hyp-insider",
    kind: "supports",
    status: "candidate",
    label: "支持",
    confidence: 0.72,
    proposalId: "PROP-EDGE-01",
  },
  {
    id: "edge-identity-3",
    pathId: "path-identity",
    source: "hyp-insider",
    target: "conclusion-identity",
    kind: "explains",
    status: "candidate",
    label: "解释",
    confidence: 0.68,
    proposalId: "PROP-EDGE-02",
  },
  {
    id: "edge-leak-1",
    pathId: "path-leak",
    source: "leak-sources",
    target: "claim-early-access",
    kind: "supports",
    status: "existing",
    label: "支持",
    confidence: 0.94,
  },
  {
    id: "edge-leak-gap",
    pathId: "path-leak",
    source: "gap-acquisition-event",
    target: "hyp-active-leak",
    kind: "requires",
    status: "conflict",
    label: "缺失环节",
  },
  {
    id: "edge-leak-2",
    pathId: "path-leak",
    source: "claim-early-access",
    target: "hyp-active-leak",
    kind: "supports",
    status: "candidate",
    label: "支持",
    confidence: 0.79,
    proposalId: "PROP-EDGE-03",
  },
  {
    id: "edge-leak-3",
    pathId: "path-leak",
    source: "hyp-active-leak",
    target: "conclusion-leak",
    kind: "explains",
    status: "candidate",
    label: "解释",
    confidence: 0.83,
    proposalId: "PROP-EDGE-04",
  },
  {
    id: "edge-auto-1",
    pathId: "path-auto",
    source: "auto-sources",
    target: "claim-system-fault",
    kind: "supports",
    status: "excluded",
    label: "曾支持",
    confidence: 0.31,
  },
  {
    id: "edge-auto-2",
    pathId: "path-auto",
    source: "claim-system-fault",
    target: "hyp-automatic-leak",
    kind: "supports",
    status: "excluded",
    label: "弱支持",
    confidence: 0.23,
  },
  {
    id: "edge-auto-3",
    pathId: "path-auto",
    source: "auto-sources",
    target: "hyp-automatic-leak",
    kind: "refutes",
    status: "excluded",
    label: "反驳",
    confidence: 0.91,
  },
  {
    id: "edge-auto-4",
    pathId: "path-auto",
    source: "hyp-automatic-leak",
    target: "conclusion-auto",
    kind: "explains",
    status: "excluded",
    label: "排除",
    confidence: 0.91,
  },
];

const exploreProposals: ReasoningProposalChange[] = [
  {
    id: "PROP-NODE-01",
    targetId: "hyp-insider",
    targetType: "node",
    action: "create",
    label: "新增身份假设",
    description: "第五人是被回滚机制从公开记录中移除的内部成员。",
    rationale: "权限记录、舱外脚印和内部访问日志形成三条相互独立的来源。",
    sourceIds: ["INFO-2107", "INFO-4402", "LOG-1819"],
    confidence: 0.72,
    selected: true,
    status: "pending",
  },
  {
    id: "PROP-NODE-02",
    targetId: "conclusion-identity",
    targetType: "node",
    action: "create",
    label: "补充身份结论",
    description: "第五人曾属于维护组，而不是系统生成的权限幽灵。",
    rationale: "访问日志使用内部凭据，且舱外脚印无法由纯系统实体产生。",
    sourceIds: ["INFO-4402", "LOG-1819"],
    confidence: 0.68,
    selected: false,
    status: "pending",
  },
  {
    id: "PROP-NODE-03",
    targetId: "hyp-active-leak",
    targetType: "node",
    action: "create",
    label: "新增泄露假设",
    description: "有人在保护协议生效前主动扩大了权限记录的可见范围。",
    rationale: "林望的知识状态早于记录允许的获得时间，且存在内部凭据访问。",
    sourceIds: ["EVL-1823", "INFO-2107", "LOG-1819"],
    confidence: 0.79,
    selected: true,
    status: "pending",
  },
  {
    id: "PROP-NODE-04",
    targetId: "conclusion-leak",
    targetType: "node",
    action: "create",
    label: "补充设计结论",
    description: "在谜底冻结前补上权限获得、隐藏与回收事件。",
    rationale: "当前链路有确定性知识状态缺口，且影响唯一根因的公平验证。",
    sourceIds: ["EVL-1823", "INFO-2107", "PHASE-03"],
    confidence: 0.83,
    selected: true,
    status: "pending",
  },
  {
    id: "PROP-EDGE-01",
    targetId: "edge-identity-2",
    targetType: "edge",
    action: "connect",
    label: "建立身份支持关系",
    description: "“第五人拥有内部访问能力”支持“被抹除的内部成员”。",
    rationale: "主张与假设共享权限记录和访问日志来源。",
    sourceIds: ["INFO-2107", "LOG-1819"],
    confidence: 0.72,
    selected: true,
    status: "pending",
  },
  {
    id: "PROP-EDGE-03",
    targetId: "edge-leak-2",
    targetType: "edge",
    action: "connect",
    label: "建立泄露支持关系",
    description: "提前读取受限记录支持主动信息泄露假设。",
    rationale: "知识获得顺序无法由当前事件链解释。",
    sourceIds: ["EVL-1823", "INFO-2107"],
    confidence: 0.79,
    selected: true,
    status: "pending",
  },
];

const organizeProposals: ReasoningProposalChange[] = [
  {
    id: "PROP-ORG-01",
    targetId: "edge-root-2",
    targetType: "edge",
    action: "classify",
    label: "归类为解释关系",
    description: "将保护协议主张与保护假设归入同一主路径。",
    rationale: "两个已有对象共享保护协议和状态回滚来源。",
    sourceIds: ["EVL-1823", "EVL-1825"],
    confidence: 0.86,
    selected: true,
    status: "pending",
  },
  {
    id: "PROP-ORG-02",
    targetId: "edge-auto-3",
    targetType: "edge",
    action: "classify",
    label: "归类为反驳关系",
    description: "访问日志与协议时间顺序反驳系统自动泄露。",
    rationale: "内部凭据访问发生在系统公开动作之前。",
    sourceIds: ["LOG-1819", "EVL-1823"],
    confidence: 0.91,
    selected: true,
    status: "pending",
  },
];

export function createDefaultReasoningState(
  draftRevision: number,
): PrototypeReasoningState {
  return {
    status: "idle",
    mode: "organize",
    view: "overview",
    baseRevision: draftRevision,
    progress: 0,
    stage: "等待手动生成",
    activePathId: "",
    selectedNodeId: "",
    selectedProposalId: "",
    paths: [],
    nodes: [],
    edges: [],
    proposals: [],
    positions: {},
    expandedBundleIds: [],
    runs: [],
    runSequence: 0,
    failureMessage: "",
  };
}

export function buildReasoningFixture(
  mode: ReasoningMode,
): Pick<
  PrototypeReasoningState,
  "paths" | "nodes" | "edges" | "proposals"
> {
  const exploreProposalIds = new Set(
    exploreProposals.map((proposal) => proposal.id),
  );
  const nodes =
    mode === "explore"
      ? baseNodes
      : baseNodes.filter(
          (node) => !node.proposalId || !exploreProposalIds.has(node.proposalId),
        );
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges =
    mode === "explore"
      ? baseEdges
      : baseEdges.filter(
          (edge) =>
            nodeIds.has(edge.source) &&
            nodeIds.has(edge.target) &&
            (!edge.proposalId || !exploreProposalIds.has(edge.proposalId)),
        );

  return {
    paths: basePaths.map((path) => ({
      ...path,
      nodeIds: path.nodeIds.filter((nodeId) => nodeIds.has(nodeId)),
      sharedSourceIds: [...path.sharedSourceIds],
    })),
    nodes: nodes.map((node) => ({
      ...node,
      sourceIds: [...node.sourceIds],
      tags: [...node.tags],
    })),
    edges: edges.map((edge) => ({ ...edge })),
    proposals: (mode === "explore"
      ? exploreProposals
      : organizeProposals
    ).map((proposal) => ({
      ...proposal,
      sourceIds: [...proposal.sourceIds],
    })),
  };
}

export function getReasoningSource(
  sourceId: string,
): ReasoningSourceObject | undefined {
  return reasoningSourceCatalog.find((source) => source.id === sourceId);
}

export function getReasoningOverviewMetrics(
  state: PrototypeReasoningState,
) {
  const uniqueSources = new Set(
    state.nodes.flatMap((node) => node.sourceIds),
  ).size;
  const potentialSources = reasoningSourceCatalog.length;
  return {
    questions: state.paths.length,
    paths: state.paths.length,
    gaps: state.nodes.filter((node) => node.kind === "gap").length,
    conflicts: state.paths.reduce(
      (sum, path) => sum + path.conflictCount,
      0,
    ),
    sourceCoverage:
      potentialSources === 0
        ? 0
        : Math.round((uniqueSources / potentialSources) * 100),
  };
}

export function getPendingReasoningChanges(
  state: PrototypeReasoningState,
): ReasoningProposalChange[] {
  return state.proposals.filter((proposal) => proposal.status === "pending");
}

export function getSelectedReasoningChanges(
  state: PrototypeReasoningState,
): ReasoningProposalChange[] {
  return getPendingReasoningChanges(state).filter(
    (proposal) => proposal.selected,
  );
}
