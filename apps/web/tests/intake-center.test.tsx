import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BriefContent,
  BriefIntakeCandidateContent,
  BriefIntakeCandidateView,
  BriefIntakeQuestionView,
  BriefIntakeView,
  BriefView,
  DraftCandidateView,
  DraftView,
  TaskView,
} from "@/lib/api-client";

import { CaseSessionProvider } from "@/features/case-session/case-session-provider";
import { IntakeCenter } from "@/features/intake/intake-center";

const { routerPush } = vi.hoisted(() => ({ routerPush: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

function buildFakeBackend() {
  const questions: BriefIntakeQuestionView[] = [
    {
      question_key: "reasoning_goal",
      ordinal: 1,
      prompt: "玩家最终必须回答哪一个问题？",
      impact: "这个答案会决定线索如何组织，也会成为后续验证的核心命题。",
      required: true,
      suggestions: [
        "找出是谁伪造了那段不存在的时间。",
        "判断三份可靠记录为什么会同时说谎。",
      ],
      answer_status: "unanswered",
      answer_text: null,
      answer_source: null,
    },
    {
      question_key: "experience_scale",
      ordinal: 2,
      prompt: "你希望它是一晚完成的小案，还是可以持续扩展的长案？",
      impact: "这会影响角色数量、场景密度与建议体验时长。",
      required: false,
      suggestions: ["一晚完成，控制在 60–90 分钟。", "可以扩成三幕长案。"],
      answer_status: "unanswered",
      answer_text: null,
      answer_source: null,
    },
  ];

  let revision = 1;
  let sourceSeq = 0;
  let source: BriefIntakeView["current_source"] = null;
  let currentQuestions: BriefIntakeQuestionView[] = [];
  let candidates: BriefIntakeCandidateView[] = [];
  let currentCandidateId: number | null = null;
  let briefRevision = 1;
  let briefVersionId: number | null = null;
  let versionNo = 0;
  let briefContent: BriefContent | null = null;
  const caseDraftRevision = 17;
  let draftCandidates: DraftCandidateView[] = [];
  let taskSeq = 100;
  const taskTypes = new Map<number, string>();
  const taskProviders = new Map<number, string>();
  let configuredProviders = ["openai"];
  let failOpenaiAuth = false;
  const generationDraftRevisions: number[] = [];
  const adoptionDraftRevisions: number[] = [];

  function intakeView(): BriefIntakeView {
    const stage =
      currentCandidateId !== null
        ? "confirmation"
        : currentQuestions.length > 0
          ? "questions"
          : "idea";
    return {
      brief_intake_id: 1,
      project_id: 1,
      revision,
      stage,
      current_source: source,
      current_questions_task_run_id: null,
      questions: currentQuestions,
      hard_questions_resolved: currentQuestions.every((question) =>
        question.required
          ? question.answer_status === "user_answered" ||
            question.answer_status === "suggestion_accepted"
          : true,
      ),
      current_candidate_id: currentCandidateId,
      adopted_candidate_id: null,
      candidates,
      pending_decisions: [],
      brief: {
        brief_id: 1,
        draft_revision: briefRevision,
        current_version_id: briefVersionId,
        has_content: briefContent !== null,
      },
      updated_at: null,
    };
  }

  function briefView(): BriefView {
    return {
      brief_id: 1,
      public_id: "brief-1",
      draft_revision: briefRevision,
      content: briefContent ?? ({} as never),
      current_version_id: briefVersionId,
    };
  }

  function baseTask(taskRunId: number): TaskView {
    return {
      task_run_id: taskRunId,
      project_id: 1,
      task_type: "brief_polish",
      status: "running",
      stage: "running",
      provider: "openai",
      model_id: "gpt-5.6-sol",
      input_draft_revision: 1,
      input_brief_revision: null,
      input_source_record_id: null,
      input_brief_intake_id: null,
      input_brief_intake_revision: null,
      base_brief_intake_candidate_id: null,
      agent_thread_id: null,
      input_message_id: null,
      output_message_id: null,
      input_hash: "h",
      attempt_count: 1,
      usage: {},
      result_snapshot_id: null,
      result: null,
      error_code: null,
      failure: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
  }

  function terminalTask(taskRunId: number): TaskView {
    const taskType = taskTypes.get(taskRunId) ?? "brief_to_draft";
    const taskProvider = taskProviders.get(taskRunId) ?? "openai";
    if (
      failOpenaiAuth &&
      taskProvider === "openai" &&
      taskType === "brief_intake_questions"
    ) {
      throw new CaseSessionError(
        "模型服务认证失败，请检查 API Key 与模型权限。",
        "provider_authentication_failed",
      );
    }
    const common: TaskView = {
      ...baseTask(taskRunId),
      task_type: taskType as TaskView["task_type"],
      status: "succeeded",
      stage: "done",
      provider: taskProvider as TaskView["provider"],
    };
    if (taskType === "brief_polish") {
      return {
        ...common,
        result: {
          input_hash: "h",
          polished_text:
            "深夜的修复室里，一名档案修复师发现：三份彼此独立且可靠的记录，都指向一段从未存在过的时间。记录永久封存前，她只剩一次机会。",
          preserved_intent_summary: "保留了原有角色、目标与因果。",
          ambiguities: [],
          introduced_details: ["新增“深夜修复档案”的时间氛围。"],
          polish_mode: "narrative_enhance",
          proposal_source_record: {
            source_record_id: sourceSeq,
            source_kind: "agent_polish_proposal",
            content_text: "校样来源",
            content_hash: "p1",
            parent_source_record_id: null,
            generated_by_task_run_id: null,
            created_at: new Date().toISOString(),
          },
        },
      };
    }
    if (taskType === "brief_intake_questions") {
      const additional = currentQuestions.length > 0;
      const batchNumber = Math.floor(currentQuestions.length / 2) + 1;
      const nextQuestions = questions.map((question, index) => ({
        ...question,
        question_key: additional
          ? `${question.question_key}_${batchNumber}`
          : question.question_key,
        ordinal: currentQuestions.length + index + 1,
        prompt: additional
          ? index === 0
            ? "还需要多少组相互矛盾的记录，才能支撑核心推理？"
            : "次要证人应该各自承担线索，还是合并为更少角色？"
          : question.prompt,
        required: additional ? false : question.required,
      }));
      currentQuestions = [...currentQuestions, ...nextQuestions];
      return {
        ...common,
        result: {
          input_hash: "h",
          questions: nextQuestions,
          stale: false,
        },
      };
    }
    if (taskType === "brief_intake_synthesize") {
      const reasoningAnswer =
        currentQuestions.find((q) => q.question_key === "reasoning_goal")
          ?.answer_text ?? "";
      const content: BriefIntakeCandidateContent = {
        concept:
          source?.content_text
            .split(/\r?\n/u)
            .map((line) => line.trim())
            .find(Boolean)
            ?.slice(0, 1000) ?? "未命名概念",
        core_selling_points: ["相互印证却共同失真的档案"],
        content_outline: ["发现不存在的时间段", "比对三份独立记录"],
        reasoning_goal:
          reasoningAnswer || "找出是谁制造了那段不存在的时间，以及这样做的目的。",
        resolution_mode: "agent_proposed",
        author_answer: null,
        constraints: [],
        pending_decisions: [],
        scope_estimate: "4 名核心角色 / 7 个场景 / 90 分钟",
        risk_notes: ["需要避免让记忆改写成为无法验证的万能解释。"],
        field_sources: {
          concept: "user_original",
          core_selling_points: "agent_suggestion",
          content_outline: "agent_suggestion",
          reasoning_goal: "agent_suggestion",
          resolution_mode: "user_confirmed",
          author_answer: "unresolved",
          constraints: "unresolved",
          scope_estimate: "agent_suggestion",
          risk_notes: "agent_suggestion",
        },
      };
      const candidateId = candidates.length + 1;
      candidates = [
        {
          candidate_id: candidateId,
          parent_candidate_id: null,
          generated_by_task_run_id: taskRunId,
          origin: "agent_synthesis",
          basis_input_hash: "b",
          content_hash: `c${candidateId}`,
          content,
          is_current: true,
          is_adopted: false,
          is_saved: false,
          is_stale: false,
          can_activate: true,
          saved_at: null,
          created_at: new Date().toISOString(),
        },
        ...candidates,
      ];
      currentCandidateId = candidateId;
      return {
        ...common,
        result: {
          input_hash: "h",
          candidate_id: candidateId,
          content_hash: `c${candidateId}`,
          origin: "agent_synthesis",
          stale: false,
        },
      };
    }
    if (taskType === "brief_anchor_extract") {
      return {
        ...common,
        result: {
          input_hash: "h",
          author_anchors: [],
          creative_constraints: [],
          warnings: [],
        },
      };
    }
    const titles = ["缺页校准稿", "封存室夜班稿", "第七码互证稿"];
    const index = draftCandidates.length;
    const summary: DraftCandidateView = {
      task_run_id: taskRunId,
      brief_version_no: versionNo,
      is_current_brief: true,
      is_current: false,
      is_adopted: false,
      can_adopt: true,
      provider: "openai",
      model_id: "gpt-5.6-sol",
      attempt_count: 1,
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      title: titles[index % titles.length],
      content_hash: `d${taskRunId}`,
      object_counts: {
        entities: 4,
        events: 3,
        information_units: 2,
        reasoning_paths: 1,
      },
      reasoning_questions: ["三份可靠记录为何会共同证明一段不存在的时间？"],
      constraint_statements: [],
    };
    draftCandidates = [...draftCandidates, summary];
    return {
      ...common,
      result: {
        title: summary.title,
        content_hash: summary.content_hash,
        object_counts: summary.object_counts,
        reasoning_questions: summary.reasoning_questions,
        constraint_statements: summary.constraint_statements,
      },
    };
  }

  function recordTask(taskType: string, provider = "openai"): TaskView {
    const taskRunId = ++taskSeq;
    taskTypes.set(taskRunId, taskType);
    taskProviders.set(taskRunId, provider);
    return {
      ...baseTask(taskRunId),
      task_type: taskType as TaskView["task_type"],
      provider: provider as TaskView["provider"],
    };
  }

  class CaseSessionError extends Error {
    constructor(
      message: string,
      readonly failureCode?: string | null,
    ) {
      super(message);
    }
  }

  function isAuthFailure(error: unknown): boolean {
    return (
      error instanceof CaseSessionError &&
      error.failureCode === "provider_authentication_failed"
    );
  }

  return {
    CaseSessionError,
    setConfiguredProviders: (providers: string[]) => {
      configuredProviders = providers;
    },
    setFailOpenaiAuth: (value: boolean) => {
      failOpenaiAuth = value;
    },
    getGenerationDraftRevisions: () => generationDraftRevisions,
    getAdoptionDraftRevisions: () => adoptionDraftRevisions,
    listConfiguredProviders: async () => configuredProviders,
    isProviderAuthFailure: isAuthFailure,
    runTaskWithProviderFallback: async (operation: (provider: string) => Promise<unknown>) => {
      let lastError: unknown = null;
      for (const provider of configuredProviders) {
        try {
          return { provider, result: await operation(provider) };
        } catch (error) {
          lastError = error;
          if (!isAuthFailure(error)) throw error;
        }
      }
      throw lastError;
    },
    createCaseProject: async () => ({
      id: 1,
      title: "测试项目",
      description: null,
      profile: {},
      draft: { id: 1, revision: 1, schema_version: "v1", status: "open" },
    }),
    fetchCaseIntake: async () => intakeView(),
    persistCaseSource: async (
      _projectId: number,
      _intakeRevision: number,
      text: string,
      parentSourceRecordId: number | null = null,
    ) => {
      sourceSeq += 1;
      revision += 1;
      source = {
        source_record_id: sourceSeq,
        source_kind: parentSourceRecordId ? "human_revision" : "human_original",
        content_text: text,
        content_hash: `s${sourceSeq}`,
        parent_source_record_id: parentSourceRecordId,
        generated_by_task_run_id: null,
        created_at: new Date().toISOString(),
      };
      return intakeView();
    },
    startPolishTask: async () => recordTask("brief_polish"),
    startQuestionsTask: async (
      _projectId: number,
      expectedRevision: number,
      provider: string,
    ) => {
      // 与后端一致：任务创建校验并推进 intake revision。
      if (expectedRevision !== revision) {
        throw new Error("Brief Intake revision is stale");
      }
      revision += 1;
      return recordTask("brief_intake_questions", provider);
    },
    startSynthesizeTask: async (
      _projectId: number,
      expectedRevision: number,
      provider: string,
    ) => {
      if (expectedRevision !== revision) {
        throw new Error("Brief Intake revision is stale");
      }
      revision += 1;
      return recordTask("brief_intake_synthesize", provider);
    },
    startAnchorExtractTask: async () => recordTask("brief_anchor_extract"),
    fetchCaseDraft: async (): Promise<DraftView> => ({
      project_id: 1,
      revision: caseDraftRevision,
      schema_version: "v1",
      status: "open",
      content: null,
    }),
    startDraftGenerationTask: async (
      _projectId: number,
      _briefVersionId: number,
      expectedDraftRevision: number,
    ) => {
      generationDraftRevisions.push(expectedDraftRevision);
      if (expectedDraftRevision !== caseDraftRevision) {
        throw new Error("CaseFile Draft revision is stale");
      }
      return recordTask("brief_to_draft");
    },
    fetchTask: async (_projectId: number, taskRunId: number) =>
      terminalTask(taskRunId),
    waitForTask: async (_projectId: number, taskRunId: number) =>
      terminalTask(taskRunId),
    answerQuestion: async (
      _projectId: number,
      _intakeRevision: number,
      questionKey: string,
      answer: { mode: string; text?: string; suggestionIndex?: number },
    ) => {
      revision += 1;
      currentQuestions = currentQuestions.map((question) => {
        if (question.question_key !== questionKey) return question;
        if (answer.mode === "pending") {
          return {
            ...question,
            answer_status: "pending",
            answer_text: null,
            answer_source: null,
          };
        }
        if (answer.mode === "suggestion") {
          return {
            ...question,
            answer_status: "suggestion_accepted",
            answer_text:
              question.suggestions[answer.suggestionIndex ?? 0] ?? "",
            answer_source: "agent_suggestion",
          };
        }
        return {
          ...question,
          answer_status: "user_answered",
          answer_text: answer.text ?? "",
          answer_source: "user_confirmed",
        };
      });
      return intakeView();
    },
    createBriefCandidate: async (
      _projectId: number,
      _intakeRevision: number,
      content: BriefIntakeCandidateContent,
      parentCandidateId: number | null = null,
    ) => {
      const candidateId = candidates.length + 1;
      candidates = [
        {
          candidate_id: candidateId,
          parent_candidate_id: parentCandidateId,
          generated_by_task_run_id: null,
          origin: "manual_edit",
          basis_input_hash: "b",
          content_hash: `c${candidateId}`,
          content,
          is_current: true,
          is_adopted: false,
          is_saved: false,
          is_stale: false,
          can_activate: true,
          saved_at: null,
          created_at: new Date().toISOString(),
        },
        ...candidates,
      ];
      currentCandidateId = candidateId;
      return intakeView();
    },
    saveBriefCandidate: async () => intakeView(),
    activateBriefCandidate: async (
      _projectId: number,
      _intakeRevision: number,
      candidateId: number,
    ) => {
      currentCandidateId = candidateId;
      return intakeView();
    },
    adoptBriefCandidate: async (
      _projectId: number,
      _intakeRevision: number,
      candidateId: number,
    ) => {
      briefRevision += 1;
      const candidate = candidates.find(
        (item) => item.candidate_id === candidateId,
      );
      if (candidate) {
        briefContent = {
          source_record_ids: [],
          creative_intent: candidate.content.concept,
          reasoning_proposition: candidate.content.reasoning_goal,
          resolution_mode: candidate.content.resolution_mode,
          author_answer: candidate.content.author_answer ?? null,
          author_anchors: [],
          boundary_text: null,
          creative_constraints: [],
          core_selling_points: candidate.content.core_selling_points,
          content_outline: candidate.content.content_outline,
          scope_estimate: candidate.content.scope_estimate,
          risk_notes: candidate.content.risk_notes,
        };
      }
      return { intake: intakeView(), brief: briefView() };
    },
    fetchBrief: async () => briefView(),
    updateBrief: async (
      _projectId: number,
      _expectedRevision: number,
      content: BriefContent,
    ) => {
      briefRevision += 1;
      briefContent = content;
      return briefView();
    },
    confirmBrief: async () => {
      versionNo += 1;
      briefRevision += 1;
      briefVersionId = versionNo;
      return {
        brief_version_id: versionNo,
        version_no: versionNo,
        content: briefContent ?? ({} as never),
      };
    },
    fetchDraftCandidates: async () => draftCandidates,
    adoptDraftCandidate: async (
      _projectId: number,
      taskRunId: number,
      expectedDraftRevision: number,
    ) => {
      adoptionDraftRevisions.push(expectedDraftRevision);
      if (expectedDraftRevision !== caseDraftRevision) {
        throw new Error("CaseFile Draft revision is stale");
      }
      draftCandidates = draftCandidates.map((candidate) =>
        candidate.task_run_id === taskRunId
          ? { ...candidate, is_adopted: true, is_current: true }
          : candidate,
      );
      return { task_run_id: taskRunId, adopted: true as const };
    },
  };
}

const fake = vi.hoisted(() => ({ backend: buildFakeBackend() }));

vi.mock("@/features/case-session/case-session-api", () => fake.backend);

function renderIntake() {
  return render(
    <CaseSessionProvider>
      <IntakeCenter />
    </CaseSessionProvider>,
  );
}

async function flush() {
  await act(async () => {});
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  routerPush.mockReset();
  fake.backend.setConfiguredProviders(["openai"]);
  fake.backend.setFailOpenaiAuth(false);
});

describe("intake center", () => {
  it("keeps the real A-path functions in the official intake surface", () => {
    renderIntake();

    expect(
      screen.getByRole("heading", {
        name: "把念头照亮，留下可追溯的起案依据。",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /我有一个想法/u })).toBeEnabled();
    expect(screen.getByRole("button", { name: /帮我想一个/u })).toBeDisabled();
    expect(
      screen.getByRole("radio", { name: /表达优化/u }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /叙事增强/u }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("写下最初想法")).toHaveValue("");
    expect(screen.getByText("实时简报映射")).toBeInTheDocument();
  });

  it("runs the full A path against the real intake backend and generates three draft candidates", async () => {
    renderIntake();

    fireEvent.click(screen.getByRole("button", { name: "载入示例" }));
    fireEvent.click(screen.getByRole("radio", { name: /叙事增强/u }));
    fireEvent.click(
      screen.getByRole("button", { name: /生成润色校样/u }),
    );
    await flush();

    expect(
      screen.getByRole("heading", { name: "逐字确认 Agent 改了什么。" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("当前作者原稿")).toHaveAttribute("readonly");
    expect(
      (screen.getByLabelText("编辑 Agent 润色工作稿") as HTMLTextAreaElement)
        .value,
    ).toContain("深夜");
    expect(screen.getByText("新增细节审阅")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /采用这版校样/u }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));

    expect(
      screen.getByRole("heading", { name: "只问会改变方向的问题。" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Agent 正在思考" }),
    ).toBeInTheDocument();

    await flush();

    expect(
      screen.getByRole("heading", { name: "只问会改变方向的问题。" }),
    ).toBeInTheDocument();
    const generateBrief = screen.getByRole("button", {
      name: /形成创作简报/u,
    });
    expect(generateBrief).toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "稍后决定" }));
    expect(generateBrief).toBeEnabled();
    fireEvent.click(generateBrief);

    expect(
      screen.getByRole("heading", {
        name: "确认整体方向，再交给正式审阅。",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Agent 正在整理创作简报" }),
    ).toBeInTheDocument();

    await flush();

    expect(
      screen.getByRole("heading", {
        name: "确认整体方向，再交给正式审阅。",
      }),
    ).toBeInTheDocument();
    const returnToQuestions = screen.getByRole("button", {
      name: "← 返回追问",
    });
    expect(returnToQuestions).toBeInTheDocument();
    expect(returnToQuestions.closest("header")).not.toBeNull();
    expect(
      (screen.getByLabelText("一句话概念") as HTMLTextAreaElement).value,
    ).toContain("档案修复师");
    expect(screen.getByLabelText("推理目标")).toHaveValue(
      "找出是谁伪造了那段不存在的时间。",
    );
    expect(screen.getAllByLabelText(/核心卖点第 \d+ 项/u)).toHaveLength(3);
    expect(screen.getByLabelText("核心卖点第 1 项")).toHaveValue(
      "相互印证却共同失真的档案",
    );
    expect(screen.getAllByLabelText(/阶段 \d+ 名称/u)).toHaveLength(4);
    expect(screen.getByLabelText("阶段 1 描述")).toHaveValue(
      "发现不存在的时间段",
    );
    fireEvent.click(screen.getByRole("button", { name: /添加一条卖点/u }));
    expect(screen.getAllByLabelText(/核心卖点第 \d+ 项/u)).toHaveLength(4);
    fireEvent.change(screen.getByLabelText("核心卖点第 2 项"), {
      target: { value: "记忆改写留下可交叉验证的痕迹" },
    });
    expect(screen.getByLabelText("核心卖点第 2 项")).toHaveValue(
      "记忆改写留下可交叉验证的痕迹",
    );
    const conceptField = screen.getByLabelText("一句话概念").closest("section");
    const briefFields = Array.from(conceptField?.parentElement?.children ?? []);
    expect(
      briefFields.slice(0, 2).map((field) =>
        field.querySelector("header label")?.textContent,
      ),
    ).toEqual(["一句话概念*", "推理目标*"]);
    expect(
      briefFields.slice(0, 2).every((field) =>
        field.matches('[data-required="true"]') &&
        field.querySelector("header label > em")?.textContent === "*",
      ),
    ).toBe(true);
    expect(screen.getByText("约束抽屉")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /进入创作简报审阅/u }),
    );
    await flush();

    expect(
      screen.getByRole("heading", { name: "把生成依据逐条钉在纸面上。" }),
    ).toBeInTheDocument();
    // 采用候选时简报已在服务端保存，审阅可直接冻结。
    const freeze = screen.getByRole("button", { name: /确认并冻结/u });
    expect(freeze).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "保存审阅" }));
    await flush();
    fireEvent.click(freeze);
    await flush();

    expect(
      screen.getByRole("heading", { name: "让三种创作策略同时摊开。" }),
    ).toBeInTheDocument();
    expect(screen.getByText("候选卷尚空")).toBeInTheDocument();
    const generateCandidateActions = screen.getAllByRole("button", {
      name: /生成三份候选/u,
    });
    expect(generateCandidateActions).toHaveLength(2);
    fireEvent.click(generateCandidateActions[1]);
    await flush();

    expect(fake.backend.getGenerationDraftRevisions().slice(-3)).toEqual([
      17, 17, 17,
    ]);

    expect(screen.getByRole("button", { name: /缺页校准稿/u })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /封存室夜班稿/u })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /第七码互证稿/u })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /缺页校准稿/u }));
    fireEvent.click(screen.getByRole("button", { name: "预览工作台" }));
    expect(routerPush).toHaveBeenCalledWith("/workbench");
    fireEvent.click(
      screen.getByRole("button", { name: /采用为当前工作稿/u }),
    );
    await flush();
    expect(fake.backend.getAdoptionDraftRevisions().at(-1)).toBe(17);
    expect(
      screen.getByRole("button", { name: /已是当前工作稿/u }),
    ).toBeInTheDocument();
  });

  it("falls back to the next provider and retries with a fresh intake revision when questions auth fails", async () => {
    fake.backend.setConfiguredProviders(["openai", "deepseek"]);
    fake.backend.setFailOpenaiAuth(true);
    renderIntake();

    fireEvent.click(screen.getByRole("button", { name: "载入示例" }));
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();

    // openai 认证失败后回退 deepseek 重试，且重试使用任务创建后推进的最新 revision。
    expect(
      screen.getByRole("heading", { name: "只问会改变方向的问题。" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("玩家最终必须回答哪一个问题？"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /形成创作简报/u })).toBeEnabled();

    fireEvent.click(
      screen.getByRole("button", { name: "再生成一些问题" }),
    );
    expect(
      screen.getByRole("status", { name: "Agent 正在继续研查" }),
    ).toBeInTheDocument();
    await flush();

    expect(
      screen.getByText("还需要多少组相互矛盾的记录，才能支撑核心推理？"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("必须回答")).toHaveLength(1);
    expect(screen.getAllByText("可以暂缓")).toHaveLength(3);
  });

  it("keeps the official intake on the real backend without browser persistence", () => {
    const feature = readFileSync(
      resolve(
        process.cwd(),
        "features/intake/intake-center.tsx",
      ),
      "utf8",
    );
    const model = readFileSync(
      resolve(
        process.cwd(),
        "features/intake/intake-model.ts",
      ),
      "utf8",
    );
    const route = readFileSync(
      resolve(process.cwd(), "app/page.tsx"),
      "utf8",
    );
    const shell = readFileSync(
      resolve(process.cwd(), "components/product-shell.tsx"),
      "utf8",
    );
    const globalCss = readFileSync(
      resolve(process.cwd(), "app/globals.css"),
      "utf8",
    );
    const provider = readFileSync(
      resolve(
        process.cwd(),
        "features/case-session/case-session-provider.tsx",
      ),
      "utf8",
    );
    const api = readFileSync(
      resolve(
        process.cwd(),
        "features/case-session/case-session-api.ts",
      ),
      "utf8",
    );

    [feature, model, route].forEach((source) => {
      expect(source).not.toContain("@/lib/api-client");
      expect(source).not.toContain("@/store/workflow-store");
      expect(source).not.toContain("localStorage");
      expect(source).not.toContain("sessionStorage");
      expect(source).not.toMatch(/\bfetch\s*\(/u);
    });
    [provider].forEach((source) => {
      expect(source).not.toContain("@/store/workflow-store");
      expect(source).not.toContain("localStorage");
      expect(source).not.toContain("sessionStorage");
    });
    expect(provider).toContain("./case-session-api");
    expect(api).toContain("@/lib/api-client");
    expect(route).toContain("@/features/intake/intake-center");
    expect(shell).toContain('"intake-center-v1"');
    expect(shell).toContain("<CaseSessionProvider>");
    expect(shell).toContain("SettingsDialog");
    expect(shell).toContain("data-casefile-kind");
    expect(globalCss).toContain("min-width: 0");
  });
});
