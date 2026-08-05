export type PrototypeStep =
  | "idea"
  | "questions"
  | "confirmation"
  | "review"
  | "candidates";

export type PrototypePolishMode =
  | "proofread"
  | "rewrite"
  | "narrative_enhance";

export type PrototypeResolutionMode =
  | "author_anchored"
  | "agent_proposed"
  | "open";

export type PrototypeFieldSource =
  | "user_original"
  | "user_confirmed"
  | "agent_suggestion"
  | "unresolved";

export type PrototypeConstraintStrength = "hard" | "soft";

export interface PrototypeQuestion {
  key: string;
  ordinal: number;
  prompt: string;
  impact: string;
  required: boolean;
  suggestions: string[];
}

export interface PrototypeAnswer {
  text: string;
  source: PrototypeFieldSource;
  pending: boolean;
}

export interface PrototypeConstraint {
  key: string;
  label: string;
  hint: string;
  placeholder: string;
  statement: string;
  strength: PrototypeConstraintStrength;
}

export interface PrototypeBrief {
  concept: string;
  sellingPoints: string;
  outline: string;
  reasoningGoal: string;
  resolutionMode: PrototypeResolutionMode;
  authorAnswer: string;
  scopeEstimate: string;
  riskNotes: string;
  constraints: PrototypeConstraint[];
  sources: {
    concept: PrototypeFieldSource;
    sellingPoints: PrototypeFieldSource;
    outline: PrototypeFieldSource;
    reasoningGoal: PrototypeFieldSource;
    resolutionMode: PrototypeFieldSource;
    authorAnswer: PrototypeFieldSource;
    scopeEstimate: PrototypeFieldSource;
    riskNotes: PrototypeFieldSource;
  };
}

export interface PrototypeCandidate {
  id: number;
  label: string;
  origin: "agent" | "manual" | "dialogue";
  createdAt: string;
  bookmarked: boolean;
  brief: PrototypeBrief;
}

export type PrototypeAtomicOrigin = "agent" | "manual" | "saved";

export interface PrototypeAnchorReview {
  id: string;
  statement: string;
  origin: PrototypeAtomicOrigin;
}

export interface PrototypeConstraintReview {
  id: string;
  statement: string;
  strength: PrototypeConstraintStrength;
  origin: PrototypeAtomicOrigin;
}

export interface PrototypeBriefReview {
  creativeIntent: string;
  reasoningProposition: string;
  resolutionMode: PrototypeResolutionMode;
  authorAnswer: string;
  boundaryText: string;
  authorAnchors: PrototypeAnchorReview[];
  creativeConstraints: PrototypeConstraintReview[];
  pendingDecisions: string[];
  dirty: boolean;
  saved: boolean;
}

export const prototypeSteps: Array<{
  id: PrototypeStep;
  no: string;
  label: string;
  shortLabel: string;
}> = [
  { id: "idea", no: "01", label: "最初想法", shortLabel: "输入" },
  { id: "questions", no: "02", label: "关键追问", shortLabel: "核验" },
  { id: "confirmation", no: "03", label: "创作简报成案", shortLabel: "成案" },
  { id: "review", no: "04", label: "创作简报审阅", shortLabel: "冻结" },
  { id: "candidates", no: "05", label: "候选工作稿", shortLabel: "决策" },
];

export const intakeRoutes = [
  {
    code: "A",
    label: "我有一个想法",
    summary: "把已有灵感整理成创作简报",
    state: "available",
  },
  {
    code: "B",
    label: "帮我想一个",
    summary: "根据偏好生成多个创意方向",
    state: "planned",
  },
  {
    code: "C",
    label: "我有已有内容",
    summary: "从现成素材中提取起案信息",
    state: "planned",
  },
  {
    code: "D",
    label: "我已经准备好",
    summary: "从模板或空白结构直接开始",
    state: "planned",
  },
] as const;

export const polishModes: Array<{
  value: PrototypePolishMode;
  label: string;
  hint: string;
}> = [
  {
    value: "proofread",
    label: "轻度校对",
    hint: "只处理错字、病句与标点",
  },
  {
    value: "rewrite",
    label: "表达优化",
    hint: "改善措辞、语序与节奏",
  },
  {
    value: "narrative_enhance",
    label: "叙事增强",
    hint: "增强画面感，新增细节单独披露",
  },
];

