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
import { ApiError, type DraftView } from "@/lib/api-client";

const mocks = vi.hoisted(() => ({
  adoptCandidate: vi.fn(),
  candidateStatus: vi.fn(),
  fetchCaseDraft: vi.fn(),
  loadProject: vi.fn(),
  patchCaseDraftObject: vi.fn(),
}));

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
  patchCaseDraftObject: mocks.patchCaseDraftObject,
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
    schema_version: "1.0",
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
          start: "2026-08-07T09:00:00Z",
          end: null,
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
    revision,
    schema_version: "v1",
    status: "open",
    content: makeCaseFile(entityName),
  };
}

beforeEach(() => {
  mocks.adoptCandidate.mockReset().mockResolvedValue(false);
  mocks.candidateStatus.mockReset();
  mocks.fetchCaseDraft.mockReset();
  mocks.loadProject.mockReset().mockResolvedValue(undefined);
  mocks.patchCaseDraftObject.mockReset();
});

afterEach(cleanup);

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
      within(drawer).getByText("真实来源内容尚未接入"),
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
    const graph = screen.getByRole("group", { name: "事件关系图" });
    expect(
      graph.querySelectorAll('g[data-active="true"]').length,
    ).toBeGreaterThan(0);

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
