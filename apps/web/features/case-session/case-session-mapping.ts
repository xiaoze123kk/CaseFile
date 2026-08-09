"use client";

import type {
  BriefContent,
  BriefIntakeCandidateContent,
  BriefIntakeCandidateView,
  BriefIntakeQuestionView,
  BriefIntakeView,
  CandidateStrategy,
  DraftCandidateView,
} from "@/lib/api-client";

import {
  buildWorkbenchCandidates,
  type WorkbenchCandidate,
} from "@/features/analyst-workbench/analyst-fixture";
import type {
  IntakeAnswer,
  IntakeBrief,
  BriefReview,
  BriefCandidate,
  IntakeConstraint,
  ConstraintReview,
  IntakeQuestion,
} from "@/features/intake/intake-model";
import {
  createConstraints,
  extractAuthorAnchors,
  type FieldSource,
} from "@/features/intake/intake-model";

const CONSTRAINT_LABELS: Record<string, { label: string; hint: string }> = {
  must_keep: { label: "必须保留", hint: "不可被后续候选改写的事实或意图" },
  must_avoid: { label: "禁止出现", hint: "题材、机制或表达禁区" },
  scope: { label: "规模", hint: "场景与案件体量" },
  cast: { label: "人数", hint: "核心角色与可控配角数量" },
  duration: { label: "时长", hint: "体验、阅读或游玩时长" },
  content_scale: { label: "内容尺度", hint: "强度、年龄或敏感内容边界" },
  resolution_author_provided: {
    label: "结论模式：作者提供",
    hint: "作品需要保留作者已经确定的结局与动机。",
  },
  scale_mid_length: {
    label: "规模：中篇",
    hint: "故事按中篇体量组织，包含多次改版痕迹调查与人物对话。",
  },
};

function constraintLabel(key: string) {
  const normalizedKey = key.replace(/^constraint_/, "");
  return (
    CONSTRAINT_LABELS[normalizedKey] ?? {
      label: "其他约束",
      hint: "由 Agent 或作者补充的创作边界。",
    }
  );
}

function sourceFromServer(
  source: BriefIntakeCandidateContent["field_sources"][keyof BriefIntakeCandidateContent["field_sources"]],
): FieldSource {
  return source;
}

export function mapIntakeQuestions(
  questions: BriefIntakeQuestionView[],
): IntakeQuestion[] {
  return questions.map((question) => ({
    key: question.question_key,
    ordinal: question.ordinal,
    prompt: question.prompt,
    impact: question.impact,
    required: question.required,
    suggestions: question.suggestions,
  }));
}

export function mapAnswersFromQuestions(
  questions: BriefIntakeQuestionView[],
): Record<string, IntakeAnswer> {
  const answers: Record<string, IntakeAnswer> = {};
  for (const question of questions) {
    if (question.answer_status === "unanswered") continue;
    if (question.answer_status === "pending") {
      answers[question.question_key] = {
        text: "稍后决定",
        source: "unresolved",
        pending: true,
      };
      continue;
    }
    answers[question.question_key] = {
      text: question.answer_text ?? "",
      source:
        question.answer_source === "agent_suggestion"
          ? "agent_suggestion"
          : "user_confirmed",
      pending: false,
    };
  }
  return answers;
}

function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean);
}

// 服务端 adopt 投影只产出 boundary_text（"必须：…"/"偏好：…" 行），不产出
// creative_constraints；这里把边界原文解析为原子约束，恢复 fixture 时代
// "进入审阅即可冻结" 的交互，原子项内容与审阅页展示的边界原文一致。
function boundaryLinesToConstraints(
  boundaryText: string,
): ConstraintReview[] {
  return splitLines(boundaryText).map((line, index) => ({
    id: `constraint-agent-${index + 1}`,
    statement: line.replace(/^(必须|偏好)：/u, "").trim(),
    strength: line.startsWith("偏好：") ? "soft" : "hard",
    origin: "agent" as const,
  }));
}

