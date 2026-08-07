import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  buildWorkbenchCandidates,
  validateWorkbenchSeed,
} from "@/features/analyst-workbench/analyst-fixture";
import {
  createInitialCaseSessionState,
  CaseSessionProvider,
  caseSessionReducer,
  candidateTaskStageFromTask,
  workbenchCandidateStatus,
  type CaseSessionState,
  useCaseSession,
} from "@/features/case-session/case-session-provider";
import {
  atomicReviewComplete,
  canFreezeBriefReview,
  createBriefReview,
  sampleIdea,
  synthesizeBrief,
} from "@/features/intake/intake-model";

afterEach(cleanup);

const answers = {
  reasoning_goal: {
    text: "找出三份可靠记录为何共同指向不存在的时间。",
    source: "user_confirmed" as const,
    pending: false,
  },
  experience_scale: {
    text: "",
    source: "unresolved" as const,
    pending: true,
  },
};

function reviewedBriefFixture() {
  const brief = synthesizeBrief(sampleIdea, answers);
  brief.resolutionMode = "author_anchored";
  brief.authorAnswer = "共享校准层在封存前改写了第四条索引。";
  brief.constraints[0] = {
    ...brief.constraints[0],
    statement: "必须保留三份记录互相独立这一前提。",
    strength: "hard",
  };
  return { brief, review: createBriefReview(brief, answers) };
}

describe("case session state model", () => {
  it("deterministically generates three distinct, reference-complete workbench seeds", () => {
    const { review } = reviewedBriefFixture();
    const input = {
      creativeIntent: review.creativeIntent,
      reasoningProposition: review.reasoningProposition,
      authorAnswer: review.authorAnswer,
      constraints: review.creativeConstraints.map((item) => item.statement),
    };

    const first = buildWorkbenchCandidates(input, 3);
    const second = buildWorkbenchCandidates(input, 3);

    expect(first).toEqual(second);
    expect(first).toHaveLength(3);
    expect(first.map((candidate) => candidate.focus)).toEqual([
      "structure",
      "atmosphere",
      "reasoning",
    ]);
    expect(new Set(first.map((candidate) => candidate.workbenchSeed.id)).size).toBe(3);
    first.forEach((candidate) => {
      expect(candidate.briefVersion).toBe(3);
      expect(candidate.constraintStatements).toContain(
        "必须保留三份记录互相独立这一前提。",
      );
      expect(validateWorkbenchSeed(candidate.workbenchSeed)).toEqual([]);
    });
  });

  it("blocks freezing until required fields, atomic review, and saved state all pass", () => {
    const { review } = reviewedBriefFixture();

    expect(atomicReviewComplete(review)).toBe(true);
    expect(canFreezeBriefReview(review)).toBe(false);
    expect(canFreezeBriefReview({ ...review, saved: true })).toBe(true);
    expect(
      canFreezeBriefReview({ ...review, creativeIntent: "", saved: true }),
    ).toBe(false);
    expect(
      canFreezeBriefReview({ ...review, authorAnchors: [], saved: true }),
    ).toBe(false);
    expect(
      canFreezeBriefReview({
        ...review,
        creativeConstraints: [],
        saved: true,
      }),
    ).toBe(false);
    expect(
      canFreezeBriefReview({ ...review, dirty: true, saved: true }),
    ).toBe(false);
  });

  it("keeps an adopted workbench valid while making non-adopted old candidates stale", () => {
    const { brief, review } = reviewedBriefFixture();
    let state: CaseSessionState = {
      ...createInitialCaseSessionState(),
      brief,
      review,
    };

    state = caseSessionReducer(state, { type: "freeze_review" });
    expect(state.frozenBriefVersion).toBeNull();
    state = caseSessionReducer(state, { type: "save_review" });
    state = caseSessionReducer(state, { type: "freeze_review" });
    expect(state.frozenBriefVersion).toBe(1);

    const candidates = buildWorkbenchCandidates(
      {
        creativeIntent: review.creativeIntent,
        reasoningProposition: review.reasoningProposition,
        authorAnswer: review.authorAnswer,
        constraints: review.creativeConstraints.map((item) => item.statement),
      },
      1,
    );
    state = caseSessionReducer(state, {
      type: "complete_generation",
      candidates,
    });
    state = caseSessionReducer(state, {
      type: "adopt_candidate",
      candidateId: candidates[0].id,
    });
    expect(workbenchCandidateStatus(state, candidates[0])).toBe("current");

    state = caseSessionReducer(state, { type: "begin_revision" });
    expect(state.workingBriefVersion).toBe(2);
    expect(state.frozenBriefVersion).toBeNull();
    expect(state.adoptedCandidateId).toBe(candidates[0].id);
    expect(workbenchCandidateStatus(state, candidates[0])).toBe("current");
    expect(workbenchCandidateStatus(state, candidates[1])).toBe("stale");

    const unchanged = caseSessionReducer(state, {
      type: "adopt_candidate",
      candidateId: candidates[1].id,
    });
    expect(unchanged.adoptedCandidateId).toBe(candidates[0].id);
    expect(
      caseSessionReducer(state, {
        type: "preview_candidate",
        candidateId: candidates[1].id,
      }).previewCandidateId,
    ).toBe(candidates[1].id);
  });
});

