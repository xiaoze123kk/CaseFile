export type IntakeStep =
  | "idea"
  | "questions"
  | "confirmation"
  | "review"
  | "candidates";

export type IntakePolishMode =
  | "proofread"
  | "rewrite"
  | "narrative_enhance";

export type ResolutionMode =
  | "author_anchored"
  | "agent_proposed"
  | "open";

export type ConclusionMode =
  | "unique"
  | "finite_multiple"
  | "optimal"
  | "probabilistic"
  | "open_interpretation"
  | "multiple_endings"
  | "undetermined";

export type FieldSource =
  | "user_original"
  | "user_confirmed"
  | "agent_suggestion"
  | "unresolved";

export type ConstraintStrength = "hard" | "soft";

export interface IntakeQuestion {
  key: string;
  ordinal: number;
  prompt: string;
  impact: string;
  required: boolean;
  suggestions: string[];
}

export interface IntakeAnswer {
  text: string;
  source: FieldSource;
  pending: boolean;
}

export interface IntakeConstraint {
  key: string;
  label: string;
  hint: string;
  placeholder: string;
  statement: string;
  strength: ConstraintStrength;
}

export interface IntakeBrief {
  concept: string;
  sellingPoints: string;
  outline: string;
  reasoningGoal: string;
  resolutionMode: ResolutionMode;
  conclusionMode: ConclusionMode;
  authorAnswer: string;
  scopeEstimate: string;
  riskNotes: string;
  constraints: IntakeConstraint[];
  sources: {
    concept: FieldSource;
    sellingPoints: FieldSource;
    outline: FieldSource;
    reasoningGoal: FieldSource;
    resolutionMode: FieldSource;
    conclusionMode: FieldSource;
    authorAnswer: FieldSource;
    scopeEstimate: FieldSource;
    riskNotes: FieldSource;
  };
}

export interface BriefCandidate {
  id: number;
  label: string;
  origin: "agent" | "manual" | "dialogue";
  createdAt: string;
  bookmarked: boolean;
  brief: IntakeBrief;
}

export function candidateHistoryVersions(
  candidates: readonly Pick<BriefCandidate, "id">[],
): ReadonlyMap<number, number> {
  return new Map(
    [...candidates]
      .sort((first, second) => first.id - second.id)
      .map((candidate, index) => [candidate.id, index + 1]),
  );
}

export type AtomicOrigin = "agent" | "manual" | "saved";

export interface AnchorReview {
  id: string;
  statement: string;
  origin: AtomicOrigin;
}

export interface ConstraintReview {
  id: string;
  statement: string;
  strength: ConstraintStrength;
  origin: AtomicOrigin;
}

export interface BriefReview {
  creativeIntent: string;
  reasoningProposition: string;
  resolutionMode: ResolutionMode;
  conclusionMode: ConclusionMode;
  authorAnswer: string;
  boundaryText: string;
  authorAnchors: AnchorReview[];
  creativeConstraints: ConstraintReview[];
  pendingDecisions: string[];
  dirty: boolean;
  saved: boolean;
}