export function mapCandidateContentToBrief(
  content: BriefIntakeCandidateContent,
): IntakeBrief {
  const preset = createConstraints();
  const constraints: IntakeConstraint[] = preset.map((row) => {
    const serverRow = content.constraints.find(
      (item) =>
        item.constraint_key === row.key ||
        item.constraint_key === `constraint_${row.key}`,
    );
    return {
      ...row,
      statement: serverRow?.statement ?? "",
      strength: serverRow?.strength ?? row.strength,
    };
  });
  for (const serverRow of content.constraints) {
    if (!constraints.some((row) => row.key === serverRow.constraint_key)) {
      const display = constraintLabel(serverRow.constraint_key);
      constraints.push({
        key: serverRow.constraint_key,
        label: display.label,
        hint: display.hint,
        placeholder: "",
        statement: serverRow.statement,
        strength: serverRow.strength,
      });
    }
  }
  const sources = content.field_sources;
  return {
    concept: content.concept,
    sellingPoints: content.core_selling_points.join("\n"),
    outline: content.content_outline.join("\n"),
    reasoningGoal: content.reasoning_goal,
    resolutionMode: content.resolution_mode,
    conclusionMode: content.conclusion_mode,
    authorAnswer: content.author_answer ?? "",
    scopeEstimate: content.scope_estimate ?? "",
    riskNotes: content.risk_notes.join("\n"),
    constraints,
    sources: {
      concept: sourceFromServer(sources.concept),
      sellingPoints: sourceFromServer(sources.core_selling_points),
      outline: sourceFromServer(sources.content_outline),
      reasoningGoal: sourceFromServer(sources.reasoning_goal),
      resolutionMode: sourceFromServer(sources.resolution_mode),
      conclusionMode: sourceFromServer(sources.conclusion_mode),
      authorAnswer: sourceFromServer(sources.author_answer),
      scopeEstimate: sourceFromServer(sources.scope_estimate),
      riskNotes: sourceFromServer(sources.risk_notes),
    },
  };
}

export function mapCandidateView(
  view: BriefIntakeCandidateView,
): BriefCandidate {
  const brief = mapCandidateContentToBrief(view.content);
  const originLabel: Record<BriefCandidate["origin"], string> = {
    agent: "方向核验初稿",
    manual: "人工简报起点",
    dialogue: "对话修改候选",
  };
  const origin: BriefCandidate["origin"] =
    view.origin === "agent_synthesis"
      ? "agent"
      : view.origin === "manual_edit"
        ? "manual"
        : "dialogue";
  return {
    id: view.candidate_id,
    label: originLabel[origin],
    origin,
    createdAt: view.created_at ?? "服务端生成",
    bookmarked: view.is_saved,
    brief,
  };
}

export function currentIntakeCandidate(
  intake: BriefIntakeView,
): BriefIntakeCandidateView | null {
  return (
    intake.candidates.find(
      (candidate) => candidate.candidate_id === intake.current_candidate_id,
    ) ?? null
  );
}

export function mapIntakeToSessionState(intake: BriefIntakeView) {
  const current = currentIntakeCandidate(intake);
  return {
    questions: mapIntakeQuestions(intake.questions),
    answers: mapAnswersFromQuestions(intake.questions),
    briefCandidates: intake.candidates.map(mapCandidateView),
    currentBriefCandidateId: intake.current_candidate_id,
    brief: current ? mapCandidateContentToBrief(current.content) : null,
  };
}

export function briefsMatch(
  brief: IntakeBrief,
  content: BriefIntakeCandidateContent,
): boolean {
  return (
    brief.concept === content.concept &&
    brief.sellingPoints === content.core_selling_points.join("\n") &&
    brief.outline === content.content_outline.join("\n") &&
    brief.reasoningGoal === content.reasoning_goal &&
    brief.resolutionMode === content.resolution_mode &&
    brief.conclusionMode === content.conclusion_mode &&
    brief.authorAnswer === (content.author_answer ?? "") &&
    brief.scopeEstimate === (content.scope_estimate ?? "") &&
    brief.riskNotes === content.risk_notes.join("\n") &&
    brief.constraints.every((row) => {
      const serverRow = content.constraints.find(
        (item) => item.constraint_key === row.key,
      );
      return serverRow?.statement === row.statement;
    })
  );
}

const PRESET_CONSTRAINT_CATEGORIES = new Set([
  "must_keep",
  "must_avoid",
  "scope",
  "cast",
  "duration",
  "content_scale",
]);

