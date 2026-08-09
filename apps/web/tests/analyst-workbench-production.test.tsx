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
    ],
    relationships: [],
    locations: [],
    events: [],
    information_units: [],
    claims: [],
    hypotheses: [],
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
    expect(mocks.loadProject).toHaveBeenCalledWith(42);
    expect(mocks.fetchCaseDraft).toHaveBeenCalledWith(42);
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