export const prototypeQuestions: PrototypeQuestion[] = [
  {
    key: "reasoning_goal",
    ordinal: 1,
    prompt: "玩家最终必须回答哪一个问题？",
    impact: "这个答案会决定线索如何组织，也会成为后续验证的核心命题。",
    required: true,
    suggestions: [
      "找出是谁伪造了那段不存在的时间。",
      "判断三份可靠记录为什么会同时说谎。",
    ],
  },
  {
    key: "experience_scale",
    ordinal: 2,
    prompt: "你希望它是一晚完成的小案，还是可以持续扩展的长案？",
    impact: "这会影响角色数量、场景密度与建议体验时长。",
    required: false,
    suggestions: ["一晚完成，控制在 60–90 分钟。", "可以扩成三幕长案。"],
  },
];

export const sampleIdea =
  "一名档案修复师发现，三份彼此独立且可靠的记录，都指向一段从未存在过的时间。她必须在记录被永久封存前，找出是谁改写了所有人的记忆。";

export const resolutionModes: Array<{
  value: PrototypeResolutionMode;
  label: string;
  hint: string;
}> = [
  {
    value: "author_anchored",
    label: "按作者底牌展开",
    hint: "答案已经确定，后续围绕它铺设证据。",
  },
  {
    value: "agent_proposed",
    label: "由 Agent 提出候选结论",
    hint: "先形成多个可验证答案，再由你采用。",
  },
  {
    value: "open",
    label: "保持开放",
    hint: "暂时不锁定真相，只确认调查问题。",
  },
];

export const fieldSourceLabels: Record<PrototypeFieldSource, string> = {
  user_original: "你的原文",
  user_confirmed: "由你确认",
  agent_suggestion: "Agent 建议",
  unresolved: "尚未决定",
};

export const candidateOriginLabels: Record<
  PrototypeCandidate["origin"],
  string
> = {
  agent: "Agent 初稿",
  manual: "表单修改",
  dialogue: "对话修改",
};

export function createConstraints(): PrototypeConstraint[] {
  return [
    {
      key: "must_keep",
      label: "必须保留",
      hint: "不可被后续候选改写的事实或意图",
      placeholder: "例如：三份记录都可靠这一前提不能推翻。",
      statement: "",
      strength: "hard",
    },
    {
      key: "must_avoid",
      label: "禁止出现",
      hint: "题材、机制或表达禁区",
      placeholder: "例如：不使用超自然解释。",
      statement: "",
      strength: "hard",
    },
    {
      key: "scope",
      label: "规模",
      hint: "场景与案件体量",
      placeholder: "例如：不超过 8 个场景。",
      statement: "",
      strength: "soft",
    },
    {
      key: "cast",
      label: "人数",
      hint: "核心角色与可控配角数量",
      placeholder: "例如：核心角色不超过 4 人。",
      statement: "",
      strength: "soft",
    },
    {
      key: "duration",
      label: "时长",
      hint: "体验、阅读或游玩时长",
      placeholder: "例如：单次体验控制在 60–90 分钟。",
      statement: "",
      strength: "soft",
    },
    {
      key: "content_scale",
      label: "内容尺度",
      hint: "强度、年龄或敏感内容边界",
      placeholder: "例如：适合 12 岁以上，不出现肢解描写。",
      statement: "",
      strength: "hard",
    },
  ];
}

export function createEmptyBrief(sourceText: string): PrototypeBrief {
  const concept = firstMeaningfulLine(sourceText);
  return {
    concept,
    sellingPoints: "",
    outline: "",
    reasoningGoal: "",
    resolutionMode: "agent_proposed",
    authorAnswer: "",
    scopeEstimate: "",
    riskNotes: "",
    constraints: createConstraints(),
    sources: {
      concept: concept ? "user_original" : "unresolved",
      sellingPoints: "unresolved",
      outline: "unresolved",
      reasoningGoal: "unresolved",
      resolutionMode: "user_confirmed",
      authorAnswer: "unresolved",
      scopeEstimate: "unresolved",
      riskNotes: "unresolved",
    },
  };
}