export const intakeSteps: Array<{
  id: IntakeStep;
  no: string;
  label: string;
  shortLabel: string;
}> = [
  { id: "idea", no: "01", label: "最初想法", shortLabel: "输入" },
  { id: "questions", no: "02", label: "关键追问", shortLabel: "研查" },
  { id: "confirmation", no: "03", label: "创作简报草案", shortLabel: "成案" },
  { id: "review", no: "04", label: "创作简报审阅", shortLabel: "活化" },
  { id: "candidates", no: "05", label: "触达工作格", shortLabel: "决策" },
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
  value: IntakePolishMode;
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

export const defaultQuestions: IntakeQuestion[] = [
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
  value: ResolutionMode;
  label: string;
  hint: string;
  effectTiming: string;
  effectTitle: string;
  effectDetail: string;
}> = [
  {
    value: "author_anchored",
    label: "使用我提供的答案",
    hint: "现在填写答案；深稿只围绕它铺设证据。",
    effectTiming: "现在与深稿生成时生效",
    effectTitle: "你先锁定答案，Agent 只负责展开",
    effectDetail: "答案会被固定。Agent 可以补全证据与推理链，但不能改写、弱化或替换答案。",
  },
  {
    value: "agent_proposed",
    label: "让 Agent 在深稿中拟定",
    hint: "现在不生成；冻结简报后补全一套可验证答案。",
    effectTiming: "冻结简报后生效",
    effectTitle: "Agent 会随深稿拟定答案",
    effectDetail: "这里不会立即出现候选。生成深稿时，Agent 会补全一套答案、证据与推理链；你最终审阅并采用的是整份深稿。",
  },
  {
    value: "open",
    label: "暂不设定答案",
    hint: "深稿保留多种解释，不收束为唯一真相。",
    effectTiming: "深稿生成时生效",
    effectTitle: "Agent 不会替你暗中锁定真相",
    effectDetail: "深稿仍会组织线索与推理路径，但必须真实保留答案开放性，不能制造唯一结论。",
  },
];

export const conclusionModes: Array<{
  value: ConclusionMode;
  label: string;
  hint: string;
}> = [
  {
    value: "unique",
    label: "唯一解",
    hint: "需要证明信息充分、公平，且不存在同样成立的第二种解释。",
  },
  {
    value: "finite_multiple",
    label: "有限多解",
    hint: "列出可成立的解，以及每个解成立所需的条件。",
  },
  {
    value: "optimal",
    label: "最优方案",
    hint: "比较可行方案，并说明选定方案为何更优。",
  },
  {
    value: "probabilistic",
    label: "概率排序",
    hint: "按证据支持度排列结论，不伪装成确定答案。",
  },
  {
    value: "open_interpretation",
    label: "开放解释",
    hint: "允许多种解释，但每种解释都必须有可追溯依据。",
  },
  {
    value: "multiple_endings",
    label: "多结局",
    hint: "保留多个结局，并明确各自触发条件。",
  },
  {
    value: "undetermined",
    label: "信息不足时保持未决",
    hint: "当前证据不足时保持未决，不强行制造唯一答案。",
  },
];

export const fieldSourceLabels: Record<FieldSource, string> = {
  user_original: "你的原文",
  user_confirmed: "由你确认",
  agent_suggestion: "Agent 建议",
  unresolved: "尚未决定",
};

export const candidateOriginLabels: Record<
  BriefCandidate["origin"],
  string
> = {
  agent: "Agent 初稿",
  manual: "表单修改",
  dialogue: "对话修改",
};

export function createConstraints(): IntakeConstraint[] {
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

export function createEmptyBrief(sourceText: string): IntakeBrief {
  const concept = firstMeaningfulLine(sourceText);
  return {
    concept,
    sellingPoints: "",
    outline: "",
    reasoningGoal: "",
    resolutionMode: "agent_proposed",
    conclusionMode: "undetermined",
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
      conclusionMode: "unresolved",
      authorAnswer: "unresolved",
      scopeEstimate: "unresolved",
      riskNotes: "unresolved",
    },
  };
}

export function synthesizeBrief(
  sourceText: string,
  answers: Record<string, IntakeAnswer>,
): IntakeBrief {
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
    conclusionMode: "undetermined",
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
      conclusionMode: "agent_suggestion",
      authorAnswer: "unresolved",
      scopeEstimate: scaleAnswer ? "user_confirmed" : "agent_suggestion",
      riskNotes: "agent_suggestion",
    },
  };
}

export function polishIdea(
  sourceText: string,
  mode: IntakePolishMode,
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

export function cloneBrief(brief: IntakeBrief): IntakeBrief {
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
): AnchorReview[] {
  return splitAtomicStatements(authorAnswer).map((statement, index) => ({
    id: `anchor-agent-${index + 1}`,
    statement,
    origin: "agent",
  }));
}

export function extractCreativeConstraints(
  boundaryText: string,
  briefConstraints: IntakeConstraint[] = [],
): ConstraintReview[] {
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
  brief: IntakeBrief,
  answers: Record<string, IntakeAnswer>,
): BriefReview {
  const boundaryText = brief.constraints
    .map((constraint) => constraint.statement.trim())
    .filter(Boolean)
    .join("\n");
  const pendingDecisions = defaultQuestions
    .filter((question) => answers[question.key]?.pending)
    .map((question) => question.prompt);

  return {
    creativeIntent: brief.concept,
    reasoningProposition: brief.reasoningGoal,
    resolutionMode: brief.resolutionMode,
    conclusionMode: brief.conclusionMode,
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

export function reviewFieldBlockers(review: BriefReview): string[] {
  const blockers: string[] = [];
  if (!review.creativeIntent.trim()) blockers.push("创作意图");
  if (!review.reasoningProposition.trim()) blockers.push("核心推理命题");
  if (
    review.resolutionMode === "author_anchored" &&
    !review.authorAnswer.trim()
  ) {
    blockers.push("作者答案");
  }
  return blockers;
}

export function atomicReviewComplete(review: BriefReview) {
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

export function canFreezeBriefReview(review: BriefReview) {
  return (
    review.saved &&
    !review.dirty &&
    reviewFieldBlockers(review).length === 0 &&
    atomicReviewComplete(review)
  );
}

export function mergeReviewIntoBrief(
  brief: IntakeBrief,
  review: BriefReview,
): IntakeBrief {
  const reviewedConstraints = new Map(
    review.creativeConstraints.map((constraint) => [constraint.id, constraint]),
  );
  return {
    ...cloneBrief(brief),
    concept: review.creativeIntent,
    reasoningGoal: review.reasoningProposition,
    resolutionMode: review.resolutionMode,
    conclusionMode: review.conclusionMode,
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

export function missingHardFields(brief: IntakeBrief): string[] {
  const missing: string[] = [];
  if (!brief.concept.trim()) missing.push("一句话概念");
  if (!brief.reasoningGoal.trim()) missing.push("推理目标");
  if (brief.sources.conclusionMode !== "user_confirmed") missing.push("结论模式");
  if (brief.resolutionMode === "author_anchored" && !brief.authorAnswer.trim()) {
    missing.push("作者答案");
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