function ProviderProbe() {
  const { state, patchState } = useCaseSession();
  return (
    <button
      onClick={() => patchState({ sourceText: "客户端路由内存" })}
      type="button"
    >
      {state.sourceText || "空白初态"}
    </button>
  );
}

describe("CaseSessionProvider lifecycle", () => {
  it("keeps state inside one mount and resets after the provider remounts", () => {
    const first = render(
      <CaseSessionProvider>
        <ProviderProbe />
      </CaseSessionProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "空白初态" }));
    expect(
      screen.getByRole("button", { name: "客户端路由内存" }),
    ).toBeInTheDocument();

    first.unmount();
    render(
      <CaseSessionProvider>
        <ProviderProbe />
      </CaseSessionProvider>,
    );
    expect(screen.getByRole("button", { name: "空白初态" })).toBeInTheDocument();
  });
});

describe("candidate generation progress", () => {
  it("maps task stages to user-facing generation stages", () => {
    expect(candidateTaskStageFromTask({ status: "running", stage: "planning" })).toBe(
      "planning",
    );
    expect(candidateTaskStageFromTask({ status: "running", stage: "generating" })).toBe(
      "generating",
    );
    expect(candidateTaskStageFromTask({ status: "running", stage: "validating" })).toBe(
      "validating",
    );
    expect(candidateTaskStageFromTask({ status: "succeeded", stage: "completed" })).toBe(
      "completed",
    );
    expect(candidateTaskStageFromTask({ status: "failed", stage: "failed" })).toBe(
      "failed",
    );
  });

  it("keeps slot progress while a candidate moves through generation", () => {
    let state = createInitialCaseSessionState();
    state = caseSessionReducer(state, {
      type: "start_generation",
      strategies: ["structure_first"],
    });
    state = caseSessionReducer(state, {
      type: "update_generation_slot",
      strategy: "structure_first",
      status: "running",
      stage: "generating",
      taskRunId: 101,
    });
    expect(state.generation.slots.structure_first).toMatchObject({
      status: "running",
      stage: "generating",
      taskRunId: 101,
    });

    state = caseSessionReducer(state, {
      type: "update_generation_slot",
      strategy: "structure_first",
      status: "succeeded",
      stage: "completed",
    });
    expect(state.generation.slots.structure_first).toMatchObject({
      status: "succeeded",
      stage: "completed",
    });

    state = caseSessionReducer(state, {
      type: "update_generation_slot",
      strategy: "structure_first",
      status: "failed",
      stage: "failed",
      attempt: 1,
      error: "候选结构校验失败",
    });
    expect(state.generation.slots.structure_first).toMatchObject({
      status: "failed",
      stage: "failed",
      error: "候选结构校验失败",
    });

    state = caseSessionReducer(state, {
      type: "update_generation_slot",
      strategy: "structure_first",
      status: "running",
      stage: "queued",
      attempt: 2,
      error: null,
    });
    expect(state.generation.slots.structure_first).toMatchObject({
      status: "running",
      stage: "queued",
      attempt: 2,
      error: null,
    });
  });
});
