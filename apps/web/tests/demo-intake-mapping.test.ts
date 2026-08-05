import { describe, expect, it } from "vitest";

import { createEmptyBrief, createConstraints } from "@/features/intake-prototype/intake-prototype-model";
import {
  mapBriefToCandidateContent,
  mapCandidateContentToBrief,
} from "@/features/demo-prototype/demo-intake-mapping";

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

describe("demo intake candidate mapping", () => {
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
});
