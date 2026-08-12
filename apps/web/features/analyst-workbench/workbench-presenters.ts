import type { ReasoningOutcome } from "./analyst-fixture";

const objectSubtypeLabels: Record<string, string> = {
  accepted: "已采纳",
  active: "核对中",
  approximate: "约略时间",
  canon_true: "既定事实",
  dialogue: "对话",
  disputed: "有争议",
  document: "文档",
  day: "精确到日",
  entity: "实体",
  eliminated: "已排除",
  environment: "环境信息",
  evidence: "证据",
  false_belief: "错误认知",
  faction: "阵营",
  feedback: "反馈",
  information: "信息",
  information_unit: "信息",
  location: "地点",
  hour: "精确到小时",
  minute: "精确到分钟",
  object: "物件",
  observation: "观察",
  organization: "组织",
  other: "其他",
  person: "人物",
  rejected: "已拒绝",
  reported: "转述事实",
  rule: "规则",
  rule_actor: "规则角色",
  schematic: "示意位置",
  second: "精确到秒",
  supported: "已支持",
  system: "系统",
  system_log: "系统日志",
  topology: "拓扑位置",
  undetermined: "待判定",
  unknown: "未标注",
  wgs84: "地理坐标",
};

const reliabilityLabels: Record<string, string> = {
  high: "高",
  low: "低",
  medium: "中等",
  unknown: "未标注",
};

const classificationLabels: Record<string, string> = {
  background: "背景信息",
  distractor: "干扰项",
  incomplete: "信息不完整",
  key: "关键线索",
  misleading: "误导信息",
  supporting: "支持信息",
};

const confirmationStatusLabels: Record<string, string> = {
  ai_inferred: "AI 推定，待核对",
  unresolved: "待确认",
  user_confirmed: "作者已确认",
};

const objectTypeLabels: Record<string, string> = {
  casefile: "卷宗",
  claim: "论断",
  constraint: "约束",
  entity: "实体",
  event: "事件",
  hypothesis: "假设",
  information: "信息",
  information_unit: "信息",
  location: "地点",
  reasoning_path: "推理路径",
  relationship: "关系",
  resolution_spec: "目标问题",
  source_fragment: "来源片段",
  structure_lock: "结构锁",
};

const creatorTextTypeLabels: Record<string, string> = {
  casefile: "卷宗",
  claim: "论断",
  constraint: "约束",
  entity: "实体",
  event: "事件",
  hypothesis: "假设",
  information: "信息",
  information_unit: "信息",
  location: "地点",
  reasoning_path: "推理路径",
  relationship: "关系",
  resolution_spec: "目标问题",
  structure_lock: "结构锁",
  source_fragment: "来源片段",
  unknown: "对象",
};

export interface CaseWallClock {
  date: string;
  fractionalSeconds: string;
  seconds: string;
  time: string;
  zoneSuffix: string;
}

const wallClockPattern = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/;

export function objectSubtypeLabel(value: string): string {
  return objectSubtypeLabels[value] ?? "其他";
}

export function objectTypeLabel(value: string): string {
  return objectTypeLabels[value] ?? "其他对象";
}

function chineseExcerpt(value: string | null | undefined): string {
  const text = value?.trim() ?? "";
  if (!/[\u3400-\u9fff]/u.test(text)) return "";
  return (text.match(/^[^。！？!?]*[。！？!?]?/u)?.[0] ?? text)
    .replace(/^[\s、，：:；;“”‘’（）()《》<>-]+|[\s、，：:；;“”‘’（）()《》<>-]+$/g, "")
    .slice(0, 80)
    .trim();
}

export function creatorText(value: string | null | undefined, fallback: string): string {
  const text = value?.trim() ?? "";
  if (!text) return fallback;
  return /[\u3400-\u9fff]/u.test(text) ? text : fallback;
}

export function creatorLabel(
  value: string | null | undefined,
  options: { kind: string; index: number; description?: string | null },
): string {
  const typeLabel = creatorTextTypeLabels[options.kind] ?? "对象";
  const fallback = `${typeLabel} ${options.index + 1}（标题待补充）`;
  const title = chineseExcerpt(value);
  if (title) return title;
  const fromDescription = chineseExcerpt(options.description);
  return fromDescription || fallback;
}

export function creatorDescription(
  value: string | null | undefined,
  kind: string,
): string {
  const typeLabel = creatorTextTypeLabels[kind] ?? "对象";
  return creatorText(value, `该${typeLabel}的创作说明待补充。`);
}

export function reliabilityLabel(value: string): string {
  return reliabilityLabels[value] ?? "未标注";
}

export function classificationLabel(value: string): string {
  return classificationLabels[value] ?? "其他信息";
}

export function confirmationStatusLabel(value: string): string {
  return confirmationStatusLabels[value] ?? "待确认";
}

export function confidenceLabel(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "置信度未标注"
    : `置信度 ${Math.round(value * 100)}%`;
}

export function parseCaseWallClock(value: string): CaseWallClock | null {
  const match = wallClockPattern.exec(value);
  if (!match) return null;
  return {
    date: match[1],
    fractionalSeconds: match[4] ?? "",
    time: match[2],
    seconds: match[3] ?? "00",
    zoneSuffix: match[5] ?? "",
  };
}

export function formatCaseWallClock(value: string): string {
  const parsed = parseCaseWallClock(value);
  if (!parsed) return value === "unknown" ? "时间未定" : "时间待核对";
  const [year, month, day] = parsed.date.split("-").map(Number);
  return `${year}年${month}月${day}日 ${parsed.time}`;
}

export function formatCaseClock(value: string): string {
  return parseCaseWallClock(value)?.time ?? (value === "unknown" ? "—" : "待核对");
}

export function serializeCaseWallClock(
  date: string,
  time: string,
  originalValue: string,
): string | null {
  const original = parseCaseWallClock(originalValue);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) {
    return null;
  }
  return `${date}T${time}:${original?.seconds ?? "00"}${original?.fractionalSeconds ?? ""}${original?.zoneSuffix ?? "Z"}`;
}

export const reasoningOutcomeLabels: Record<ReasoningOutcome, string> = {
  supported: "证据支持",
  contested: "解释竞争",
  eliminated: "已排除",
};

type ReasoningOperation =
  | "infer"
  | "compare"
  | "eliminate"
  | "combine"
  | "calculate"
  | "verify_rule";

export const reasoningOperationLabels: Record<ReasoningOperation, string> = {
  infer: "推断",
  compare: "比较",
  eliminate: "排除",
  combine: "合并",
  calculate: "计算",
  verify_rule: "验证规则",
};

export function reasoningOperationLabel(operation: string): string {
  return (
    reasoningOperationLabels[operation as ReasoningOperation] ?? "其他推理操作"
  );
}