export function synthesizeBrief(
  sourceText: string,
  answers: Record<string, PrototypeAnswer>,
): PrototypeBrief {
  const reasoningAnswer = answers.reasoning_goal?.text.trim();
  const scaleAnswer = answers.experience_scale?.pending
    ? "待后续决定"
    : answers.experience_scale?.text.trim();
  return {
    concept: firstMeaningfulLine(sourceText),
    sellingPoints:
      "相互印证却共同失真的档案\n可追溯的记忆改写线索\n封存倒计时带来的选择压力",
    outline:
      "发现不存在的时间段\n比对三份独立记录\n追查共同的改写入口\n在封存前决定公开哪一版真相",
    reasoningGoal:
      reasoningAnswer || "找出是谁制造了那段不存在的时间，以及这样做的目的。",
    resolutionMode: "agent_proposed",
    authorAnswer: "",
    scopeEstimate: scaleAnswer || "4 名核心角色 / 7 个场景 / 90 分钟",
    riskNotes: "需要避免让记忆改写成为无法验证的万能解释。",
    constraints: createConstraints(),
    sources: {
      concept: "user_original",
      sellingPoints: "agent_suggestion",
      outline: "agent_suggestion",
      reasoningGoal: reasoningAnswer ? "user_confirmed" : "agent_suggestion",
      resolutionMode: "user_confirmed",
      authorAnswer: "unresolved",
      scopeEstimate: scaleAnswer ? "user_confirmed" : "agent_suggestion",
      riskNotes: "agent_suggestion",
    },
  };
}

export function polishIdea(
  sourceText: string,
  mode: PrototypePolishMode,
): { text: string; notes: string[]; introducedDetails: string[] } {
  const normalized = sourceText.trim();
  if (mode === "proofread") {
    return {
      text: normalized.replace(/[。！？]?$/u, "。"),
      notes: ["统一了句末标点，未改变情节与事实。"],
      introducedDetails: [],
    };
  }
  if (mode === "rewrite") {
    return {
      text:
        normalized.replace("发现，", "意外发现：").replace("必须", "需要") +
        (normalized.endsWith("。") ? "" : "。"),
      notes: ["调整了语序，让核心异常更早出现。", "保留原有角色、目标与因果。"],
      introducedDetails: [],
    };
  }
  return {
    text:
      normalized
        .replace(
          "一名档案修复师发现，",
          "深夜的修复室里，一名档案修复师发现：",
        )
        .replace(
          "她必须在记录被永久封存前，",
          "记录永久封存前，她只剩一次机会",
        ) +
      (normalized.endsWith("。") ? "" : "。"),
    notes: ["强化了时间压力和开场画面。", "没有改变三份记录可靠这一前提。"],
    introducedDetails: ["新增“深夜修复档案”的时间氛围。", "将封存压力表达为一次机会。"],
  };
}

export function cloneBrief(brief: PrototypeBrief): PrototypeBrief {
  return structuredClone(brief);
}

function splitAtomicStatements(value: string) {
  return value
    .split(/(?:\r?\n|[。；;]+)/u)
    .map((statement) => statement.trim())
    .filter(Boolean);
}

export function extractAuthorAnchors(
  authorAnswer: string,
): PrototypeAnchorReview[] {
  return splitAtomicStatements(authorAnswer).map((statement, index) => ({
    id: `anchor-agent-${index + 1}`,
    statement,
    origin: "agent",
  }));
}

export function extractCreativeConstraints(
  boundaryText: string,
  briefConstraints: PrototypeConstraint[] = [],
): PrototypeConstraintReview[] {
  const savedRows = briefConstraints
    .filter((constraint) => constraint.statement.trim())
    .map((constraint) => ({
      id: `constraint-${constraint.key}`,
      statement: constraint.statement.trim(),
      strength: constraint.strength,
      origin: "agent" as const,
    }));
  if (savedRows.length) return savedRows;

  return splitAtomicStatements(boundaryText).map((statement, index) => ({
    id: `constraint-agent-${index + 1}`,
    statement,
    strength: "hard",
    origin: "agent",
  }));
}

