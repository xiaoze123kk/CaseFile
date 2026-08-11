import type { CaseFile, CoreMetadata } from "@casefile/contracts";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnalystWorkbench } from "@/features/analyst-workbench/analyst-workbench";
import {
  ApiError,
  type DraftCandidatePreviewView,
  type DraftSummaryView,
  type DraftView,
  type ProjectView,
  type WorkbenchContextView,
} from "@/lib/api-client";

const mocks = vi.hoisted(() => ({
  activateDraft: vi.fn(),
  adoptCandidate: vi.fn(),
  candidateStatus: vi.fn(),
  fetchCaseDraft: vi.fn(),
  fetchDraftCandidatePreview: vi.fn(),
  fetchExposurePlan: vi.fn(),
  fetchWorkbenchContext: vi.fn(),
  listDrafts: vi.fn(),
  listProjects: vi.fn(),
  loadProject: vi.fn(),
  patchCaseDraftObject: vi.fn(),
  previewCaseDraftEventTime: vi.fn(),
  putExposurePlan: vi.fn(),
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    activateDraft: mocks.activateDraft,
    fetchWorkbenchContext: mocks.fetchWorkbenchContext,
    listDrafts: mocks.listDrafts,
    listProjects: mocks.listProjects,
  };
});

vi.mock("@/features/case-session/case-session-provider", () => ({
  useCaseSession: () => ({
    activeProjectId: null,
    activeCandidate: null,
    adoptCandidate: mocks.adoptCandidate,
    candidateStatus: mocks.candidateStatus,
    loadProject: mocks.loadProject,
  }),
}));

vi.mock("@/features/case-session/case-session-api", () => ({
  fetchCaseDraft: mocks.fetchCaseDraft,
  fetchDraftCandidatePreview: mocks.fetchDraftCandidatePreview,
  fetchExposurePlan: mocks.fetchExposurePlan,
  patchCaseDraftObject: mocks.patchCaseDraftObject,
  previewCaseDraftEventTime: mocks.previewCaseDraftEventTime,
  putExposurePlan: mocks.putExposurePlan,
}));

function metadata(description: string): CoreMetadata {
  return {
    description,
    tags: [],
    source_refs: [],
    confidence: 0.88,
    confirmation_status: "ai_inferred",
    created_by: {
      actor_type: "agent",
      actor_id: "agent_brief_to_draft",
    },
    updated_at: "2026-08-07T12:00:00Z",
    revision: 1,
  };
}

function makeCaseFile(entityName = "真实调查员"): CaseFile {
  return {
    schema_version: "2.0",
    casefile_id: "case_production_test",
    title: "真实测试卷宗",
    status: "draft",
    version: {
      version_id: "draft_production_test_1",
      version_no: 1,
      parent_version_id: null,
    },
    brief_ref: { brief_id: "brief_production_test", version: 1 },
    resolution_specs: [
      {
        ...metadata("定义当前卷宗必须回答的核心问题。"),
        id: "res_core",
        title: "核心问题",
        question_type: "causal_explanation",
        reasoning_question: "谁改写了记录？",
        conclusion_mode: "unique",
        required_slots: [],
        accepted_answers: [],
        required_claim_refs: [],
      },
    ],
    entities: [
      {
        ...metadata("负责核对真实 Draft 的调查员。"),
        id: "ent_real_analyst",
        entity_type: "person",
        name: entityName,
        aliases: [],
        traits: ["谨慎"],
        goals: ["核对记录"],
        secrets: [],
        capabilities: [],
        knowledge_states: [],
      },
      {
        ...metadata("负责门禁值守。"),
        id: "ent_gate_operator",
        entity_type: "person",
        name: "值班员",
        aliases: [],
        traits: ["警觉"],
        goals: ["维护门禁"],
        secrets: [],
        capabilities: [],
        knowledge_states: [],
      },
    ],
    relationships: [
      {
        ...metadata("调查员正在核对值班员的记录。"),
        id: "rel_analyst_operator",
        title: "核对记录",
        from_ref: { object_type: "entity", object_id: "ent_real_analyst" },
        to_ref: { object_type: "entity", object_id: "ent_gate_operator" },
        relationship_type: "investigates",
        direction: "directed",
        truth_status: "canon_true",
        visibility: "public",
      },
    ],
    locations: [
      {
        ...metadata("门禁记录产生的位置。"),
        id: "loc_archive_gate",
        name: "档案馆门禁",
        parent_ref: null,
        adjacency_refs: [],
        access_rules: [],
        travel_times: [],
        visibility_rules: [],
        spatial_position: {
          coordinate_system: "schematic",
          x: 42,
          y: 36,
        },
      },
    ],
    events: [
      {
        ...metadata("值班员在门禁处开启档案馆。"),
        id: "evt_gate_opened",
        title: "门禁开启",
        truth_status: "reported",
        time: {
          kind: "exact",
          value: "2026-08-07T09:00",
          precision: "minute",
        },
        participant_refs: [
          { object_type: "entity", object_id: "ent_real_analyst" },
          { object_type: "entity", object_id: "ent_gate_operator" },
        ],
        location_ref: {
          object_type: "location",
          object_id: "loc_archive_gate",
        },
        cause_refs: [],
        effect_refs: [],
        observed_by_refs: [],
      },
    ],
    information_units: [
      {
        ...metadata("门禁系统生成的原始记录。"),
        id: "info_gate_log",
        information_type: "system_log",
        title: "门禁记录",
        content: "九点整门禁被值班员开启。",
        source_event_ref: {
          object_type: "event",
          object_id: "evt_gate_opened",
        },
        reliability: "high",
        truth_status: "reported",
        supports_claim_refs: [],
        refutes_claim_refs: [],
        availability: {
          perspective_refs: [],
          acquisition_conditions: [],
          alternative_path_refs: [],
        },
        classification: "key",
      },
    ],
    claims: [],
    hypotheses: [
      {
        ...metadata("尚无事件直接关联的待核假设。"),
        id: "hyp_record_tampered",
        title: "记录曾被改写",
        proposition: "门禁记录在生成后被改写。",
        target_resolution_ref: {
          object_type: "resolution_spec",
          object_id: "res_core",
        },
        required_claim_refs: [],
        falsifier_refs: [],
        competing_hypothesis_refs: [],
        status: "active",
        score: 0.45,
      },
    ],
    reasoning_paths: [],
    constraints: [],
    structure_locks: [],
    content_notices: [],
    extensions: {},
  };
}

