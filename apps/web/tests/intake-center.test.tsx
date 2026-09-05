import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
  ProjectView,
  TaskView,
} from "@/lib/api-client";

import { CaseSessionProvider } from "@/features/case-session/case-session-provider";
import { IntakeCenter } from "@/features/intake/intake-center";
import * as sessionApi from "@/features/case-session/case-session-api";

const { routerPush } = vi.hoisted(() => ({ routerPush: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

function buildFakeBackend() {
  const defaultQuestions: BriefIntakeQuestionView[] = [
    {
      question_key: "reasoning_goal",
      ordinal: 1,
      prompt: "作品最终要回答哪一个核心问题？",
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
  let questionBatch: BriefIntakeQuestionView[] = defaultQuestions;

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
  let formalBriefReview = false;
  let synthesizeBaseCandidateId: number | null = null;
  let synthesizeInstruction: string | null = null;
  const caseDraftRevision = 17;
  const caseDraftId = 71;
  let draftCandidates: DraftCandidateView[] = [];
  let taskSeq = 100;
  const taskTypes = new Map<number, string>();
  const taskProviders = new Map<number, string>();
  let configuredProviders = ["openai"];
  let failOpenaiAuth = false;
  let failNextAnchorExtract = false;
  let failNextQuestionRevision = false;
  let failNextQuestionGeneration = false;
  let resolutionNeedsConfirmation = false;
  let failNextBriefConfirmation = false;
  const generationDraftRevisions: number[] = [];
  const generationDraftIds: number[] = [];
  const adoptionCurrentDraftIds: number[] = [];
  let failNextDraftAdoption = false;
  let draftAdoptionGate: Promise<void> | null = null;
  let projects: ProjectView[] = [
    {
      id: 1,
      title: "测试项目",
      description: null,
      profile: {},
      status: "active",
      archived_at: null,
      created_at: new Date(Date.now() - 86400000).toISOString(),
      updated_at: new Date().toISOString(),
      casefile_id: 1,
      current_draft_id: 1,
      draft: { id: 1, title: "测试项目", revision: 1, schema_version: "v1", status: "active" },
    },
    {
      id: 2,
      title: "午夜回航旧案",
      description: null,
      profile: {},
      status: "active",
      archived_at: null,
      created_at: new Date(Date.now() - 172800000).toISOString(),
      updated_at: new Date(Date.now() - 3600000).toISOString(),
      casefile_id: 2,
      current_draft_id: 2,
      draft: { id: 2, title: "午夜回航旧案", revision: 1, schema_version: "v1", status: "active" },
    },
    {
      id: 3,
      title: "封存的旧卷",
      description: null,
      profile: {},
      status: "archived",
      archived_at: new Date(Date.now() - 3600000).toISOString(),
      created_at: new Date(Date.now() - 259200000).toISOString(),
      updated_at: new Date(Date.now() - 3600000).toISOString(),
      casefile_id: 3,
      current_draft_id: 3,
      draft: { id: 3, title: "封存的旧卷", revision: 1, schema_version: "v1", status: "active" },
    },
  ];

  function intakeView(): BriefIntakeView {
    const stage =
      formalBriefReview
        ? "brief_review"
        : currentCandidateId !== null
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
    candidate_strategy: null,
    component_steps: [],
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
      const nextQuestions = questionBatch.map((question, index) => ({
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
        suggestions: additional
          ? index === 0
            ? ["两组即可，重点验证来源独立。", "三组以上，形成更强的交叉验证。"]
            : ["各自承担一条独立线索。", "合并角色，减少叙事负担。"]
          : question.suggestions,
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
      let content: BriefIntakeCandidateContent = {
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
        conclusion_mode: "undetermined",
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
          resolution_mode: resolutionNeedsConfirmation
            ? "agent_suggestion"
            : "user_confirmed",
          conclusion_mode: "agent_suggestion",
          author_answer: "unresolved",
          constraints: "unresolved",
          scope_estimate: "agent_suggestion",
          risk_notes: "agent_suggestion",
        },
      };
      const dialogueBase = candidates.find(
        (candidate) => candidate.candidate_id === synthesizeBaseCandidateId,
      );
      if (dialogueBase && synthesizeInstruction) {
        content = {
          ...dialogueBase.content,
          content_outline: [
            ...dialogueBase.content.content_outline,
            "在封存前完成最终验证",
          ],
          field_sources: {
            ...dialogueBase.content.field_sources,
            content_outline: "agent_suggestion",
          },
        };
      }
      const candidateId = candidates.length + 1;
      candidates = [
        {
          candidate_id: candidateId,
          parent_candidate_id: dialogueBase?.candidate_id ?? null,
          generated_by_task_run_id: taskRunId,
          origin: dialogueBase ? "dialogue_revision" : "agent_synthesis",
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
          origin: dialogueBase ? "dialogue_revision" : "agent_synthesis",
          stale: false,
        },
      };
    }
    if (taskType === "brief_anchor_extract") {
      if (failNextAnchorExtract) {
        failNextAnchorExtract = false;
        throw new Error("作者答案候选生成接口不兼容，请重启本地服务后重试。");
      }
      return {
        ...common,
        result: {
          input_hash: "h",
          suggested_author_answer: "真正的发送者来自未来，并利用求救信号改写当前记录。",
          author_anchors: [{ statement: "真正的发送者来自未来。" }],
          creative_constraints: [],
          warnings: [],
        },
      };
    }
    if (taskType === "brief_strategy_options") {
      return {
        ...common,
        result: {
          input_hash: "h",
          strategy_version: "candidate-strategy-v1",
          options: [
            {
              strategy: "structure_first",
              direction: "先建立事件、对象和因果骨架。",
              focus: "让结构首先清晰可审阅",
              strengths: ["结构稳定", "符合boundary_text中的创作边界"],
              tradeoffs: ["氛围细节稍后深化"],
              brief_fit: "直接对应Brief中的content_outline，并满足boundary_text中的要求。",
            },
            {
              strategy: "atmosphere_first",
              direction: "先建立场景质感与人物张力。",
              focus: "让氛围成为线索载体",
              strengths: ["场景鲜明", "呼应core_selling_points"],
              tradeoffs: ["需要继续核对推理密度"],
              brief_fit: "引用creative_intent中的封存室与夜班设定。",
            },
            {
              strategy: "reasoning_first",
              direction: "先建立证据、反证和解答链。",
              focus: "让核心命题可验证",
              strengths: ["证据链清晰", "符合risk_notes"],
              tradeoffs: ["场景铺陈稍后深化"],
              brief_fit: "直接围绕reasoning_proposition展开。",
            },
          ],
          recommended_strategy: "reasoning_first",
          recommendation_reason: "reasoning_proposition适合先建立证据闭环。",
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
      candidate_strategy_attempt: 1,
      attempt_count: 1,
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      candidate_strategy: ([
        "structure_first",
        "atmosphere_first",
        "reasoning_first",
      ][index % 3]) as DraftCandidateView["candidate_strategy"],
      candidate_strategy_version: "candidate-strategy-v1",
      candidate_strategy_label: ["结构优先", "氛围优先", "推理优先"][index % 3],
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
        candidate_strategy: summary.candidate_strategy,
        candidate_strategy_version: summary.candidate_strategy_version,
        candidate_strategy_label: summary.candidate_strategy_label,
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

  function isRevisionConflict(error: unknown): boolean {
    return (
      error instanceof CaseSessionError &&
      error.failureCode === "brief_intake_revision_conflict"
    );
  }

  return {
    CaseSessionError,
    resetProjects: () => {
      formalBriefReview = false;
      synthesizeBaseCandidateId = null;
      synthesizeInstruction = null;
      projects = projects.map((project) =>
        project.id === 3
          ? {
              ...project,
              status: "archived",
              archived_at: new Date(Date.now() - 3600000).toISOString(),
            }
          : { ...project, status: "active", archived_at: null },
      );
    },
    setConfiguredProviders: (providers: string[]) => {
      configuredProviders = providers;
    },
    setFailOpenaiAuth: (value: boolean) => {
      failOpenaiAuth = value;
    },
    setFailNextAnchorExtract: (value: boolean) => {
      failNextAnchorExtract = value;
    },
    setFailNextQuestionRevision: (value: boolean) => {
      failNextQuestionRevision = value;
    },
    setFailNextQuestionGeneration: (value: boolean) => {
      failNextQuestionGeneration = value;
    },
    setQuestionBatch: (value: BriefIntakeQuestionView[]) => {
      questionBatch = value.map((question, index) => ({
        ...question,
        ordinal: index + 1,
      }));
    },
    resetAll: () => {
      revision = 1;
      sourceSeq = 0;
      source = null;
      currentQuestions = [];
      candidates = [];
      currentCandidateId = null;
      briefRevision = 1;
      briefVersionId = null;
      versionNo = 0;
      briefContent = null;
      formalBriefReview = false;
      draftCandidates = [];
      taskSeq = 100;
      taskTypes.clear();
      taskProviders.clear();
      questionBatch = defaultQuestions;
      configuredProviders = ["openai"];
      failOpenaiAuth = false;
      failNextAnchorExtract = false;
      failNextQuestionRevision = false;
      failNextQuestionGeneration = false;
      resolutionNeedsConfirmation = false;
      failNextBriefConfirmation = false;
      failNextDraftAdoption = false;
      draftAdoptionGate = null;
      generationDraftRevisions.length = 0;
      generationDraftIds.length = 0;
      adoptionCurrentDraftIds.length = 0;
    },
    setFailNextDraftAdoption: (value: boolean) => {
      failNextDraftAdoption = value;
    },
    setResolutionNeedsConfirmation: (value: boolean) => {
      resolutionNeedsConfirmation = value;
    },
    setFailNextBriefConfirmation: (value: boolean) => {
      failNextBriefConfirmation = value;
    },
    markFormalBriefReview: () => {
      formalBriefReview = true;
      revision += 1;
    },
    setDraftAdoptionGate: (gate: Promise<void> | null) => {
      draftAdoptionGate = gate;
    },
    getGenerationDraftRevisions: () => generationDraftRevisions,
    getGenerationDraftIds: () => generationDraftIds,
    getAdoptionCurrentDraftIds: () => adoptionCurrentDraftIds,
    listConfiguredProviders: async () => configuredProviders,
    isProviderAuthFailure: isAuthFailure,
    isBriefIntakeRevisionConflict: isRevisionConflict,
    isBriefConfirmationRevisionConflict: isRevisionConflict,
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
      status: "active",
      archived_at: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      casefile_id: 1,
      draft: { id: 1, revision: 1, schema_version: "v1", status: "open" },
    }),
    fetchIdeas: async (projectId: number) => ({ project_id: projectId, batches: {} }),
    generateIdeas: async (projectId: number) => ({
      project_id: projectId, batch_id: "test-batch", ideas: [],
    }),
    fetchCaseIntake: async (projectId: number) => {
      // 历史项目各自拥有独立的 intake 状态；当前项目复用全局会话状态。
      if (projectId === 1) return intakeView();
      return {
        ...intakeView(),
        project_id: projectId,
        stage: "idea" as const,
        current_source: null,
        questions: [],
        current_candidate_id: null,
        adopted_candidate_id: null,
        candidates: [],
        pending_decisions: [],
        brief: {
          brief_id: projectId,
          draft_revision: 1,
          current_version_id: null,
          has_content: false,
        },
      };
    },
    beginBriefRevision: vi.fn(async (projectId: number) => {
      if (projectId === 1) {
        formalBriefReview = false;
        revision += 1;
      }
      return intakeView();
    }),
    listProjects: async () => projects,
    archiveProject: async (_actorId: number, projectId: number) => {
      projects = projects.map((project) =>
        project.id === projectId
          ? {
              ...project,
              status: "archived",
              archived_at: new Date().toISOString(),
            }
          : project,
      );
      return projects.find((project) => project.id === projectId)!;
    },
    unarchiveProject: async (_actorId: number, projectId: number) => {
      projects = projects.map((project) =>
        project.id === projectId
          ? { ...project, status: "active", archived_at: null }
          : project,
      );
      return projects.find((project) => project.id === projectId)!;
    },
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
      if (failNextQuestionGeneration) {
        failNextQuestionGeneration = false;
        throw new Error("追问服务暂不可用，请返回原稿后重试。");
      }
      if (failNextQuestionRevision) {
        failNextQuestionRevision = false;
        revision += 1;
        throw new CaseSessionError(
          "Brief Intake revision is stale",
          "brief_intake_revision_conflict",
        );
      }
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
      baseCandidateId: number | null = null,
      instruction: string | null = null,
    ) => {
      if (expectedRevision !== revision) {
        throw new Error("Brief Intake revision is stale");
      }
      synthesizeBaseCandidateId = baseCandidateId;
      synthesizeInstruction = instruction;
      revision += 1;
      return recordTask("brief_intake_synthesize", provider);
    },
    startAnchorExtractTask: async () => recordTask("brief_anchor_extract"),
    startStrategyOptionsTask: async () => recordTask("brief_strategy_options"),
    strategyOptionsResult: (task: TaskView) => task.result,
    fetchCaseDraft: async (): Promise<DraftView> => ({
      project_id: 1,
      casefile_id: 1,
      draft_id: caseDraftId,
      title: "测试工作稿",
      revision: caseDraftRevision,
      schema_version: "v1",
      status: "active",
      document_status: "draft",
      brief_version_id: null,
      created_at: new Date(Date.now() - 3600000).toISOString(),
      updated_at: new Date().toISOString(),
      content: null,
    }),
    startDraftGenerationTask: async (
      _projectId: number,
      _briefVersionId: number,
      expectedDraftId: number,
      expectedDraftRevision: number,
    ) => {
      generationDraftIds.push(expectedDraftId);
      generationDraftRevisions.push(expectedDraftRevision);
      if (
        expectedDraftId !== caseDraftId ||
        expectedDraftRevision !== caseDraftRevision
      ) {
        throw new Error("CaseFile Draft revision is stale");
      }
      return recordTask("brief_to_draft");
    },
    fetchTask: async (_projectId: number, taskRunId: number) =>
      terminalTask(taskRunId),
    waitForTask: async (
      _projectId: number,
      taskRunId: number,
      onTick?: (task: TaskView) => void,
    ) => {
      const taskType = (taskTypes.get(taskRunId) ?? "brief_to_draft") as TaskView["task_type"];
      for (const stage of ["planning", "generating", "validating"] as const) {
        onTick?.({
          ...baseTask(taskRunId),
          task_type: taskType,
          status: "running",
          stage,
        });
        await Promise.resolve();
      }
      onTick?.({
        ...baseTask(taskRunId),
        task_type: taskType,
        status: "succeeded",
        stage: "completed",
      });
      return terminalTask(taskRunId);
    },
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
      intakeRevision: number,
      content: BriefIntakeCandidateContent,
      parentCandidateId: number | null = null,
    ) => {
      if (intakeRevision !== revision) {
        throw new CaseSessionError(
          "Brief Intake revision is stale",
          "brief_intake_revision_conflict",
        );
      }
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
      formalBriefReview = true;
      revision += 1;
      const candidate = candidates.find(
        (item) => item.candidate_id === candidateId,
      );
      if (candidate) {
        briefContent = {
          source_record_ids: [],
          creative_intent: candidate.content.concept,
          reasoning_proposition: candidate.content.reasoning_goal,
          resolution_mode: candidate.content.resolution_mode,
          conclusion_mode: candidate.content.conclusion_mode,
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
    confirmBrief: vi.fn(async () => {
      if (failNextBriefConfirmation) {
        failNextBriefConfirmation = false;
        throw new Error("建案确认服务暂不可用。");
      }
      versionNo += 1;
      briefRevision += 1;
      briefVersionId = versionNo;
      return {
        brief_version_id: versionNo,
        version_no: versionNo,
        content: briefContent ?? ({} as never),
      };
    }),
    fetchDraftCandidates: async () => draftCandidates,
    adoptDraftCandidateWithReconciliation: async (
      _projectId: number,
      taskRunId: number,
      expectedCurrentDraftId: number,
    ) => {
      adoptionCurrentDraftIds.push(expectedCurrentDraftId);
      const gate = draftAdoptionGate;
      if (gate) {
        draftAdoptionGate = null;
        await gate;
      }
      if (failNextDraftAdoption) {
        failNextDraftAdoption = false;
        throw new Error("候选采用服务暂不可用。");
      }
      if (expectedCurrentDraftId !== caseDraftId) {
        throw new Error("Current Draft changed");
      }
      draftCandidates = draftCandidates.map((candidate) =>
        candidate.task_run_id === taskRunId
          ? { ...candidate, is_adopted: true, is_current: true }
          : candidate,
      );
      return {
        adoption: {
          task_run_id: taskRunId,
          draft_id: caseDraftId,
          revision: caseDraftRevision,
          title: "测试工作稿",
          content_hash: "a".repeat(64),
          adopted: true as const,
        },
        facts: null,
        error: null,
      };
    },
  };
}

const fake = vi.hoisted(() => ({ backend: buildFakeBackend() }));

vi.mock("@/features/case-session/case-session-api", () => fake.backend);

vi.mock("@/lib/api-client", () => ({
  listProjects: fake.backend.listProjects,
  archiveProject: fake.backend.archiveProject,
  unarchiveProject: fake.backend.unarchiveProject,
}));

function renderLanding() {
  return render(
    <CaseSessionProvider>
      <IntakeCenter />
    </CaseSessionProvider>,
  );
}

function renderIntake() {
  const view = renderLanding();
  const pathA = screen.queryByRole("button", { name: /我有一个想法/u });
  if (pathA) fireEvent.click(pathA);
  return view;
}

async function flush() {
  await act(async () => {});
}

async function reachBriefWithoutQuestions() {
  fake.backend.setQuestionBatch([]);
  renderIntake();
  fireEvent.change(screen.getByLabelText("写下最初想法"), {
    target: { value: "一名档案员发现三份可靠记录指向不存在的时间。" },
  });
  fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
  await flush();
  fireEvent.click(screen.getByRole("button", { name: /形成创作简报/u }));
  await flush();
  fireEvent.click(screen.getByRole("radio", { name: /唯一解/u }));
}

async function reachBriefWithQuestions() {
  renderIntake();
  fireEvent.change(screen.getByLabelText("写下最初想法"), {
    target: { value: "一名档案员发现三份可靠记录指向不存在的时间。" },
  });
  fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
  await flush();
  fireEvent.click(
    screen.getByRole("radio", {
      name: "找出是谁伪造了那段不存在的时间。",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
  fireEvent.click(screen.getByRole("button", { name: "稍后决定" }));
  fireEvent.click(screen.getByRole("button", { name: /形成创作简报/u }));
  await flush();
}

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
  vi.useRealTimers();
  routerPush.mockReset();
  fake.backend.resetAll();
  fake.backend.resetProjects();
});

describe("intake center", () => {
  it("keeps new idea generation pending when an old session's generation fails", async () => {
    type Result = Awaited<ReturnType<typeof sessionApi.generateIdeas>>;
    let fail!: (error: Error) => void, complete!: (result: Result) => void;
    const old = new Promise<Result>((_resolve, reject) => { fail = reject; });
    const current = new Promise<Result>((resolve) => { complete = resolve; });
    const original = sessionApi.generateIdeas;
    const spy = vi.spyOn(sessionApi, "generateIdeas").mockReturnValueOnce(old).mockReturnValueOnce(current);
    try {
      renderLanding();
      fireEvent.click(screen.getByRole("button", { name: /帮我想一个/u }));
      await flush();
      fireEvent.click(screen.getByRole("button", { name: "生成创意候选" }));
      await flush();
      expect(spy).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
      fireEvent.click(screen.getByRole("button", { name: "重置会话" }));
      fireEvent.click(screen.getByRole("button", { name: /帮我想一个/u }));
      await flush();
      fireEvent.click(screen.getByRole("button", { name: "生成创意候选" }));
      await flush();
      expect(spy).toHaveBeenCalledTimes(2);
      await act(async () => { fail(new Error("旧创意失败")); await old.catch(() => undefined); });
      expect(screen.queryByText("旧创意失败")).not.toBeInTheDocument();
      expect(screen.getByText("正在生成创意方向...")).toBeInTheDocument();
      await act(async () => { complete(await original(...spy.mock.calls[1])); await current; });
    } finally {
      spy.mockRestore();
    }
  });
  it("does not let an old polish failure close the new session's review", async () => {
    let fail!: (error: Error) => void, complete!: (task: TaskView) => void;
    const pending = new Promise<TaskView>((_resolve, reject) => { fail = reject; });
    const current = new Promise<TaskView>((resolve) => { complete = resolve; });
    const originalWait = sessionApi.waitForTask;
    const spy = vi.spyOn(sessionApi, "waitForTask").mockReturnValueOnce(pending).mockReturnValueOnce(current);
    try {
      renderIntake();
      fireEvent.click(screen.getByRole("button", { name: "载入示例" }));
      fireEvent.click(screen.getByRole("button", { name: /生成润色校样/u }));
      await flush();
      expect(spy).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
      fireEvent.click(screen.getByRole("button", { name: "重置会话" }));
      fireEvent.click(screen.getByRole("button", { name: /我有一个想法/u }));
      fireEvent.click(screen.getByRole("button", { name: "载入示例" }));
      fireEvent.click(screen.getByRole("button", { name: /生成润色校样/u }));
      await flush();
      expect(spy).toHaveBeenCalledTimes(2);
      await act(async () => { fail(new Error("旧润色失败")); await pending.catch(() => undefined); });
      expect(screen.getByRole("heading", { name: "逐字确认 Agent 改了什么。" })).toBeInTheDocument();
      expect(screen.queryByText("旧润色失败")).not.toBeInTheDocument();
      await act(async () => { complete(await originalWait(...spy.mock.calls[1])); await current; });
    } finally {
      spy.mockRestore();
    }
  });

  it("uses the three-card official landing and removes redundant side rails", async () => {
    renderLanding();

    expect(
      document.querySelector('[data-casefile-surface="intake-center-v1"]'),
    ).toHaveAttribute("data-entrance-motion", "true");
    expect(screen.getByTestId("landing-entrance-prologue")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "你的故事，想从哪里开始？" }),
    ).toBeInTheDocument();
    expect(screen.getByText("CASE INTAKE / 故事从此落笔")).toBeInTheDocument();
    expect(
      screen.getByText(/走过的线索都会替你留在案卷里/u),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /我有一个想法/u })).toBeEnabled();
    expect(screen.getByRole("button", { name: /帮我想一个/u })).toBeEnabled();
    expect(screen.getByRole("button", { name: /我有已有内容/u })).toBeEnabled();
    expect(screen.queryByLabelText("建案入口")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("实时简报映射")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("建案进度")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("测试项目")).toBeInTheDocument());
    expect(screen.getByText("午夜回航旧案")).toBeInTheDocument();
  });

  it("plays the landing entrance only on the initial visit", async () => {
    renderLanding();
    const surface = document.querySelector(
      '[data-casefile-surface="intake-center-v1"]',
    );

    fireEvent.click(screen.getByRole("button", { name: /我有一个想法/u }));
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));

    await waitFor(() => {
      expect(surface).not.toHaveAttribute("data-entrance-motion");
    });
  });

  it("moves the real A, B, and C functions behind the landing cards", async () => {
    const view = renderLanding();

    fireEvent.click(screen.getByRole("button", { name: /我有一个想法/u }));

    expect(
      screen.getByRole("heading", {
        name: "把一闪而过的念头，留在故事开始的地方。",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("第 1 步 / 捕捉微光")).not.toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /表达优化/u }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /叙事增强/u }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("写下最初想法")).toHaveValue("");
    const ideaFocusPlane = screen.getByRole("main");
    const continueAction = screen.getByRole("button", {
      name: /继续关键追问/u,
    });
    expect(ideaFocusPlane).toHaveAttribute("data-focus-layout", "content-fit");
    expect(
      screen.queryByRole("button", { name: "关闭提示" }),
    ).not.toBeInTheDocument();
    expect(continueAction.closest("footer")?.parentElement).toBe(
      screen
        .getByRole("heading", {
          name: "把一闪而过的念头，留在故事开始的地方。",
        })
        .closest("section"),
    );
    expect(screen.queryByLabelText("建案入口")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("实时简报映射")).not.toBeInTheDocument();

    view.unmount();
    renderLanding();
    fireEvent.click(screen.getByRole("button", { name: /帮我想一个/u }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "创意方向" })).toBeInTheDocument(),
    );

    cleanup();
    renderLanding();
    fireEvent.click(screen.getByRole("button", { name: /我有已有内容/u }));
    expect(
      screen.getByRole("heading", { name: "反向解析审阅" }),
    ).toBeInTheDocument();
  });

  it("hydrates the current project from the URL pointer", async () => {
    window.history.replaceState({}, "", "/?project=1");
    renderIntake();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "返回首页" }),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    expect(
      screen.getByRole("link", { name: /分析师工作台/u }),
    ).toHaveAttribute("href", "/workbench?project=1");
    expect(screen.queryByText("正在从服务端恢复当前卷宗…")).not.toBeInTheDocument();
  });

  it("rejects an invalid URL project pointer without creating a session", async () => {
    window.history.replaceState({}, "", "/?project=invalid");
    renderIntake();
    await flush();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "项目地址无效，请从建案历史重新调出。",
    );
    expect(screen.getByLabelText("写下最初想法")).toHaveValue("");
    expect(window.location.search).toBe("");
  });

  it("runs the full A path, selects a tailored strategy, and generates one deep draft", async () => {
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
      screen.getByRole("heading", { name: "沿着疑问的微光，辨认故事的方向。" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("第 2 步 / 关键追问")).not.toBeInTheDocument();
    expect(screen.queryByText(/关键判断/u)).not.toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Agent 正在思考" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("agent-thinking-motion")).toHaveAttribute(
      "aria-hidden",
      "true",
    );

    await flush();

    expect(
      screen.getByRole("heading", { name: "沿着疑问的微光，辨认故事的方向。" }),
    ).toBeInTheDocument();
    // 成功反馈必须进入可见的 live region，而不是只写进未渲染的 state。
    expect(
      screen.getByText("起案原文已记录，进入关键追问。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回起案" })).toBeInTheDocument();
    const nextQuestion = screen.getByRole("button", { name: /下一题/u });
    expect(nextQuestion).toBeDisabled();

    fireEvent.click(
      screen.getByRole("radio", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    );
    expect(nextQuestion).toBeEnabled();
    fireEvent.click(nextQuestion);
    expect(screen.getByRole("button", { name: "← 上一题" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "← 上一题" }));
    expect(
      screen.getByRole("radio", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    ).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: "稍后决定" }));
    const generateBrief = screen.getByRole("button", {
      name: /形成创作简报/u,
    });
    expect(generateBrief).toBeEnabled();
    fireEvent.click(generateBrief);

    expect(
      screen.getByRole("heading", {
        name: "正在形成可确认的创作简报。",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Agent 正在整理创作简报" }),
    ).toBeInTheDocument();

    await flush();

    expect(
      screen.getByRole("heading", {
        name: "让故事的方向落定，再向深处落笔。",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/第 3 步 \/ /u)).not.toBeInTheDocument();
    expect(
      screen.queryByText("每个字段都保留来源。表单修改和对话修改会产生新候选，不覆盖旧版本。"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("CASE BRIEF")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "创作简报摘要" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/T\d{2}:\d{2}:\d{2}/u)).not.toBeInTheDocument();
    const returnToQuestions = screen.getByRole("button", {
      name: "返回关键追问",
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
    fireEvent.click(screen.getByRole("radio", { name: /唯一解/u }));
    fireEvent.click(
      screen.getByRole("radio", { name: /信息不足时保持未决/u }),
    );
    expect(
      screen.getByRole("radio", { name: /信息不足时保持未决/u }),
    ).toBeChecked();
    expect(screen.getByText("Agent 会随深稿拟定答案")).toBeInTheDocument();
    expect(
      screen.getByText(/这里不会立即出现候选。生成深稿时/u),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("radio", { name: /使用我提供的答案/u }),
    );
    expect(
      screen.getByText("你先锁定答案，Agent 只负责展开"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("知道答案可直接填写；还没有答案也可以让 Agent 先拟一版"),
    ).toBeInTheDocument();
    fake.backend.setFailNextAnchorExtract(true);
    fireEvent.click(screen.getByRole("button", { name: "让 Agent 先拟一版" }));
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "作者答案候选生成接口不兼容，请重启本地服务后重试。",
    );
    fireEvent.click(screen.getByRole("button", { name: "让 Agent 先拟一版" }));
    const authorAnswerPending = screen.getByRole("button", {
      name: "Agent 正在拟定…",
    });
    expect(authorAnswerPending).toBeDisabled();
    expect(
      within(authorAnswerPending).getByTestId("author-answer-thinking"),
    ).toBeInTheDocument();
    await flush();
    expect(screen.getByText("Agent 候选 · 待作者确认")).toBeInTheDocument();
    expect(
      screen.getByText("Agent 只提供候选，不会自动写入作者答案。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "不采用，我自己写" }));
    fireEvent.change(screen.getByLabelText("作者答案"), {
      target: { value: "我自己的结论：真正的发送者是未来的档案修复师。" },
    });
    expect(
      screen.getByRole("button", { name: /确认建案并继续/u }),
    ).toBeEnabled();
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
      briefFields.slice(0, 3).map((field) =>
        field.querySelector("header label")?.textContent,
      ),
    ).toEqual(["一句话概念*", "推理目标*", "结论模式*"]);
    expect(
      briefFields.slice(0, 3).every((field) =>
        field.matches('[data-required="true"]') &&
        field.querySelector("header label > em")?.textContent === "*",
      ),
    ).toBe(true);
    expect(screen.getByText("创作约束设置")).toBeInTheDocument();
    expect(screen.getByText("展开设置")).toBeInTheDocument();
    // 服务端已经推进到隐藏的 brief_review，页面仍应在第 3 步完成幂等确认。
    fake.backend.markFormalBriefReview();

    fireEvent.click(
      screen.getByRole("button", { name: /确认建案并继续/u }),
    );
    expect(
      screen.getByRole("heading", { name: "正在确认建案" }),
    ).toBeInTheDocument();
    expect(screen.getByText("正在整理创作边界与生成依据……")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存审阅" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: /逐条核对答案要点/u }),
    ).not.toBeInTheDocument();
    await screen.findByRole(
      "heading",
      { name: "建案完成" },
      { timeout: 1500 },
    );
    expect(
      screen.getByText("CaseFile 已准备好进入深稿阶段。"),
    ).toBeInTheDocument();

    await waitFor(
      () =>
        expect(
          screen.getByRole("heading", {
            name: "择定故事的航向，让它生长成篇。",
          }),
        ).toBeInTheDocument(),
      { timeout: 2000 },
    );
    expect(
      screen.queryByText("第 4 步 / 深稿候选与采用"),
    ).not.toBeInTheDocument();

    // 冻结后回到第 3 步查看同一份只读简报，不再恢复旧审阅页。
    fireEvent.click(
      screen.getByRole("button", { name: "03 建案 创作简报" }),
    );
    expect(screen.getByLabelText("一句话概念")).toBeDisabled();
    expect(screen.getByRole("button", { name: "修改建案" })).toBeEnabled();
    expect(screen.queryByText("把生成依据逐条钉在纸面上。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /返回深稿候选/u }));

    const strategyComparison = await screen.findByLabelText("三种策略并列比较");
    expect(within(strategyComparison).getAllByRole("button")).toHaveLength(3);
    expect(strategyComparison).toHaveTextContent("内容骨架");
    expect(strategyComparison).toHaveTextContent("创作边界");
    expect(strategyComparison).toHaveTextContent("核心卖点");
    expect(strategyComparison).toHaveTextContent("创作意图");
    expect(strategyComparison).toHaveTextContent("推理目标");
    expect(strategyComparison).toHaveTextContent("风险提示");
    expect(strategyComparison).not.toHaveTextContent(
      /content_outline|boundary_text|core_selling_points|creative_intent|reasoning_proposition|risk_notes/u,
    );
    expect(screen.queryByText(/Agent 建议/u)).not.toBeInTheDocument();
    expect(screen.queryByText("推理目标适合先建立证据闭环。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /让结构首先清晰可审阅/u }));
    fireEvent.click(screen.getByRole("button", { name: /生成结构优先完整深稿/u }));
    await flush();

    expect(fake.backend.getGenerationDraftRevisions().slice(-1)).toEqual([17]);
    expect(fake.backend.getGenerationDraftIds().slice(-1)).toEqual([71]);
    expect(screen.getByRole("button", { name: /重新生成结构优先完整深稿/u })).toBeEnabled();
    expect(screen.getByRole("button", { name: /缺页校准稿/u })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /封存室夜班稿/u })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /第七码互证稿/u })).not.toBeInTheDocument();

    // 新生成的候选默认展开，采用与预览直接可见。
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "预览工作台" })).toBeVisible(),
    );
    fireEvent.click(screen.getByRole("button", { name: "预览工作台" }));
    expect(routerPush).toHaveBeenLastCalledWith(
      expect.stringMatching(/^\/workbench\?project=1&preview=\d+$/u),
    );
    routerPush.mockClear();

    fake.backend.setFailNextDraftAdoption(true);
    fireEvent.click(
      screen.getByRole("button", { name: /采用为当前工作稿/u }),
    );
    await flush();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "候选采用服务暂不可用。",
    );
    expect(routerPush).not.toHaveBeenCalled();

    let releaseAdoption = () => {};
    fake.backend.setDraftAdoptionGate(
      new Promise<void>((resolve) => {
        releaseAdoption = resolve;
      }),
    );
    const adoptionCountBeforeRetry =
      fake.backend.getAdoptionCurrentDraftIds().length;
    fireEvent.click(
      screen.getByRole("button", { name: /采用为当前工作稿/u }),
    );
    await flush();

    const adopting = screen.getByRole("button", { name: "正在采用…" });
    expect(adopting).toBeDisabled();
    fireEvent.click(adopting);
    expect(fake.backend.getAdoptionCurrentDraftIds()).toHaveLength(
      adoptionCountBeforeRetry + 1,
    );

    await act(async () => {
      releaseAdoption();
    });
    await flush();

    expect(fake.backend.getAdoptionCurrentDraftIds().at(-1)).toBe(71);
    expect(routerPush).toHaveBeenCalledTimes(1);
    expect(routerPush).toHaveBeenLastCalledWith("/workbench?project=1");
    expect(
      screen.getByRole("button", { name: /进入分析师工作台/u }),
    ).toBeInTheDocument();

    routerPush.mockClear();
    fireEvent.click(
      screen.getByRole("button", { name: /进入分析师工作台/u }),
    );
    expect(routerPush).toHaveBeenCalledWith("/workbench?project=1");

    // 建立简报修订后，深稿候选在重新冻结前必须不可达。
    fireEvent.click(screen.getByRole("button", { name: "修改建案" }));
    const revisionDialog = screen.getByRole("dialog", {
      name: "创建建案修订",
    });
    expect(revisionDialog).toHaveTextContent("当前 V1 会继续保留");
    expect(revisionDialog).toHaveTextContent("现有候选和 Agent 对话都不会丢失");
    fireEvent.click(
      within(revisionDialog).getByRole("button", { name: "创建 V2" }),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("heading", {
          name: "让故事的方向落定，再向深处落笔。",
        }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "04 采用 深稿候选与采用" }),
    ).toBeDisabled();
    expect(fake.backend.beginBriefRevision).toHaveBeenCalledWith(1);

    // 修订已在服务端重开，第 3 步可再次走同一个后台确认链。
    fireEvent.click(
      screen.getByRole("button", { name: /确认建案并继续/u }),
    );
    expect(
      screen.getByRole("heading", { name: "正在确认建案" }),
    ).toBeInTheDocument();
  }, 15_000);

  it("interrupts inline only when the answer provider still needs an author decision", async () => {
    fake.backend.setResolutionNeedsConfirmation(true);
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "一名档案员发现三份可靠记录指向不存在的时间。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();
    fireEvent.click(
      screen.getByRole("radio", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: "稍后决定" }));
    fireEvent.click(screen.getByRole("button", { name: /形成创作简报/u }));
    await flush();

    fireEvent.click(screen.getByRole("radio", { name: /唯一解/u }));
    fireEvent.click(screen.getByRole("button", { name: /确认建案并继续/u }));

    const interruption = screen.getByRole("alert");
    expect(interruption).toHaveTextContent("还有一个判断需要你确认");
    expect(interruption).toHaveTextContent("当前还没有确定最终答案由谁提供");
    expect(
      screen.queryByRole("button", { name: /确认建案并继续/u }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "正在确认建案" }))
      .not.toBeInTheDocument();

    fireEvent.click(
      within(interruption).getByRole("radio", {
        name: /让 Agent 在深稿中形成答案/u,
      }),
    );
    fireEvent.click(
      within(interruption).getByRole("button", { name: /确认后继续/u }),
    );

    expect(
      screen.getByRole("heading", { name: "正在确认建案" }),
    ).toBeInTheDocument();
    await screen.findByRole(
      "heading",
      { name: "建案完成" },
      { timeout: 1500 },
    );
  });

  it("keeps the editable Brief in place when background confirmation fails", async () => {
    await reachBriefWithoutQuestions();
    fake.backend.setFailNextBriefConfirmation(true);
    const confirmationCallsBefore = fake.backend.confirmBrief.mock.calls.length;

    const confirm = screen.getByRole("button", { name: /确认建案并继续/u });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(
      screen.getByRole("heading", { name: "正在确认建案" }),
    ).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "建案确认服务暂不可用。",
      ),
    );
    expect(screen.getByLabelText("一句话概念")).toHaveValue(
      "一名档案员发现三份可靠记录指向不存在的时间。",
    );
    expect(screen.getByRole("button", { name: "重新确认" })).toBeEnabled();
    expect(fake.backend.confirmBrief).toHaveBeenCalledTimes(
      confirmationCallsBefore + 1,
    );
  });

  it("shows a compact Agent revision state and reports exactly which Brief fields changed", async () => {
    await reachBriefWithoutQuestions();

    expect(screen.getByText("CASEFILE AGENT / REVISION")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("对话修改指令"), {
      target: { value: "把内容骨架扩充一个最终验证阶段。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /交给 Agent 修改/u }));

    const pendingAction = screen.getByRole("button", {
      name: /Agent 正在生成.*对照当前候选/u,
    });
    expect(pendingAction).toBeDisabled();
    expect(pendingAction.closest("section")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByTestId("dialogue-revision-motion")).not.toBeInTheDocument();

    const receipt = await screen.findByRole("status", {
      name: "本轮 Agent 修改",
    });
    expect(receipt).toHaveTextContent("内容骨架");
    expect(receipt).toHaveTextContent("2 阶段");
    expect(receipt).toHaveTextContent("3 阶段");
    expect(receipt).toHaveTextContent("未列出的字段保持不变");
    expect(screen.getByLabelText("阶段 3 描述")).toHaveValue(
      "在封存前完成最终验证",
    );
    const outlineCard = screen
      .getByLabelText("阶段 3 描述")
      .closest('section[data-field="outline"]');
    expect(outlineCard).not.toBeNull();
    expect(
      within(outlineCard as HTMLElement).getByText("本轮已修改"),
    ).toBeInTheDocument();
  });

  it("lets the author form a brief when the agent decides no questions are needed", async () => {
    fake.backend.setQuestionBatch([]);
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: {
        value:
          "一名档案员发现三份可靠记录指向一段不存在的时间；结论由 Agent 在深稿中拟定，规模一晚完成。",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();

    expect(
      screen.getByText(/无需追问；可以直接形成创作简报/u),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "当前信息已经足够。" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("questions-complete-motion")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.getByRole("button", { name: /形成创作简报/u })).toBeEnabled();
    expect(
      screen.getByText(/Agent 已完成方向缺口研查/u),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /形成创作简报/u }));
    await flush();
    expect(
      screen.getByRole("heading", {
        name: "让故事的方向落定，再向深处落笔。",
      }),
    ).toBeInTheDocument();
  });

  it.each(["source refresh", "additional questions"])(
    "keeps unanswered required questions reachable after an empty %s batch",
    async (entry) => {
      renderIntake();
      fireEvent.change(screen.getByLabelText("写下最初想法"), {
        target: { value: "一名档案员发现三份矛盾的记录。" },
      });
      fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
      await flush();
      fake.backend.setQuestionBatch([]);

      if (entry === "source refresh") {
        fireEvent.click(screen.getByRole("button", { name: "01 输入 最初想法" }));
        fireEvent.change(screen.getByLabelText("写下最初想法"), {
          target: { value: "档案员发现第四份记录，决定重新调查。" },
        });
        fireEvent.click(screen.getByRole("button", { name: /重新研查关键追问/u }));
      } else {
        fireEvent.click(screen.getByRole("button", { name: "再生成一些问题" }));
      }
      await flush();

      expect(screen.queryByText(/无需追问；可以直接形成创作简报/u)).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "作品最终要回答哪一个核心问题？" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /下一题/u })).toBeDisabled();
      fireEvent.click(screen.getByRole("radio", { name: "找出是谁伪造了那段不存在的时间。" }));
      fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
      expect(screen.getByRole("button", { name: /形成创作简报/u })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: /形成创作简报/u }));
      await flush();
      expect(screen.getByRole("heading", { name: "让故事的方向落定，再向深处落笔。" })).toBeInTheDocument();
    },
  );

  it("treats an all-optional question set as answerable", async () => {
    fake.backend.setQuestionBatch([
      {
        question_key: "experience_scale",
        ordinal: 1,
        prompt: "你希望它是一晚完成的小案，还是长案？",
        impact: "影响角色数量与时长。",
        required: false,
        suggestions: ["一晚完成。", "三幕长案。"],
        answer_status: "unanswered",
        answer_text: null,
        answer_source: null,
      },
      {
        question_key: "tone",
        ordinal: 2,
        prompt: "希望偏克制还是偏戏剧？",
        impact: "影响叙事语气。",
        required: false,
        suggestions: ["克制", "戏剧化"],
        answer_status: "unanswered",
        answer_text: null,
        answer_source: null,
      },
    ]);
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "一段不存在的时间。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();

    expect(screen.queryByText("可以暂缓")).not.toBeInTheDocument();
    expect(screen.queryByText("必须回答")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    expect(screen.getByRole("button", { name: /形成创作简报/u })).toBeEnabled();
  });

  it("confirms before replacing a non-empty idea with the sample", async () => {
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "我已经写下的灵感。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "载入示例" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "载入示例会替换当前已输入的最初想法。",
    );
    expect(screen.getByLabelText("写下最初想法")).toHaveValue(
      "我已经写下的灵感。",
    );

    fireEvent.click(screen.getByRole("button", { name: "仍要载入" }));
    expect(
      (screen.getByLabelText("写下最初想法") as HTMLTextAreaElement).value,
    ).toContain("档案");
  });

  it("returns to the source after question generation fails without a manual brief fallback", async () => {
    fake.backend.setFailNextQuestionGeneration(true);
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "一段不存在的时间。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();

    expect(
      screen.getByRole("heading", { name: "沿着疑问的微光，辨认故事的方向。" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "追问服务暂不可用，请返回原稿后重试。",
    );
    expect(screen.getByRole("button", { name: /形成创作简报/u })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /手动建立简报/u }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回原稿" }));
    expect(
      screen.getByRole("heading", {
        name: "把一闪而过的念头，留在故事开始的地方。",
      }),
    ).toBeInTheDocument();
  });

  it("blocks stepper jumps that would bypass persisting the source text", async () => {
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "已保存的初始想法。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "01 输入 最初想法" }));

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "尚未保存的改动。" },
    });
    fireEvent.click(
      screen.getByRole("button", {
        name: "02 追问 关键追问 需要更新",
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "最初想法尚未保存，请先完成新的关键追问研查。",
    );
    expect(screen.getByLabelText("写下最初想法")).toHaveValue(
      "尚未保存的改动。",
    );
  });

  it("marks the brief stale when a prior answer changes and rebuilds it without deleting history", async () => {
    await reachBriefWithQuestions();

    fireEvent.click(screen.getByRole("button", { name: "返回关键追问" }));
    fireEvent.click(screen.getByRole("button", { name: "← 上一题" }));
    fireEvent.click(
      screen.getByRole("radio", {
        name: "判断三份可靠记录为什么会同时说谎。",
      }),
    );

    expect(
      screen.getByRole("button", {
        name: "03 建案 创作简报 需要更新",
      }),
    ).toBeInTheDocument();
    const dependencyNotice = screen.getByRole("status", {
      name: "创作简报需要更新",
    });
    expect(within(dependencyNotice).getByText("已修改 1 个创作判断")).toBeInTheDocument();
    expect(dependencyNotice).toHaveTextContent("现有 Brief 与候选不会被删除");

    fireEvent.click(
      screen.getByRole("button", {
        name: "03 建案 创作简报 需要更新",
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "新的判断尚未并入简报，请点击“更新建案简报”。",
    );

    fireEvent.click(
      within(dependencyNotice).getByRole("button", {
        name: /更新建案简报/u,
      }),
    );
    await flush();

    expect(
      screen.getByRole("heading", {
        name: "让故事的方向落定，再向深处落笔。",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "03 建案 创作简报" }),
    ).toBeInTheDocument();
  });

  it("warns before returning from the brief to source and invalidates downstream only after editing", async () => {
    await reachBriefWithQuestions();

    fireEvent.click(screen.getByRole("button", { name: "01 输入 最初想法" }));
    const impactDialog = screen.getByRole("alertdialog", {
      name: "返回修改起案内容？",
    });
    expect(impactDialog).toHaveTextContent("当前关键追问");
    expect(impactDialog).toHaveTextContent("已有内容、候选和版本不会丢失");

    fireEvent.click(within(impactDialog).getByRole("button", { name: "取消" }));
    expect(
      screen.getByRole("heading", {
        name: "让故事的方向落定，再向深处落笔。",
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "01 输入 最初想法" }));
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: /返回修改/u,
      }),
    );
    expect(screen.queryByLabelText("下游内容需要更新")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "档案员发现四份记录，其中一份来自未来。" },
    });

    expect(screen.getByLabelText("下游内容需要更新")).toHaveTextContent(
      "已有内容、候选和版本都不会丢失",
    );
    expect(
      screen.getByRole("button", {
        name: "02 追问 关键追问 需要更新",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "03 建案 创作简报 需要更新",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /重新研查关键追问/u }),
    ).toBeEnabled();
  });

  it("keeps old answers as context but only presents the newly generated question batch", async () => {
    renderIntake();

    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value: "一名档案员发现三份可靠记录指向一段不存在的时间。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();

    fireEvent.click(
      screen.getByRole("radio", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    );
    expect(
      screen.getByDisplayValue("找出是谁伪造了那段不存在的时间。"),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "再生成一些问题" }),
    );
    expect(
      screen.getByRole("status", { name: "Agent 正在继续研查" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("作品最终要回答哪一个核心问题？"),
    ).not.toBeInTheDocument();
    await flush();

    expect(
      screen.getByRole("heading", {
        name: "还需要多少组相互矛盾的记录，才能支撑核心推理？",
      }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("关键追问 1 / 2")).toBeInTheDocument();
    expect(
      screen.queryByText("作品最终要回答哪一个核心问题？"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("radio", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "前往第 1 题" }));
    expect(
      screen.getByRole("heading", {
        name: "还需要多少组相互矛盾的记录，才能支撑核心推理？",
      }),
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
      screen.getByRole("heading", { name: "沿着疑问的微光，辨认故事的方向。" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("作品最终要回答哪一个核心问题？"),
    ).toBeInTheDocument();
    // Provider 回退后，追加批次也必须保留尚未回答的必答题。
    expect(screen.getByRole("button", { name: /下一题/u })).toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", { name: "再生成一些问题" }),
    );
    expect(
      screen.getByRole("status", { name: "Agent 正在继续研查" }),
    ).toBeInTheDocument();
    await flush();

    expect(screen.getByRole("heading", { name: "作品最终要回答哪一个核心问题？" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "前往第 4 题" }));
    expect(screen.getByRole("button", { name: /形成创作简报/u })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "前往第 1 题" }));
    fireEvent.click(screen.getByRole("radio", { name: "找出是谁伪造了那段不存在的时间。" }));
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    expect(
      screen.getByRole("heading", {
        name: "还需要多少组相互矛盾的记录，才能支撑核心推理？",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText("必须回答")).not.toBeInTheDocument();
    expect(screen.queryByText("可以暂缓")).not.toBeInTheDocument();
  });

  it("refreshes and retries when the intake revision changes during question creation", async () => {
    fake.backend.setFailNextQuestionRevision(true);
    renderIntake();

    fireEvent.click(screen.getByRole("button", { name: "载入示例" }));
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
    await flush();

    expect(
      screen.getByRole("heading", { name: "沿着疑问的微光，辨认故事的方向。" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("作品最终要回答哪一个核心问题？"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Brief Intake revision is stale"),
    ).not.toBeInTheDocument();
  });


});

describe("case history drawer", () => {
  it("keeps the reset landing page when an older history restore completes", async () => {
    renderIntake();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    fireEvent.click(screen.getByRole("button", { name: "打开建案历史" }));
    await flush();
    const oldIntake = await sessionApi.fetchCaseIntake(2);
    let complete!: (value: BriefIntakeView) => void;
    const pending = new Promise<BriefIntakeView>((resolve) => { complete = resolve; });
    const spy = vi.spyOn(sessionApi, "fetchCaseIntake").mockReturnValueOnce(pending);
    try {
      const dialog = screen.getByRole("dialog", { name: "建案历史档案" });
      const card = within(dialog).getByText("午夜回航旧案").closest("article")!;
      fireEvent.click(within(card).getByRole("button", { name: "调出此卷" }));
      fireEvent.click(within(dialog).getByRole("button", { name: "关闭" }));
      fireEvent.click(screen.getByRole("button", { name: "重置会话" }));
      await act(async () => { complete(oldIntake); await pending; });
      expect(screen.getByRole("button", { name: /我有一个想法/u })).toBeInTheDocument();
      expect(window.location.search).toBe("");
      expect(screen.queryByText("已恢复该卷宗；服务端状态已重新同步。")).not.toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });
  function startCase(value = "正在建案中的念头。") {
    fireEvent.change(screen.getByLabelText("写下最初想法"), {
      target: { value },
    });
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));
  }

  it("opens from the topbar and lists active cases with progress", async () => {
    renderIntake();
    await flush();
    startCase();
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    fireEvent.click(screen.getByRole("button", { name: "打开建案历史" }));
    await flush();

    const dialog = screen.getByRole("dialog", { name: "建案历史档案" });
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("CF-0001")).toBeInTheDocument();
    expect(within(dialog).getByText("CF-0002")).toBeInTheDocument();
    expect(within(dialog).getByText("测试项目")).toBeInTheDocument();
    expect(within(dialog).getByText("午夜回航旧案")).toBeInTheDocument();
    expect(within(dialog).getByText("当前卷宗")).toBeInTheDocument();
    expect(within(dialog).queryByText("封存的旧卷")).not.toBeInTheDocument();
  });

  it("reveals archived cases behind the archived toggle and can unarchive them", async () => {
    renderLanding();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "打开建案历史" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /已归档/u }));
    expect(screen.getByText("封存的旧卷")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "移出归档" }));
    await flush();

    // 归档层已清空，移出的卷宗回到进行中区。
    expect(screen.getByText("没有封存的卷宗。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /已归档/u }));
    expect(screen.getByText("封存的旧卷")).toBeInTheDocument();
  });

  it("archives an active case from the drawer", async () => {
    renderLanding();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "打开建案历史" }));
    await flush();

    const dialog = screen.getByRole("dialog", { name: "建案历史档案" });
    const card = within(dialog).getByText("午夜回航旧案").closest("article")!;
    fireEvent.click(
      within(card).getByRole("button", { name: "归档" }),
    );
    await flush();

    expect(within(dialog).queryByText("午夜回航旧案")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /已归档/u }));
    expect(within(dialog).getByText("午夜回航旧案")).toBeInTheDocument();
  });

  it("does not offer archive for the currently active case", async () => {
    renderIntake();
    await flush();
    startCase();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    fireEvent.click(screen.getByRole("button", { name: "打开建案历史" }));
    await flush();

    const dialog = screen.getByRole("dialog", { name: "建案历史档案" });
    const card = within(dialog).getByText("测试项目").closest("article")!;
    expect(
      within(card).queryByRole("button", { name: "归档" }),
    ).not.toBeInTheDocument();
    expect(within(card).getByText("当前卷宗")).toBeInTheDocument();
  });

  it("stashes the current case when restoring a historical one and can return to it", async () => {
    renderIntake();
    await flush();

    startCase("正在进行中的念头。");
    await flush();
    expect(
      screen.queryByRole("button", { name: "回到暂存" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    fireEvent.click(screen.getByRole("button", { name: "打开建案历史" }));
    await flush();
    const dialog = screen.getByRole("dialog", { name: "建案历史档案" });
    const card = within(dialog).getByText("午夜回航旧案").closest("article")!;
    fireEvent.click(within(card).getByRole("button", { name: "调出此卷" }));
    await flush();

    expect(
      screen.queryByRole("dialog", { name: "建案历史档案" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("写下最初想法")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "回到暂存" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "回到暂存" }));
    // 回到暂存后恢复的是当时的关键追问步骤，原文在追问视图的源胶囊中。
    expect(
      screen.getAllByText("正在进行中的念头。").length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "回到暂存" }),
    ).not.toBeInTheDocument();
  });
});