export function mapBriefToCandidateContent(
  brief: IntakeBrief,
): BriefIntakeCandidateContent {
  const sources = brief.sources;
  return {
    concept: brief.concept,
    core_selling_points: splitLines(brief.sellingPoints),
    content_outline: splitLines(brief.outline),
    reasoning_goal: brief.reasoningGoal,
    resolution_mode: brief.resolutionMode,
    conclusion_mode: brief.conclusionMode,
    author_answer: brief.authorAnswer || null,
    constraints: brief.constraints
      .filter((constraint) => constraint.statement.trim())
      .map((constraint) => {
        // 契约要求 constraint_key 带 constraint_ 前缀，category 使用预设枚举。
        const stripped = constraint.key.replace(/^constraint_/, "");
        return {
          constraint_key: `constraint_${stripped}`,
          category: (
            PRESET_CONSTRAINT_CATEGORIES.has(stripped) ? stripped : "other"
          ) as BriefIntakeCandidateContent["constraints"][number]["category"],
          statement: constraint.statement.trim(),
          strength: constraint.strength,
          confirmed: true,
          source: "user_confirmed",
        };
      }),
    pending_decisions: [],
    scope_estimate: brief.scopeEstimate || null,
    risk_notes: splitLines(brief.riskNotes),
    field_sources: {
      concept: sources.concept,
      core_selling_points: sources.sellingPoints,
      content_outline: sources.outline,
      reasoning_goal: sources.reasoningGoal,
      resolution_mode: sources.resolutionMode,
      conclusion_mode: sources.conclusionMode,
      author_answer: sources.authorAnswer,
      constraints: "user_confirmed",
      scope_estimate: sources.scopeEstimate,
      risk_notes: sources.riskNotes,
    },
  };
}

export function mapIntakeBriefToAnchorContent(
  brief: IntakeBrief,
  base: BriefContent | Record<string, never>,
  sourceRecordId: number | null,
): BriefContent {
  const constraints = brief.constraints
    .filter((constraint) => constraint.statement.trim())
    .map((constraint) => ({
      constraint_id: `constraint_${constraint.key.replace(/^constraint_/u, "")}`,
      statement: constraint.statement.trim(),
      strength: constraint.strength,
    }));
  const authorAnswer = brief.authorAnswer.trim() || null;
  return {
    ...base,
    source_record_ids:
      Array.isArray(base.source_record_ids) && base.source_record_ids.length
        ? base.source_record_ids
        : sourceRecordId
          ? [sourceRecordId]
          : [],
    creative_intent: brief.concept.trim(),
    reasoning_proposition: brief.reasoningGoal.trim(),
    // The suggestion is allowed before the author has supplied a final answer.
    resolution_mode:
      authorAnswer && brief.resolutionMode === "author_anchored"
        ? "author_anchored"
        : "agent_proposed",
    conclusion_mode: brief.conclusionMode,
    author_answer: authorAnswer,
    author_anchors: [],
    boundary_text: constraints.length
      ? constraints.map((constraint) => constraint.statement).join("\n")
      : null,
    creative_constraints: constraints,
  };
}

export function mapBriefContentToReview(
  content: BriefContent | Record<string, never>,
  pendingDecisions: string[],
): BriefReview {
  const briefContent = "creative_intent" in content ? content : null;
  const serverAuthorAnchors = (briefContent?.author_anchors ?? []).map((anchor) => ({
    id: anchor.anchor_id,
    statement: anchor.statement,
    origin: "agent" as const,
  }));
  // Brief Intake 采用投影会先写入作者底牌原文，原子项则留待审阅确认。
  // 与创作边界的回填一致，这里先从原文建立可审阅原子项，确认冻结时再写回服务端。
  const authorAnchors =
    serverAuthorAnchors.length > 0
      ? serverAuthorAnchors
      : extractAuthorAnchors(briefContent?.author_answer ?? "");
  const boundaryText = briefContent?.boundary_text ?? "";
  const serverConstraints = (briefContent?.creative_constraints ?? []).map(
    (constraint) => ({
      id: constraint.constraint_id,
      statement: constraint.statement,
      strength: constraint.strength,
      origin: "agent" as const,
    }),
  );
  const creativeConstraints =
    serverConstraints.length > 0
      ? serverConstraints
      : boundaryLinesToConstraints(boundaryText);
  return {
    creativeIntent: briefContent?.creative_intent ?? "",
    reasoningProposition: briefContent?.reasoning_proposition ?? "",
    resolutionMode: briefContent?.resolution_mode ?? "agent_proposed",
    conclusionMode: briefContent?.conclusion_mode ?? "undetermined",
    authorAnswer: briefContent?.author_answer ?? "",
    boundaryText,
    authorAnchors,
    creativeConstraints,
    pendingDecisions,
    dirty: false,
    saved: true,
  };
}