function makeDraft(revision: number, entityName?: string): DraftView {
  return {
    project_id: 42,
    casefile_id: 9,
    draft_id: 9,
    title: "真实测试卷宗",
    revision,
    schema_version: "2.0",
    status: "active",
    document_status: "draft",
    brief_version_id: 4,
    created_at: "2026-08-09T10:00:00+00:00",
    updated_at: "2026-08-09T11:00:00+00:00",
    content: makeCaseFile(entityName),
  };
}

function makeDraftWithScenePosition(revision: number, x: number, y = 36): DraftView {
  const draft = makeDraft(revision);
  const location = draft.content?.locations.find(
    (candidate) => candidate.id === "loc_archive_gate",
  );
  if (!location) throw new Error("测试工作稿缺少档案馆门禁地点");
  location.spatial_position = { coordinate_system: "schematic", x, y };
  return draft;
}

function makeProject(id: number, title: string): ProjectView {
  return {
    id,
    title,
    description: null,
    profile: {},
    status: "active",
    archived_at: null,
    created_at: "2026-08-09T10:00:00+00:00",
    updated_at: "2026-08-09T11:00:00+00:00",
    casefile_id: id,
    current_draft_id: id === 42 ? 9 : 19,
    draft: {
      id: id === 42 ? 9 : 19,
      title,
      revision: 7,
      schema_version: "2.0",
      status: "active",
    },
  };
}

function makeDraftSummary(
  draftId: number,
  title: string,
  isCurrent: boolean,
): DraftSummaryView {
  return {
    draft_id: draftId,
    title,
    revision: 7,
    schema_version: "2.0",
    status: "active",
    document_status: "draft",
    brief_version_id: 4,
    brief_version_no: 4,
    has_content: true,
    is_current: isCurrent,
    created_at: "2026-08-09T10:00:00+00:00",
    updated_at: "2026-08-09T11:00:00+00:00",
  };
}

function makePreview(): DraftCandidatePreviewView {
  return {
    task_run_id: 73,
    brief_version_no: 4,
    is_current_brief: true,
    is_current: false,
    is_adopted: false,
    can_adopt: true,
    provider: "deepseek",
    model_id: "fake-brief-to-draft",
    title: "真实测试卷宗",
    content_hash: "b".repeat(64),
    object_counts: { entities: 1 },
    reasoning_questions: ["谁改写了记录？"],
    constraint_statements: [],
    candidate_strategy: "structure_first",
    candidate_strategy_version: "candidate-strategy-v1",
    candidate_strategy_label: "结构优先",
    attempt_count: 1,
    created_at: "2026-08-07T12:00:00Z",
    completed_at: "2026-08-07T12:01:00Z",
    preview: true,
    read_only: true,
    content: makeCaseFile("候选调查员"),
  };
}

