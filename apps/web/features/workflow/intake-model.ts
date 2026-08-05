import type {
  BriefIntakeCandidateContent,
  BriefIntakeCandidateView,
  BriefIntakeConstraintCategory,
  BriefIntakeFieldSource,
  BriefIntakeQuestionView,
  PolishMode,
  ResolutionMode,
} from "@/lib/api-client";

export const polishModes: Array<{
  value: PolishMode;
  label: string;
  hint: string;
}> = [
  { value: "proofread", label: "轻度校对", hint: "只改错字、病句和标点" },
  { value: "rewrite", label: "表达优化", hint: "调整措辞、语序和节奏" },
  {
    value: "narrative_enhance",
    label: "叙事增强",
    hint: "增强画面感，新增细节会标记",
  },
];

export type TextDiffSegment = {
  type: "equal" | "insert" | "delete";
  text: string;
};

export type TextDiff = {
  segments: TextDiffSegment[];
  changeCount: number;
  insertedCharacters: number;
  deletedCharacters: number;
};

export function buildTextDiff(original: string, candidate: string): TextDiff {
  const before = Array.from(original);
  const after = Array.from(candidate);
  let prefixLength = 0;
  while (
    prefixLength < before.length &&
    prefixLength < after.length &&
    before[prefixLength] === after[prefixLength]
  ) {
    prefixLength += 1;
  }
  let suffixLength = 0;
  while (
    suffixLength < before.length - prefixLength &&
    suffixLength < after.length - prefixLength &&
    before[before.length - suffixLength - 1] ===
      after[after.length - suffixLength - 1]
  ) {
    suffixLength += 1;
  }

  const beforeMiddle = before.slice(
    prefixLength,
    before.length - suffixLength,
  );
  const afterMiddle = after.slice(prefixLength, after.length - suffixLength);
  const segments: TextDiffSegment[] = [];
  appendDiffSegment(segments, "equal", before.slice(0, prefixLength).join(""));

  if (beforeMiddle.length * afterMiddle.length > 250_000) {
    appendDiffSegment(segments, "delete", beforeMiddle.join(""));
    appendDiffSegment(segments, "insert", afterMiddle.join(""));
  } else {
    appendLcsDiff(segments, beforeMiddle, afterMiddle);
  }
  appendDiffSegment(
    segments,
    "equal",
    before.slice(before.length - suffixLength).join(""),
  );

  let changeCount = 0;
  let insideChange = false;
  let insertedCharacters = 0;
  let deletedCharacters = 0;
  for (const segment of segments) {
    if (segment.type === "equal") {
      insideChange = false;
      continue;
    }
    if (!insideChange) changeCount += 1;
    insideChange = true;
    if (segment.type === "insert") insertedCharacters += Array.from(segment.text).length;
    if (segment.type === "delete") deletedCharacters += Array.from(segment.text).length;
  }
  return { segments, changeCount, insertedCharacters, deletedCharacters };
}

function appendLcsDiff(
  segments: TextDiffSegment[],
  before: string[],
  after: string[],
) {
  const table = Array.from(
    { length: before.length + 1 },
    () => new Uint32Array(after.length + 1),
  );
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      table[left][right] =
        before[left] === after[right]
          ? table[left + 1][right + 1] + 1
          : Math.max(table[left + 1][right], table[left][right + 1]);
    }
  }
  let left = 0;
  let right = 0;
  while (left < before.length || right < after.length) {
    if (
      left < before.length &&
      right < after.length &&
      before[left] === after[right]
    ) {
      appendDiffSegment(segments, "equal", before[left]);
      left += 1;
      right += 1;
    } else if (
      right < after.length &&
      (left === before.length || table[left][right + 1] >= table[left + 1][right])
    ) {
      appendDiffSegment(segments, "insert", after[right]);
      right += 1;
    } else {
      appendDiffSegment(segments, "delete", before[left]);
      left += 1;
    }
  }
}

function appendDiffSegment(
  segments: TextDiffSegment[],
  type: TextDiffSegment["type"],
  text: string,
) {
  if (!text) return;
  const previous = segments.at(-1);
  if (previous?.type === type) previous.text += text;
  else segments.push({ type, text });
}

export const sourceLabels: Record<BriefIntakeFieldSource, string> = {
  user_original: "你的原文",
  user_confirmed: "由你确认",
  agent_suggestion: "Agent 建议",
  unresolved: "尚未决定",
};