// 契约要求原子 id 匹配 ^(anchor|constraint)_[a-z0-9_]+$（无连字符）；
// 前端拆解/人工新增使用 anchor-agent-N 一类可读 id，写回前统一规范化。
function atomicContractId(
  raw: string,
  kind: "anchor" | "constraint",
): string {
  const body = raw
    .replace(new RegExp(`^${kind}[-_]`, "u"), "")
    .replace(/[^a-z0-9_]/gu, "_")
    .replace(/^_+|_+$/gu, "")
    .slice(0, 48);
  return `${kind}_${body || "item"}`;
}

export function mapReviewToBriefContent(
  review: BriefReview,
  brief: IntakeBrief,
  base: BriefContent | Record<string, never>,
): BriefContent {
  const authorAnswer =
    review.resolutionMode === "author_anchored"
      ? review.authorAnswer.trim() || null
      : null;
  const boundaryText = review.boundaryText.trim() || null;
  return {
    ...base,
    source_record_ids: Array.isArray(base.source_record_ids)
      ? base.source_record_ids
      : [],
    creative_intent: review.creativeIntent.trim(),
    reasoning_proposition: review.reasoningProposition.trim(),
    resolution_mode: review.resolutionMode,
    conclusion_mode: review.conclusionMode,
    author_answer: authorAnswer,
    author_anchors: authorAnswer
      ? review.authorAnchors
          .map(({ id, statement }) => ({
            anchor_id: atomicContractId(id, "anchor"),
            statement: statement.trim(),
          }))
          .filter((anchor) => Boolean(anchor.statement))
      : [],
    boundary_text: boundaryText,
    creative_constraints: boundaryText
      ? review.creativeConstraints
          .map(({ id, statement, strength }) => ({
            constraint_id: atomicContractId(id, "constraint"),
            statement: statement.trim(),
            strength,
          }))
          .filter((constraint) => Boolean(constraint.statement))
      : [],
    core_selling_points: splitLines(brief.sellingPoints),
    content_outline: splitLines(brief.outline),
    scope_estimate: brief.scopeEstimate.trim() || null,
    risk_notes: splitLines(brief.riskNotes),
  };
}

export interface DraftCandidateRuntimeState {
  taskRunId: number;
  isCurrentBrief: boolean;
  isCurrent: boolean;
  isAdopted: boolean;
  canAdopt: boolean;
  completedAt: string | null;
}

export type SessionWorkbenchCandidate = WorkbenchCandidate & {
  /** Present for server-backed candidates; omitted by the isolated fixture harness. */
  candidateState?: DraftCandidateRuntimeState;
};

export function mapWorkbenchCandidateView(
  view: DraftCandidateView,
  base: WorkbenchCandidate,
  differenceNote?: string,
): SessionWorkbenchCandidate {
  const totalObjects = Object.values(view.object_counts).reduce(
    (sum, count) => sum + count,
    0,
  );
  return {
    ...base,
    id: `draft-${view.task_run_id}`,
    candidateState: {
      taskRunId: view.task_run_id,
      isCurrentBrief: view.is_current_brief,
      isCurrent: view.is_current,
      isAdopted: view.is_adopted,
      canAdopt: view.can_adopt,
      completedAt: view.completed_at,
    },
    briefVersion: view.brief_version_no,
    candidateStrategy: view.candidate_strategy,
    focusLabel: view.candidate_strategy_label,
    title: view.title,
    summary: `共 ${totalObjects} 个对象，已通过结构与引用校验。${
      differenceNote ? ` ${differenceNote}。` : ""
    }`,
    reasoningQuestion:
      view.reasoning_questions[0] ?? base.reasoningQuestion,
    objectCounts: view.object_counts,
    constraintStatements: view.constraint_statements,
    strengths: ["已完成确定性结构与引用校验", "可作为工作台继续编辑的基础"],
    tradeoffs: [
      "采用前需在工作台逐项核对",
      "后续修订会生成新的简报版本",
      ...(differenceNote ? [differenceNote] : []),
    ],
  };
}