export function createBriefReview(
  brief: PrototypeBrief,
  answers: Record<string, PrototypeAnswer>,
): PrototypeBriefReview {
  const boundaryText = brief.constraints
    .map((constraint) => constraint.statement.trim())
    .filter(Boolean)
    .join("\n");
  const pendingDecisions = prototypeQuestions
    .filter((question) => answers[question.key]?.pending)
    .map((question) => question.prompt);

  return {
    creativeIntent: brief.concept,
    reasoningProposition: brief.reasoningGoal,
    resolutionMode: brief.resolutionMode,
    authorAnswer:
      brief.resolutionMode === "author_anchored" ? brief.authorAnswer : "",
    boundaryText,
    authorAnchors:
      brief.resolutionMode === "author_anchored"
        ? extractAuthorAnchors(brief.authorAnswer)
        : [],
    creativeConstraints: extractCreativeConstraints(
      boundaryText,
      brief.constraints,
    ),
    pendingDecisions,
    dirty: false,
    saved: false,
  };
}

export function reviewFieldBlockers(review: PrototypeBriefReview): string[] {
  const blockers: string[] = [];
  if (!review.creativeIntent.trim()) blockers.push("创作意图");
  if (!review.reasoningProposition.trim()) blockers.push("核心推理命题");
  if (
    review.resolutionMode === "author_anchored" &&
    !review.authorAnswer.trim()
  ) {
    blockers.push("作者底牌");
  }
  return blockers;
}

export function atomicReviewComplete(review: PrototypeBriefReview) {
  const anchorsComplete =
    review.resolutionMode !== "author_anchored" ||
    (!review.authorAnswer.trim() && review.authorAnchors.length === 0) ||
    review.authorAnchors.some((anchor) => anchor.statement.trim());
  const constraintsComplete =
    !review.boundaryText.trim() ||
    review.creativeConstraints.some((constraint) =>
      Boolean(constraint.statement.trim()),
    );
  return anchorsComplete && constraintsComplete;
}

export function canFreezeBriefReview(review: PrototypeBriefReview) {
  return (
    review.saved &&
    !review.dirty &&
    reviewFieldBlockers(review).length === 0 &&
    atomicReviewComplete(review)
  );
}

export function mergeReviewIntoBrief(
  brief: PrototypeBrief,
  review: PrototypeBriefReview,
): PrototypeBrief {
  const reviewedConstraints = new Map(
    review.creativeConstraints.map((constraint) => [constraint.id, constraint]),
  );
  return {
    ...cloneBrief(brief),
    concept: review.creativeIntent,
    reasoningGoal: review.reasoningProposition,
    resolutionMode: review.resolutionMode,
    authorAnswer:
      review.resolutionMode === "author_anchored"
        ? review.authorAnswer
        : "",
    constraints: brief.constraints.map((constraint) => {
      const reviewed = reviewedConstraints.get(`constraint-${constraint.key}`);
      return reviewed
        ? {
            ...constraint,
            statement: reviewed.statement,
            strength: reviewed.strength,
          }
        : constraint;
    }),
    sources: {
      ...brief.sources,
      concept: "user_confirmed",
      reasoningGoal: "user_confirmed",
      resolutionMode: "user_confirmed",
      authorAnswer:
        review.resolutionMode === "author_anchored"
          ? "user_confirmed"
          : "unresolved",
    },
  };
}

export function missingHardFields(brief: PrototypeBrief): string[] {
  const missing: string[] = [];
  if (!brief.concept.trim()) missing.push("一句话概念");
  if (!brief.reasoningGoal.trim()) missing.push("推理目标");
  if (brief.resolutionMode === "author_anchored" && !brief.authorAnswer.trim()) {
    missing.push("作者底牌");
  }
  return missing;
}

export function firstMeaningfulLine(value: string): string {
  return (
    value
      .split(/\r?\n/u)
      .map((line) => line.trim())
      .find(Boolean)
      ?.slice(0, 1000) ?? ""
  );
}