function makeContext(
  overrides: Partial<WorkbenchContextView> = {},
): WorkbenchContextView {
  return {
    project_id: 42,
    draft_id: 9,
    draft_revision: 7,
    validation: {
      status: "passed",
      validator: "casefile.contracts.validate_casefile",
      schema_version: "2.0",
      issue_count: 0,
      issues: [],
      reason: null,
    },
    sources: [
      {
        trace_id: "source_records:12",
        source_table: "source_records",
        source_record_id: 12,
        source_kind: "human_original",
        content_text: "作者提交的真实原稿正文。",
        content_hash: "a".repeat(64),
        parent_source_record_id: null,
        generated_by_task_run_id: null,
        created_by_user_id: 1,
        created_at: "2026-08-07T12:00:00Z",
      },
    ],
    contract_source_refs: [
      { source_fragment_id: "src_gate_log", paths: ["/events/0/source_refs/0"] },
    ],
    audit_entries: [
      {
        entry_id: "draft_operations:31",
        source_table: "draft_operations",
        record_id: 31,
        occurred_at: "2026-08-07T12:05:00Z",
        actor: { kind: "user", user_id: 1, ref: null },
        action: "agent_adopt_brief_candidate",
        target_type: "draft",
        target_id: 9,
        trace_id: null,
        details: {
          sequence_no: 1,
          operation_group_no: 1,
          field_path: "",
          object_id: null,
          base_revision: 6,
          result_revision: 7,
        },
      },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  mocks.activateDraft.mockReset();
  mocks.adoptCandidate.mockReset().mockResolvedValue(false);
  mocks.candidateStatus.mockReset();
  mocks.fetchCaseDraft.mockReset();
  mocks.fetchDraftCandidatePreview.mockReset();
  mocks.fetchExposurePlan.mockReset().mockResolvedValue({
    plan_id: 17,
    draft_id: 9,
    revision: 0,
    updated_at: "2026-08-11T13:30:00+08:00",
    entries: [],
  });
  mocks.fetchWorkbenchContext.mockReset().mockResolvedValue(makeContext());
  mocks.listDrafts.mockReset().mockResolvedValue([
    makeDraftSummary(9, "真实测试卷宗", true),
    makeDraftSummary(10, "第二工作稿", false),
  ]);
  mocks.listProjects.mockReset().mockResolvedValue([
    makeProject(42, "真实测试卷宗"),
    makeProject(43, "另一卷宗"),
  ]);
  mocks.loadProject.mockReset().mockResolvedValue(undefined);
  mocks.patchCaseDraftObject.mockReset();
  mocks.previewCaseDraftEventTime.mockReset();
  mocks.putExposurePlan.mockReset();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("production analyst workbench", () => {
  it("shows explicit gates for a missing or invalid project id", () => {
    const missing = render(<AnalystWorkbench requestedProjectId={null} />);

    expect(
      screen.getByRole("heading", { name: "工作台需要项目 ID" }),
    ).toBeInTheDocument();
    expect(mocks.fetchCaseDraft).not.toHaveBeenCalled();

    missing.unmount();
    render(
      <AnalystWorkbench invalidProjectId requestedProjectId={null} />,
    );

    expect(
      screen.getByRole("heading", { name: "项目标识不合法" }),
    ).toBeInTheDocument();
    expect(mocks.fetchCaseDraft).not.toHaveBeenCalled();
  });

  it("rejects an invalid candidate preview id before loading any Draft", () => {
    render(
      <AnalystWorkbench
        invalidPreviewTaskRunId
        requestedPreviewTaskRunId={null}
        requestedProjectId={42}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "候选预览标识不合法" }),
    ).toBeInTheDocument();
    expect(mocks.fetchDraftCandidatePreview).not.toHaveBeenCalled();
    expect(mocks.fetchCaseDraft).not.toHaveBeenCalled();
    expect(mocks.fetchWorkbenchContext).not.toHaveBeenCalled();
  });

  it("loads an immutable candidate preview without reading or writing Current Draft", async () => {
    mocks.fetchDraftCandidatePreview.mockResolvedValueOnce(makePreview());

    render(
      <AnalystWorkbench
        requestedPreviewTaskRunId={73}
        requestedProjectId={42}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "正在读取候选预览" }),
    ).toBeInTheDocument();
    const previewBanner = await screen.findByRole("status", {
      name: "候选预览只读提示",
    });
    expect(previewBanner).toHaveTextContent("候选预览，不是 Current Draft");
    expect(screen.getAllByText("候选调查员").length).toBeGreaterThan(0);
    expect(previewBanner).toHaveTextContent("结构优先 · Brief V4 · 任务 #73");
    expect(mocks.fetchDraftCandidatePreview).toHaveBeenCalledWith(42, 73);
    expect(mocks.fetchCaseDraft).not.toHaveBeenCalled();
    expect(mocks.fetchWorkbenchContext).not.toHaveBeenCalled();
    expect(mocks.loadProject).not.toHaveBeenCalled();

    const editor = screen.getByRole("region", { name: "对象详情（只读）" });
    expect(within(editor).getByRole("textbox", { name: "名称" })).toHaveAttribute(
      "readonly",
    );
    expect(
      within(editor).getByRole("button", { name: "采用后才能编辑" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "候选预览不可使用 Agent" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "候选预览不可重置" }),
    ).toBeDisabled();
    expect(screen.getByRole("tab", { name: /导出预览/u })).toBeDisabled();
    expect(screen.getByRole("tab", { name: /编译中心/u })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新验证" })).toBeDisabled();
    expect(mocks.patchCaseDraftObject).not.toHaveBeenCalled();
  });

  it("loads the selected project draft and renders its real objects", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));

    render(<AnalystWorkbench requestedProjectId={42} />);

    expect(
      screen.getByRole("heading", { name: "正在读取当前工作稿" }),
    ).toBeInTheDocument();
    expect((await screen.findAllByText("真实调查员")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("真实测试卷宗").length).toBeGreaterThan(0);
    expect(screen.getByText("Draft R7 · 对象 R1")).toBeInTheDocument();
    const directory = screen.getByRole("region", { name: "对象目录结果" });
    const analystRow = within(directory).getByRole("button", {
      name: /真实调查员/,
    });
    expect(within(analystRow).getByText("人物")).toBeInTheDocument();
    expect(within(analystRow).getByText("ent_real_analyst")).toBeInTheDocument();
    expect(within(analystRow).queryByText("person")).not.toBeInTheDocument();
    expect(mocks.loadProject).toHaveBeenCalledWith(42);
    expect(mocks.fetchCaseDraft).toHaveBeenCalledWith(42);
    expect(mocks.fetchWorkbenchContext).toHaveBeenCalledWith(1, 42);
    expect(screen.getByText("已通过")).toBeInTheDocument();
  });

  it("keeps project switching available when the project has no materialized Draft", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce({
      ...makeDraft(1),
      content: null,
    });

    render(<AnalystWorkbench requestedProjectId={42} />);

    expect(
      await screen.findByRole("heading", {
        name: "这个项目还没有已生成的工作稿",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /项目.*真实测试卷宗/u }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /当前工作稿/u }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "返回建案中心生成工作稿" }),
    ).toHaveAttribute("href", "/?project=42");
  });

  it("scopes persisted canvas layouts by project and Draft", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    const first = render(<AnalystWorkbench requestedProjectId={42} />);
    await screen.findByRole("textbox", { name: "搜索对象名称或编号" });
    fireEvent.click(screen.getByRole("tab", { name: /关系图/u }));
    const firstLayoutKey = first.container
      .querySelector("[data-layout-key]")
      ?.getAttribute("data-layout-key");
    expect(firstLayoutKey).toContain("project%3A42%3Adraft%3A9");

    first.unmount();
    mocks.fetchCaseDraft.mockResolvedValueOnce({
      ...makeDraft(7),
      draft_id: 10,
    });
    const second = render(<AnalystWorkbench requestedProjectId={42} />);
    await screen.findByRole("textbox", { name: "搜索对象名称或编号" });
    fireEvent.click(screen.getByRole("tab", { name: /关系图/u }));
    const secondLayoutKey = second.container
      .querySelector("[data-layout-key]")
      ?.getAttribute("data-layout-key");

    expect(secondLayoutKey).toContain("project%3A42%3Adraft%3A10");
    expect(secondLayoutKey).not.toBe(firstLayoutKey);
  });

  it("renders real validation, SourceRecord content, trace ids, and audit provenance", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    expect((await screen.findAllByText("真实调查员")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("tab", { name: "验证问题0" }));
    expect(await screen.findByText("确定性验证已通过")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "引用来源1" }));
    expect(screen.getAllByText("source_records:12").length).toBeGreaterThan(0);
    expect(screen.getAllByText("作者提交的真实原稿正文。").length).toBeGreaterThan(0);
    expect(screen.getAllByText("src_gate_log").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("tab", { name: "审计记录1" }));
    expect(screen.getByText("采用 Draft 候选")).toBeInTheDocument();
    expect(screen.getByText("来源 draft_operations #31")).toBeInTheDocument();
  });

  it("renders deterministic validator codes and JSON paths without fixture issues", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    mocks.fetchWorkbenchContext.mockResolvedValueOnce(
      makeContext({
        validation: {
          status: "failed",
          validator: "casefile.contracts.validate_casefile",
          schema_version: "2.0",
          issue_count: 1,
          issues: [
            {
              issue_id: "validator:missing-ref",
              code: "missing_reference",
              path: "/events/0/location_ref",
              message: "引用的对象不存在",
              severity: "error",
              target: {
                object_ref: {
                  object_type: "event",
                  object_id: "evt_gate",
                },
                field_path: "/location_ref",
              },
            },
          ],
          reason: null,
        },
      }),
    );
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByText("1 个问题");
    fireEvent.click(screen.getByRole("tab", { name: "验证问题1" }));
    expect(screen.getByText("引用的对象不存在")).toBeInTheDocument();
    expect(screen.getByText("missing_reference · /events/0/location_ref")).toBeInTheDocument();
    expect(screen.queryByText("时间知识冲突")).not.toBeInTheDocument();
  });

  it("keeps a context read failure recoverable without hiding the real Draft", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    mocks.fetchWorkbenchContext
      .mockRejectedValueOnce(
        new ApiError(503, {
          code: "database_unavailable",
          message: "Database unavailable",
          details: {},
        }),
      )
      .mockResolvedValueOnce(makeContext());
    render(<AnalystWorkbench requestedProjectId={42} />);

    expect((await screen.findAllByText("真实调查员")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("tab", { name: "引用来源0" }));
    expect(screen.getAllByRole("alert")[0]).toHaveTextContent("数据库暂时不可用");
    fireEvent.click(screen.getAllByRole("button", { name: "重新读取" })[0]);
    expect((await screen.findAllByText("作者提交的真实原稿正文。")).length).toBeGreaterThan(0);
    expect(mocks.fetchWorkbenchContext).toHaveBeenCalledTimes(2);
  });

  it("starts the source drawer collapsed and lets the user expand it", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    const drawer = screen.getByRole("region", {
      name: "来源与运行记录抽屉",
    });
    const toggle = within(drawer).getByRole("button", {
      name: /来源抽屉/,
    });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      within(drawer).queryByText("真实来源内容尚未接入"),
    ).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(
      within(drawer).getByText("作者提交的真实原稿正文。"),
    ).toBeInTheDocument();
  });

  it("filters all five real object kinds with query-aware counts", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    const filters = screen.getByLabelText("对象类型筛选");
    expect(within(filters).getAllByRole("button")).toHaveLength(6);
    expect(
      within(filters).getByRole("button", { name: "实体，2 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "信息，1 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "事件，1 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "地点，1 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "假设，1 个匹配" }),
    ).toBeInTheDocument();

    const search = screen.getByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    fireEvent.change(search, { target: { value: "值班员" } });
    expect(
      within(filters).getByRole("button", { name: "全部对象，1 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "实体，1 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(filters).getByRole("button", { name: "信息，0 个匹配" }),
    ).toBeInTheDocument();

    fireEvent.click(
      within(filters).getByRole("button", { name: "信息，0 个匹配" }),
    );
    expect(
      screen.getByRole("button", { name: "信息，0 个匹配" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("当前条件没有匹配对象")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "info_gate_log" } });
    expect(
      within(filters).getByRole("button", { name: "信息，1 个匹配" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "对象目录结果" })).getByText(
        "门禁记录",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "清除对象筛选" }));
    expect(search).toHaveValue("");
    expect(
      within(filters).getByRole("button", { name: "全部对象，6 个匹配" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("nests real object subtypes under the active primary category", async () => {
    const draft = makeDraft(7);
    const content = draft.content;
    if (!content) throw new Error("测试工作稿缺少 CaseFile 内容");
    const operator = content.entities[1]!;
    operator.entity_type = "organization";
    operator.name = "门禁值班组";
    const gateLog = content.information_units[0]!;
    content.information_units.push({
      ...gateLog,
      id: "info_shift_note",
      information_type: "document",
      title: "值班交接单",
    });
    mocks.fetchCaseDraft.mockResolvedValueOnce(draft);
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    fireEvent.click(screen.getByRole("button", { name: "实体，2 个匹配" }));

    const entitySubtypes = screen.getByLabelText("实体子类型筛选");
    expect(
      within(entitySubtypes).getByRole("button", {
        name: "全部实体，2 个匹配",
      }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(entitySubtypes).getByRole("button", { name: "人物，1 个匹配" }),
    ).toBeInTheDocument();
    const organization = within(entitySubtypes).getByRole("button", {
      name: "组织，1 个匹配",
    });
    fireEvent.click(organization);

    const directory = screen.getByRole("region", { name: "对象目录结果" });
    expect(within(directory).getByText("门禁值班组")).toBeInTheDocument();
    expect(within(directory).queryByText("真实调查员")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "信息，2 个匹配" }));
    const informationSubtypes = screen.getByLabelText("信息子类型筛选");
    expect(
      within(informationSubtypes).getByRole("button", {
        name: "系统日志，1 个匹配",
      }),
    ).toBeInTheDocument();
    const documentSubtype = within(informationSubtypes).getByRole("button", {
      name: "文档，1 个匹配",
    });
    fireEvent.click(documentSubtype);
    const search = screen.getByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    fireEvent.change(search, { target: { value: "门禁记录" } });
    expect(
      within(informationSubtypes).getByRole("button", {
        name: "文档，0 个匹配",
      }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("当前条件没有匹配对象")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "值班交接单" } });
    expect(within(directory).getByText("值班交接单")).toBeInTheDocument();
    expect(screen.queryByLabelText("实体子类型筛选")).not.toBeInTheDocument();
  });

  it("links directory selection, object details, related events, and timeline", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    const directory = screen.getByRole("region", { name: "对象目录结果" });
    fireEvent.click(
      within(directory).getByRole("button", { name: /门禁记录/ }),
    );

    expect(screen.getByRole("textbox", { name: "标题" })).toHaveValue(
      "门禁记录",
    );
    const relatedEvents = screen.getByRole("region", { name: "关联事件" });
    const eventLink = within(relatedEvents).getByRole("button", {
      name: /门禁开启/,
    });
    expect(eventLink).toHaveTextContent("evt_gate_opened");

    fireEvent.click(eventLink);
    expect(screen.getByRole("tab", { name: /时间线/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("textbox", { name: "标题" })).toHaveValue(
      "门禁开启",
    );
    expect(
      screen.getByRole("button", { name: "事件，1 个匹配" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(directory).getByRole("button", { name: /门禁开启/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps a Current Draft object synchronized across all eight permanent views", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    const { container } = render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    const directory = screen.getByRole("region", { name: "对象目录结果" });
    fireEvent.click(
      within(directory).getByRole("button", { name: /记录曾被改写/ }),
    );

    const canvas = container.querySelector("#analyst-canvas") as HTMLElement;
    const expectedSelection = "假设 · 记录曾被改写 / hyp_record_tampered";
    const views = [
      ["时间线", "timeline"],
      ["关系图", "relations"],
      ["推理分析", "reasoning"],
      ["地图", "map"],
      ["卷宗编辑器", "dossier"],
      ["导出预览", "export"],
      ["编译中心", "compile"],
      ["证据对比", "evidence"],
    ] as const;

    for (const [label, view] of views) {
      fireEvent.click(screen.getByRole("tab", { name: new RegExp(label) }));
      expect(canvas).toHaveAttribute("data-workbench-view", view);
      expect(canvas).toHaveAttribute("data-draft-revision", "R7");
      expect(canvas).toHaveAttribute(
        "data-selected-object-id",
        "hyp_record_tampered",
      );
      expect(within(canvas).getByText(expectedSelection)).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: new RegExp(label) })).toHaveAttribute(
        "aria-selected",
        "true",
      );
    }

    expect(
      screen.getByText("“记录曾被改写”暂无可对照证据"),
    ).toBeInTheDocument();
    expect(screen.queryByText("角色提前知道“第五人权限”")).not.toBeInTheDocument();
    expect(screen.queryByText("Agent 建议")).not.toBeInTheDocument();
  });

  it("keeps location and event selection inside the Current Draft map view", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    const { container } = render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", { name: "搜索对象名称或编号" });
    fireEvent.click(screen.getByRole("tab", { name: /地图/ }));
    const marker = await screen.findByRole(
      "button",
      { name: /档案馆门禁，场景坐标，1 个事件/u },
      { timeout: 3000 },
    );
    fireEvent.click(marker);

    const canvas = container.querySelector("#analyst-canvas") as HTMLElement;
    expect(screen.getByRole("tab", { name: /地图/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(canvas).toHaveAttribute("data-selected-object-id", "loc_archive_gate");
    expect(await screen.findByRole("dialog")).toHaveTextContent("档案馆门禁");

    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: /门禁开启/u,
      }),
    );
    expect(screen.getByRole("tab", { name: /地图/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(canvas).toHaveAttribute("data-selected-object-id", "evt_gate_opened");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("saves only a scene spatial position and keeps selection, view, and revision linked", async () => {
    mocks.fetchCaseDraft
      .mockResolvedValueOnce(makeDraftWithScenePosition(7, 42))
      .mockResolvedValueOnce(makeDraftWithScenePosition(8, 42.5));
    mocks.patchCaseDraftObject.mockResolvedValueOnce({});
    const { container } = render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", { name: "搜索对象名称或编号" });
    fireEvent.click(screen.getByRole("tab", { name: /地图/ }));
    fireEvent.click(
      await screen.findByRole(
        "button",
        { name: /档案馆门禁，场景坐标，1 个事件/u },
        { timeout: 3000 },
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑位置" }));
    fireEvent.keyDown(
      await screen.findByRole("button", { name: /位置可编辑/u }),
      { key: "ArrowRight" },
    );

    expect(
      screen.getByRole("region", { name: "对象详情（只读）" }),
    ).toHaveTextContent("先保存或取消地图位置预览");
    fireEvent.click(screen.getByRole("tab", { name: /时间线/ }));
    expect(screen.getByRole("tab", { name: /地图/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));
    await waitFor(() =>
      expect(mocks.patchCaseDraftObject).toHaveBeenCalledWith(
        42,
        "loc_archive_gate",
        9,
        7,
        {
          spatial_position: {
            coordinate_system: "schematic",
            x: 42.5,
            y: 36,
          },
        },
      ),
    );
    await waitFor(() =>
      expect(container.querySelector("#analyst-canvas")).toHaveAttribute(
        "data-draft-revision",
        "R8",
      ),
    );
    expect(screen.getByRole("tab", { name: /地图/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(container.querySelector("#analyst-canvas")).toHaveAttribute(
      "data-selected-object-id",
      "loc_archive_gate",
    );
  });

  it("retains a map preview through a 409 and retries against the reviewed revision", async () => {
    mocks.fetchCaseDraft
      .mockResolvedValueOnce(makeDraftWithScenePosition(7, 42))
      .mockResolvedValueOnce(makeDraftWithScenePosition(8, 48))
      .mockResolvedValueOnce(makeDraftWithScenePosition(9, 42.5));
    mocks.patchCaseDraftObject
      .mockRejectedValueOnce(
        new ApiError(409, {
          code: "draft_revision_conflict",
          message: "CaseFile Draft revision is stale",
          details: {},
        }),
      )
      .mockResolvedValueOnce({});
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", { name: "搜索对象名称或编号" });
    fireEvent.click(screen.getByRole("tab", { name: /地图/ }));
    fireEvent.click(
      await screen.findByRole(
        "button",
        { name: /档案馆门禁，场景坐标，1 个事件/u },
        { timeout: 3000 },
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑位置" }));
    fireEvent.keyDown(
      await screen.findByRole("button", { name: /位置可编辑/u }),
      { key: "ArrowRight" },
    );
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));

    expect(await screen.findByRole("button", { name: "核对最新版" })).toBeInTheDocument();
    expect(mocks.fetchCaseDraft).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "核对最新版" }));
    expect(await screen.findByText(/最新版已修改此地点坐标/u)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));

    await waitFor(() => expect(mocks.patchCaseDraftObject).toHaveBeenCalledTimes(2));
    expect(mocks.patchCaseDraftObject).toHaveBeenLastCalledWith(
      42,
      "loc_archive_gate",
      9,
      8,
      {
        spatial_position: {
          coordinate_system: "schematic",
          x: 42.5,
          y: 36,
        },
      },
    );
  }, 10_000);

  it("keeps candidate map positions read-only and isolated from Current Draft writes", async () => {
    mocks.fetchDraftCandidatePreview.mockResolvedValueOnce(makePreview());
    render(
      <AnalystWorkbench
        requestedPreviewTaskRunId={73}
        requestedProjectId={42}
      />,
    );

    await screen.findByRole("status", { name: "候选预览只读提示" });
    fireEvent.click(screen.getByRole("tab", { name: /地图/ }));
    fireEvent.click(
      await screen.findByRole(
        "button",
        { name: /档案馆门禁，场景坐标/u },
        { timeout: 3000 },
      ),
    );

    expect(screen.getByRole("dialog")).toHaveTextContent(
      "候选预览只读；采用为 Current Draft 后才能编辑位置。",
    );
    expect(screen.queryByRole("button", { name: "编辑位置" })).toBeNull();
    expect(mocks.patchCaseDraftObject).not.toHaveBeenCalled();
    expect(mocks.fetchCaseDraft).not.toHaveBeenCalled();
  });

  it("blocks entering the map while object fields have unsaved edits", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    const { container } = render(<AnalystWorkbench requestedProjectId={42} />);

    const name = await screen.findByRole("textbox", { name: "名称" });
    fireEvent.change(name, { target: { value: "未保存的调查员名称" } });
    fireEvent.click(screen.getByRole("tab", { name: /地图/ }));

    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue(
      "未保存的调查员名称",
    );
    expect(
      within(screen.getByRole("region", { name: "对象详情与编辑" })).getByRole(
        "status",
      ),
    ).toHaveTextContent("请先保存或取消修改");
    expect(container.querySelector("#analyst-canvas")).toHaveAttribute(
      "data-selected-object-id",
      "ent_real_analyst",
    );
    expect(screen.getByRole("tab", { name: /时间线/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByTestId("spatial-map-canvas")).toBeNull();
  });

  it("renders only the event sequence in the Current Draft timeline", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    expect(
      screen.getByRole("heading", { name: "真实测试卷宗" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("application", { name: "事件关系图" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("同步关系图")).not.toBeInTheDocument();
  });

  it("clears stale timeline context for an object without related events", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    const directory = screen.getByRole("region", { name: "对象目录结果" });
    fireEvent.click(
      within(directory).getByRole("button", { name: /记录曾被改写/ }),
    );

    expect(screen.getByRole("textbox", { name: "标题" })).toHaveValue(
      "记录曾被改写",
    );
    expect(screen.getByText("此对象没有关联事件")).toBeInTheDocument();
    expect(
      screen.getByText("此对象尚未关联事件，时间线不会沿用上一次选择。"),
    ).toBeInTheDocument();
  });

  it("blocks object and related-event navigation until edits are saved or cancelled", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    const name = await screen.findByRole("textbox", { name: "名称" });
    const directory = screen.getByRole("region", { name: "对象目录结果" });
    fireEvent.change(name, { target: { value: "未保存的调查员名称" } });
    fireEvent.click(
      within(directory).getByRole("button", { name: /门禁记录/ }),
    );

    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue(
      "未保存的调查员名称",
    );
    expect(
      within(screen.getByRole("region", { name: "对象详情与编辑" })).getByRole(
        "status",
      ),
    ).toHaveTextContent("请先保存或取消修改");

    fireEvent.click(screen.getByRole("button", { name: "取消修改" }));
    fireEvent.click(
      within(directory).getByRole("button", { name: /门禁记录/ }),
    );
    const content = screen.getByRole("textbox", { name: "正文" });
    fireEvent.change(content, { target: { value: "未保存的门禁正文" } });
    const relatedEvents = screen.getByRole("region", { name: "关联事件" });
    fireEvent.click(
      within(relatedEvents).getByRole("button", { name: /门禁开启/ }),
    );
    expect(screen.getByRole("textbox", { name: "正文" })).toHaveValue(
      "未保存的门禁正文",
    );

    fireEvent.click(screen.getByRole("button", { name: "取消修改" }));
    fireEvent.click(
      within(relatedEvents).getByRole("button", { name: /门禁开启/ }),
    );
    expect(screen.getByRole("textbox", { name: "标题" })).toHaveValue(
      "门禁开启",
    );
  });

  it("keeps unsaved object edits in place when project or Draft switching is requested", async () => {
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    render(<AnalystWorkbench requestedProjectId={42} />);

    const name = await screen.findByRole("textbox", { name: "名称" });
    fireEvent.change(name, { target: { value: "尚未保存的调查员" } });

    fireEvent.click(
      await screen.findByRole("button", { name: /项目.*真实测试卷宗/u }),
    );
    const otherProject = screen.getByRole("menuitem", { name: /另一卷宗/u });
    expect(fireEvent.click(otherProject)).toBe(false);
    expect(name).toHaveValue("尚未保存的调查员");

    fireEvent.click(
      screen.getByRole("button", { name: /当前工作稿.*真实测试卷宗/u }),
    );
    fireEvent.click(
      await screen.findByRole("menuitem", { name: /第二工作稿/u }),
    );

    expect(mocks.activateDraft).not.toHaveBeenCalled();
    expect(name).toHaveValue("尚未保存的调查员");
    expect(
      within(screen.getByRole("region", { name: "对象详情与编辑" })).getByRole(
        "status",
      ),
    ).toHaveTextContent("请先保存或取消修改");
  });

  it("reloads the authoritative Current Draft after a concurrent activation", async () => {
    const authoritative = {
      ...makeDraft(8, "服务端当前稿人物"),
      draft_id: 13,
      title: "服务端当前工作稿",
    };
    mocks.fetchCaseDraft
      .mockResolvedValueOnce(makeDraft(7))
      .mockResolvedValueOnce(authoritative);
    mocks.activateDraft.mockRejectedValueOnce(
      new ApiError(409, {
        code: "current_draft_changed",
        message: "当前工作稿已在其他位置切换。",
        details: { current_draft_id: 13 },
      }),
    );

    render(<AnalystWorkbench requestedProjectId={42} />);
    await screen.findByRole("textbox", { name: "搜索对象名称或编号" });
    fireEvent.click(
      screen.getByRole("button", { name: /当前工作稿.*真实测试卷宗/u }),
    );
    fireEvent.click(
      await screen.findByRole("menuitem", { name: /第二工作稿/u }),
    );

    await waitFor(() => expect(mocks.fetchCaseDraft).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByRole("button", {
        name: /当前工作稿.*服务端当前工作稿/u,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue(
      "服务端当前稿人物",
    );
    expect(mocks.loadProject).toHaveBeenCalledTimes(2);
  });

  it("reveals graph selections in the directory and highlights direct relations", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mocks.fetchCaseDraft.mockResolvedValueOnce(makeDraft(7));
    const { container } = render(
      <AnalystWorkbench requestedProjectId={42} />,
    );

    await screen.findByRole("textbox", {
      name: "搜索对象名称或编号",
    });
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    const graph = screen.getByRole("application", { name: "事件关系图" });
    expect(
      within(graph).getByRole("button", { name: /真实调查员/ }),
    ).toHaveAttribute("data-active", "true");
    expect(
      within(graph).getByRole("button", { name: /值班员/ }),
    ).toHaveAttribute("data-related", "true");

    fireEvent.click(
      within(graph).getByRole("button", { name: /值班员/ }),
    );
    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue(
      "值班员",
    );
    expect(
      screen.getByRole("button", { name: "实体，2 个匹配" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      within(graph).getByRole("button", { name: /值班员/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(container.querySelector('[data-mobile-region="inspector"]')).toBeTruthy();
    expect(
      consoleError.mock.calls.filter(([message]) =>
        String(message).includes("same key"),
      ),
    ).toHaveLength(0);
    consoleError.mockRestore();
  });

  it("uses the current revision and preserves edits after a 409 refresh", async () => {
    mocks.fetchCaseDraft
      .mockResolvedValueOnce(makeDraft(7))
      .mockResolvedValueOnce(makeDraft(8, "服务端更新名称"));
    mocks.patchCaseDraftObject.mockRejectedValueOnce(
      new ApiError(409, {
        code: "draft_revision_conflict",
        message: "CaseFile Draft revision is stale",
        details: {},
      }),
    );

    render(<AnalystWorkbench requestedProjectId={42} />);

    const name = await screen.findByRole("textbox", { name: "名称" });
    fireEvent.change(name, { target: { value: "我的未保存名称" } });
    fireEvent.click(
      screen.getByRole("button", { name: "保存到当前工作稿" }),
    );

    await waitFor(() =>
      expect(mocks.patchCaseDraftObject).toHaveBeenCalledWith(
        42,
        "ent_real_analyst",
        9,
        7,
        {
          name: "我的未保存名称",
          description: "负责核对真实 Draft 的调查员。",
        },
      ),
    );
    await waitFor(() => expect(mocks.fetchCaseDraft).toHaveBeenCalledTimes(2));

    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue(
      "我的未保存名称",
    );
    expect(screen.getByText("Draft R8 · 对象 R1")).toBeInTheDocument();
    const editor = screen.getByRole("region", { name: "对象详情与编辑" });
    expect(within(editor).getByRole("status")).toHaveTextContent(
      "工作稿已更新。你的输入已保留，请核对最新版后再次保存。",
    );
    expect(
      screen.getByRole("button", { name: "保存到当前工作稿" }),
    ).toBeEnabled();
  });
});