export const sourceTones: Record<
  BriefIntakeFieldSource,
  "original" | "confirmed" | "agent" | "pending"
> = {
  user_original: "original",
  user_confirmed: "confirmed",
  agent_suggestion: "agent",
  unresolved: "pending",
};

export const resolutionModeLabels: Record<ResolutionMode, string> = {
  author_anchored: "按作者底牌展开",
  agent_proposed: "由 Agent 提出候选结论",
  open: "保持开放",
};

export const resolutionModeHints: Record<ResolutionMode, string> = {
  author_anchored: "例：已知幕后黑手。",
  agent_proposed: "例：先给候选答案。",
  open: "例：暂不锁定答案。",
};

export const candidateOriginLabels: Record<BriefIntakeCandidateView["origin"], string> = {
  agent_synthesis: "Agent 初稿",
  dialogue_revision: "对话修改",
  manual_edit: "表单修改",
  legacy_import: "旧稿恢复",
};

export const constraintCategories: Array<{
  value: Exclude<BriefIntakeConstraintCategory, "other">;
  label: string;
  hint: string;
  example: string;
}> = [
  {
    value: "must_keep",
    label: "必须保留",
    hint: "不可被候选改写的事实或意图",
    example: "例如：妹妹偷吃蛋糕这一事实不能改掉。",
  },
  {
    value: "must_avoid",
    label: "禁止出现",
    hint: "题材、机制或表达禁区",
    example: "例如：不加入超自然解释。",
  },
  {
    value: "scope",
    label: "规模",
    hint: "篇幅、场景或案件体量",
    example: "例如：不超过 8 个场景。",
  },
  {
    value: "cast",
    label: "人数",
    hint: "核心角色与可控配角数量",
    example: "例如：核心角色不超过 4 人。",
  },
  {
    value: "duration",
    label: "时长",
    hint: "体验、阅读或游玩时长",
    example: "例如：单次体验控制在 60–90 分钟。",
  },
  {
    value: "content_scale",
    label: "内容尺度",
    hint: "强度、年龄或敏感内容边界",
    example: "例如：适合 12 岁以上，不出现肢解描写。",
  },
];

export function splitLines(value: string) {
  return value
    .split(/\r?\n/u)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function seedManualCandidate(
  sourceText: string,
  questions: BriefIntakeQuestionView[] = [],
): BriefIntakeCandidateContent {
  const concept = sourceText
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .find(Boolean)
    ?.slice(0, 1000) ?? "";
  return {
    concept,
    core_selling_points: [],
    content_outline: [],
    reasoning_goal: "",
    resolution_mode: "agent_proposed",
    author_answer: null,
    constraints: [],
    pending_decisions: questions
      .filter(
        (question) =>
          !question.required && question.answer_status === "pending",
      )
      .map((question) => ({
        decision_key: `decision_${question.question_key.replace(/^question_/u, "")}`,
        prompt: question.prompt,
        impact: question.impact,
        source: "unresolved" as const,
      })),
    scope_estimate: null,
    risk_notes: [],
    field_sources: {
      concept: concept ? "user_original" : "unresolved",
      core_selling_points: "unresolved",
      content_outline: "unresolved",
      reasoning_goal: "unresolved",
      resolution_mode: "user_confirmed",
      author_answer: "unresolved",
      constraints: "unresolved",
      scope_estimate: "unresolved",
      risk_notes: "unresolved",
    },
  };
}

export function missingCandidateHardFields(content: BriefIntakeCandidateContent) {
  const missing: string[] = [];
  if (!content.concept.trim()) missing.push("一句话概念");
  if (!content.reasoning_goal.trim()) missing.push("推理目标");
  if (
    content.resolution_mode === "author_anchored" &&
    !content.author_answer?.trim()
  ) {
    missing.push("作者底牌");
  }
  return missing;
}

export function discardCandidateTarget(
  candidates: BriefIntakeCandidateView[],
  current: BriefIntakeCandidateView,
) {
  const saved = candidates.find(
    (candidate) =>
      candidate.candidate_id !== current.candidate_id &&
      candidate.is_saved &&
      !candidate.is_stale,
  );
  if (saved) return saved;
  if (current.parent_candidate_id === null) return null;
  return (
    candidates.find(
      (candidate) =>
        candidate.candidate_id === current.parent_candidate_id &&
        !candidate.is_stale,
    ) ?? null
  );
}

export function cloneCandidateContent(content: BriefIntakeCandidateContent) {
  return structuredClone(content);
}
