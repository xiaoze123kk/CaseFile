import { describe, expect, it } from "vitest";

import {
  canFreezeBriefReview,
  createEmptyBrief,
  createConstraints,
} from "@/features/intake/intake-model";
import {
  mapBriefContentToReview,
  mapBriefToCandidateContent,
  mapCandidateContentToBrief,
  mapReviewToBriefContent,
} from "@/features/case-session/case-session-mapping";

const CONSTRAINT_KEY_PATTERN = /^constraint_[a-z0-9][a-z0-9_]{0,51}$/u;
const CATEGORIES = new Set([
  "must_keep",
  "must_avoid",
  "scope",
  "cast",
  "duration",
  "content_scale",
  "other",
]);

describe("case session candidate mapping", () => {
  it("maps preset constraint keys into contract constraint_key and category", () => {
    const brief = createEmptyBrief("一名档案修复师发现三份记录都指向一段不存在的时间。");
    brief.constraints[0] = { ...brief.constraints[0], statement: "三份记录互相独立" };
    brief.constraints[2] = { ...brief.constraints[2], statement: "不超过 8 个场景" };

    const content = mapBriefToCandidateContent(brief);
    const constraints = content.constraints;

    expect(constraints.map((item) => item.constraint_key)).toEqual([
      "constraint_must_keep",
      "constraint_scope",
    ]);
    for (const constraint of constraints) {
      expect(CONSTRAINT_KEY_PATTERN.test(constraint.constraint_key)).toBe(true);
      expect(CATEGORIES.has(constraint.category)).toBe(true);
      expect(constraint.source).toBe("user_confirmed");
      expect(constraint.confirmed).toBe(true);
    }
  });

  it("normalizes server-side constraint keys back into contract keys on round trip", () => {
    const serverCandidate = {
      concept: "概念",
      core_selling_points: ["卖点"],
      content_outline: ["骨架"],
      reasoning_goal: "推理目标",
      resolution_mode: "agent_proposed" as const,
      conclusion_mode: "undetermined" as const,
      author_answer: null,
      constraints: [
        {
          constraint_key: "constraint_no_magic",
          category: "other" as const,
          statement: "不使用超自然解释",
          strength: "hard" as const,
          confirmed: true,
          source: "agent_suggestion" as const,
        },
        {
          constraint_key: "constraint_must_keep",
          category: "must_keep" as const,
          statement: "三份记录互相独立",
          strength: "hard" as const,
          confirmed: true,
          source: "agent_suggestion" as const,
        },
        {
          constraint_key: "constraint_resolution_author_provided",
          category: "other" as const,
          statement: "保留作者已确定的结局",
          strength: "hard" as const,
          confirmed: true,
          source: "agent_suggestion" as const,
        },
        {
          constraint_key: "constraint_scale_mid_length",
          category: "other" as const,
          statement: "按中篇体量组织",
          strength: "soft" as const,
          confirmed: true,
          source: "agent_suggestion" as const,
        },
      ],
      pending_decisions: [],
      scope_estimate: null,
      risk_notes: [],
      field_sources: {
        concept: "user_original" as const,
        core_selling_points: "agent_suggestion" as const,
        content_outline: "agent_suggestion" as const,
        reasoning_goal: "agent_suggestion" as const,
        resolution_mode: "user_confirmed" as const,
        conclusion_mode: "agent_suggestion" as const,
        author_answer: "unresolved" as const,
        constraints: "agent_suggestion" as const,
        scope_estimate: "unresolved" as const,
        risk_notes: "agent_suggestion" as const,
      },
    };

    const brief = mapCandidateContentToBrief(serverCandidate);
    const presetMustKeep = brief.constraints.find((row) => row.key === "must_keep");
    expect(presetMustKeep?.statement).toBe("三份记录互相独立");
    const unknownNoMagic = brief.constraints.find(
      (row) => row.key === "constraint_no_magic",
    );
    expect(unknownNoMagic?.statement).toBe("不使用超自然解释");
    expect(unknownNoMagic?.label).toBe("其他约束");
    expect(
      brief.constraints.find(
        (row) => row.key === "constraint_resolution_author_provided",
      )?.label,
    ).toBe("结论模式：作者提供");
    expect(
      brief.constraints.find((row) => row.key === "constraint_scale_mid_length")
        ?.label,
    ).toBe("规模：中篇");

    const roundTripped = mapBriefToCandidateContent(brief);
    const byKey = new Map(
      roundTripped.constraints.map((item) => [item.constraint_key, item]),
    );

    const noMagic = byKey.get("constraint_no_magic");
    expect(noMagic).toBeDefined();
    expect(noMagic?.category).toBe("other");
    expect(noMagic?.statement).toBe("不使用超自然解释");

    const mustKeep = byKey.get("constraint_must_keep");
    expect(mustKeep?.category).toBe("must_keep");

    for (const constraint of roundTripped.constraints) {
      expect(CONSTRAINT_KEY_PATTERN.test(constraint.constraint_key)).toBe(true);
      expect(CATEGORIES.has(constraint.category)).toBe(true);
    }
  });

  it("keeps preset rows without statements out of the candidate content", () => {
    const brief = createEmptyBrief("概念");
    const content = mapBriefToCandidateContent(brief);

    expect(content.constraints).toEqual([]);
    expect(createConstraints().length).toBe(6);
  });

  it("parses boundary text into atomic constraints when the server has none", () => {
    const content = {
      source_record_ids: [1],
      creative_intent: "创作意图",
      reasoning_proposition: "核心命题",
      resolution_mode: "agent_proposed" as const,
      conclusion_mode: "undetermined" as const,
      author_answer: null,
      author_anchors: [],
      boundary_text: "必须：三份记录互相独立\n偏好：氛围克制\n必须：不使用超自然解释",
      creative_constraints: [],
      core_selling_points: ["卖点"],
      content_outline: ["骨架"],
      scope_estimate: null,
      risk_notes: [],
    };

    const review = mapBriefContentToReview(content, []);
    expect(review.boundaryText).toBe(content.boundary_text);
    expect(review.creativeConstraints).toEqual([
      { id: "constraint-agent-1", statement: "三份记录互相独立", strength: "hard", origin: "agent" },
      { id: "constraint-agent-2", statement: "氛围克制", strength: "soft", origin: "agent" },
      { id: "constraint-agent-3", statement: "不使用超自然解释", strength: "hard", origin: "agent" },
    ]);
    expect(canFreezeBriefReview(review)).toBe(true);
  });

  it("prefers server atomic constraints over parsing boundary text", () => {
    const content = {
      source_record_ids: [1],
      creative_intent: "创作意图",
      reasoning_proposition: "核心命题",
      resolution_mode: "agent_proposed" as const,
      conclusion_mode: "undetermined" as const,
      author_answer: null,
      author_anchors: [],
      boundary_text: "必须：三份记录互相独立",
      creative_constraints: [
        {
          constraint_id: "constraint_1",
          statement: "Agent 拆解出的原子项",
          strength: "hard" as const,
        },
      ],
      core_selling_points: [],
      content_outline: [],
      scope_estimate: null,
      risk_notes: [],
    };

    const review = mapBriefContentToReview(content, []);
    expect(review.creativeConstraints).toEqual([
      { id: "constraint_1", statement: "Agent 拆解出的原子项", strength: "hard", origin: "agent" },
    ]);
    expect(canFreezeBriefReview(review)).toBe(true);
  });

  it("normalizes hyphenated atomic ids into contract form on write", () => {
    const review = mapBriefContentToReview(
      {
        source_record_ids: [],
        creative_intent: "创作意图",
        reasoning_proposition: "核心命题",
        resolution_mode: "author_anchored" as const,
        conclusion_mode: "unique" as const,
        author_answer: "真凶是档案修复师自己。",
        author_anchors: [],
        boundary_text: "必须：三份记录互相独立",
        creative_constraints: [],
        core_selling_points: [],
        content_outline: [],
        scope_estimate: null,
        risk_notes: [],
      },
      [],
    );
    review.authorAnchors = [
      { id: "anchor-agent-1", statement: "真凶是档案修复师自己。", origin: "agent" },
      { id: "anchor-manual-2", statement: "封存前必须揭晓。", origin: "manual" },
    ];
    review.creativeConstraints = [
      { id: "constraint-agent-1", statement: "三份记录互相独立", strength: "hard", origin: "agent" },
      { id: "constraint-manual-2", statement: "氛围克制", strength: "soft", origin: "manual" },
      { id: "constraint_1", statement: "服务端既有原子项", strength: "hard", origin: "agent" },
    ];

    const brief = createEmptyBrief("概念");
    const content = mapReviewToBriefContent(review, brief, {
      source_record_ids: [],
    } as never);

    const idPattern = /^(anchor|constraint)_[a-z0-9][a-z0-9_]{0,55}$/u;
    for (const anchor of content.author_anchors ?? []) {
      expect(idPattern.test(anchor.anchor_id)).toBe(true);
    }
    for (const constraint of content.creative_constraints ?? []) {
      expect(idPattern.test(constraint.constraint_id)).toBe(true);
    }
    const ids = (content.creative_constraints ?? []).map(
      (item) => item.constraint_id,
    );
    expect(ids).toEqual([
      "constraint_agent_1",
      "constraint_manual_2",
      "constraint_1",
    ]);
  });
});
