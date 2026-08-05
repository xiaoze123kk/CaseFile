import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntakeConfirmationStep } from "@/features/workflow/intake-confirmation-step";
import type {
  BriefIntakeCandidateContent,
  BriefIntakeView,
} from "@/lib/api-client";

afterEach(cleanup);

const manualSeed: BriefIntakeCandidateContent = {
  concept: "一个尚未展开的故事概念。",
  core_selling_points: [],
  content_outline: [],
  reasoning_goal: "",
  resolution_mode: "agent_proposed",
  author_answer: null,
  constraints: [],
  pending_decisions: [],
  scope_estimate: null,
  risk_notes: [],
  field_sources: {
    concept: "user_original",
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

const intake: BriefIntakeView = {
  brief_intake_id: 1,
  project_id: 1,
  revision: 1,
  stage: "confirmation",
  current_source: null,
  current_questions_task_run_id: null,
  questions: [],
  hard_questions_resolved: true,
  current_candidate_id: null,
  adopted_candidate_id: null,
  candidates: [],
  pending_decisions: [],
  brief: {
    brief_id: 1,
    draft_revision: 1,
    current_version_id: null,
    has_content: false,
  },
  updated_at: null,
};

function renderEditor() {
  return render(
    <IntakeConfirmationStep
      busy={false}
      currentCandidate={null}
      error={null}
      intake={intake}
      manualSeed={manualSeed}
      onActivateCandidate={vi.fn()}
      onAdoptCandidate={vi.fn()}
      onBack={vi.fn()}
      onCreateManualCandidate={vi.fn()}
      onDialogueRevision={vi.fn()}
      onOpenSettings={vi.fn()}
      onSaveCandidate={vi.fn()}
      providerReady
      synthesizeTask={null}
    />,
  );
}

describe("Intake confirmation field guidance", () => {
  it("shows a concrete, non-persistent example for every editable field", () => {
    renderEditor();

    expect(
      screen.getByPlaceholderText(
        "例如：四名玩家在不断重启的空间站中追查事故真相。",
      ),
    ).toBeInTheDocument();
    const sellingPoints = screen.getByPlaceholderText(
      "例如：循环重启 / 第五人权限记录 / 保护协议",
    );
    expect(sellingPoints).toBeInTheDocument();
    expect(sellingPoints).toHaveValue("");
    expect(
      screen.getByPlaceholderText(
        "例如：发现异常 → 追查权限记录 → 重建时间线 → 做出终止决定",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("例如：在第七次循环结束前找出谁触发了重启。"),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("例如：4 名玩家 / 6 个场景 / 60–90 分钟"),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("例如：线索过多，玩家无法复盘。"),
    ).toBeInTheDocument();
    expect(screen.getByText("概括核心设定与冲突")).toBeInTheDocument();
    expect(screen.getByText("决定谁来锁定结论")).toBeInTheDocument();
    expect(screen.getByText("例：已知幕后黑手。"))
      .toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /按作者底牌展开/u }));
    expect(
      screen.getByPlaceholderText("例如：真正触发重启的是维护机器人，而不是玩家。"),
    ).toBeInTheDocument();
  });

  it("keeps constraint examples in the drawer as placeholders", () => {
    renderEditor();

    expect(screen.getByText("点击展开")).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("例如：不超过 8 个场景。"),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("例如：适合 12 岁以上，不出现肢解描写。"),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("例如：妹妹偷吃蛋糕这一事实不能改掉。"))
      .toBeInTheDocument();
  });
});