const DRAFT_CANDIDATE_STRATEGY_ORDER: Record<CandidateStrategy, number> = {
  structure_first: 0,
  atmosphere_first: 1,
  reasoning_first: 2,
  balanced: 3,
};

const CANDIDATE_STRATEGY_TO_FOCUS = {
  structure_first: "structure",
  atmosphere_first: "atmosphere",
  reasoning_first: "reasoning",
} as const;

export function rankDraftCandidateStrategy(strategy: CandidateStrategy): number {
  return DRAFT_CANDIDATE_STRATEGY_ORDER[strategy];
}

function workbenchCandidateInput(review: BriefReview) {
  return {
    creativeIntent: review.creativeIntent,
    reasoningProposition: review.reasoningProposition,
    authorAnswer: review.authorAnswer,
    constraints: review.creativeConstraints
      .map((constraint) => constraint.statement.trim())
      .filter(Boolean),
  };
}

function candidateFocus(strategy: CandidateStrategy) {
  return strategy === "balanced"
    ? "structure"
    : CANDIDATE_STRATEGY_TO_FOCUS[strategy];
}

export function mapCurrentBriefDraftCandidates(
  views: DraftCandidateView[],
  review: BriefReview,
  briefVersion: number,
): SessionWorkbenchCandidate[] {
  const duplicateHashCounts = new Map<string, number>();
  for (const candidate of views) {
    duplicateHashCounts.set(
      candidate.content_hash,
      (duplicateHashCounts.get(candidate.content_hash) ?? 0) + 1,
    );
  }
  const base = buildWorkbenchCandidates(
    workbenchCandidateInput(review),
    briefVersion,
  );
  return [...views]
    .sort(
      (left, right) =>
        rankDraftCandidateStrategy(left.candidate_strategy) -
        rankDraftCandidateStrategy(right.candidate_strategy),
    )
    .map((view) =>
      mapWorkbenchCandidateView(
        view,
        base.find((candidate) => candidate.focus === candidateFocus(view.candidate_strategy)) ??
          base[0],
        duplicateHashCounts.get(view.content_hash)! > 1
          ? "与同批候选内容相同，差异不足"
          : undefined,
      ),
    );
}

export function mapDraftCandidateHistory(
  views: DraftCandidateView[],
  review: BriefReview,
): SessionWorkbenchCandidate[] {
  return [...views]
    .sort((left, right) => {
      if (left.is_current_brief !== right.is_current_brief) {
        return left.is_current_brief ? -1 : 1;
      }
      if (left.brief_version_no !== right.brief_version_no) {
        return right.brief_version_no - left.brief_version_no;
      }
      return (
        rankDraftCandidateStrategy(left.candidate_strategy) -
        rankDraftCandidateStrategy(right.candidate_strategy)
      );
    })
    .map((view) => {
      const base = buildWorkbenchCandidates(
        workbenchCandidateInput(review),
        view.brief_version_no,
      );
      return mapWorkbenchCandidateView(
        view,
        base.find((candidate) => candidate.focus === candidateFocus(view.candidate_strategy)) ??
          base[0],
      );
    });
}

export function mapAuthoritativeDraftCandidateState(
  views: DraftCandidateView[],
  review: BriefReview,
  previewCandidateId: string | null,
) {
  const current = views.find((candidate) => candidate.is_current);
  const currentId = current ? `draft-${current.task_run_id}` : null;
  return {
    draftCandidates: mapDraftCandidateHistory(views, review),
    adoptedCandidateId: currentId,
    previewCandidateId: currentId ?? previewCandidateId,
  };
}
