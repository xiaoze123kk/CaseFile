import type {
  BriefIntakeCandidateContent,
  BriefIntakeCandidateView,
  BriefIntakeConstraintCategory,
  BriefIntakeFieldSource,
  BriefIntakeQuestionView,
  ResolutionMode,
} from "@/lib/api-client";

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
}> = [
  { value: "must_keep", label: "必须保留", hint: "不可被候选改写的事实或意图" },
  { value: "must_avoid", label: "禁止出现", hint: "题材、机制或表达禁区" },
  { value: "scope", label: "规模", hint: "篇幅、场景或案件体量" },
  { value: "cast", label: "人数", hint: "核心角色与可控配角数量" },
  { value: "duration", label: "时长", hint: "体验、阅读或游玩时长" },
  { value: "content_scale", label: "内容尺度", hint: "强度、年龄或敏感内容边界" },
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
