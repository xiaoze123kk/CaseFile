import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NovelCandidate, NovelPlanIR, SceneRender } from "@casefile/contracts";
import { compiledChapters, completedNovelArtifact, listNovelCompiles, loadCompiledNovel,
  novelProfile, startNovelCompile, type NovelCompileRun } from "@/features/novel-workspace/novel-compiler-api";
import { NovelCompilerPanel, novelCompileStatus } from "@/features/novel-workspace/novel-compiler-panel";
import { NovelPlanOutline } from "@/features/novel-workspace/novel-plan-preview";
import { NovelWorkspace } from "@/features/novel-workspace/novel-workspace";
import { defaultWorkbenchSeed } from "@/features/analyst-workbench/analyst-fixture";
import { apiRequest, getCompileArtifactContent } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (original) => ({
  ...await original<typeof import("@/lib/api-client")>(), apiRequest: vi.fn(), getCompileArtifactContent: vi.fn(),
}));
const scope = { projectId: 7, draftId: 9, revision: 12 };
const settings = { chapters: 2, scenes: 3, style: "简洁而克制" };
const artifact = { artifact_id: 4, schema_id: "compiler.novel-candidate.v1", content_hash: "candidate" };
const run = (status = "succeeded", prose = "succeeded") => ({
  compile_run_id: 3, draft_id: 9, prose_renderer_shadow: true, created_at: "2026-09-05T01:00:00Z",
  execution: { status, input_draft_revision: 12, task_run_id: 8 },
  prose_shadow: { status: prose }, artifacts: [artifact],
}) as NovelCompileRun;
const candidate = { schema_id: "compiler.novel-candidate.v1", scene_count: 2, scene_plan_hash: "plan", profile_hash: "profile", character_count: 14,
  merged_text: "窗外下起雨。\n\n门被推开。", accepted_scenes: [
    { scene_id: "s1", scene_ordinal: 1, render_hash: "r1" }, { scene_id: "s2", scene_ordinal: 2, render_hash: "r2" },
  ] } as NovelCandidate;
const plan = { chapters: [{ chapter_id: "c1", ordinal: 1, title: "雨声" }, { chapter_id: "c2", ordinal: 2, title: "来客" }],
  scenes: [{ scene_id: "s1", chapter_id: "c1", discourse_order: 1 }, { scene_id: "s2", chapter_id: "c2", discourse_order: 2 }],
} as unknown as NovelPlanIR;
const renders = ["窗外下起雨。", "门被推开。"].map((text, index) => ({
  scene_id: `s${index + 1}`, scene_ordinal: index + 1, stage: "accepted", source: { scene_plan_hash: "plan" }, blocks: [{ text }],
})) as unknown as SceneRender[];

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
afterEach(cleanup);

describe("小说编译 API 适配", () => {
  it("冻结当前工作稿和新配置，显式启用完整正文编译", async () => {
    vi.mocked(apiRequest).mockResolvedValueOnce({ current_version_id: 25 }).mockResolvedValueOnce(run("queued", "pending"));
    await startNovelCompile(scope, settings);
    expect(apiRequest).toHaveBeenNthCalledWith(1, "/projects/7/compiler-profiles", expect.objectContaining({ body: expect.objectContaining({ payload: novelProfile(settings) }) }));
    expect(apiRequest).toHaveBeenNthCalledWith(2, "/projects/7/compile-runs", expect.objectContaining({ body: {
      mode: "preview", expected_draft_id: 9, expected_draft_revision: 12, compiler_profile_version_id: 25,
      planner_provider: "deepseek", prose_renderer_shadow: true,
    } }));
  });
  it("拒绝无效结构并隔离其他工作稿和历史结构编译", async () => {
    await expect(startNovelCompile(scope, { ...settings, scenes: 1 })).rejects.toThrow("场景数");
    expect(apiRequest).not.toHaveBeenCalled();
    vi.mocked(apiRequest).mockResolvedValue([run(), { ...run(), draft_id: 99 }, { ...run(), prose_renderer_shadow: false }]);
    expect(await listNovelCompiles(scope)).toEqual([run(), { ...run(), prose_renderer_shadow: false }]);
  });
  it("结构成功不等于正文成功，拒绝未完整通过的候选", async () => {
    for (const prose of ["running", "blocked_precondition", "semantic_rejected", "inconclusive_infrastructure"]) {
      expect(completedNovelArtifact(run("succeeded", prose))).toBeUndefined();
      await expect(loadCompiledNovel(7, run("succeeded", prose), "小说")).rejects.toThrow("尚未");
    }
    expect(getCompileArtifactContent).not.toHaveBeenCalled();
    expect(novelCompileStatus(run("succeeded", "semantic_rejected"))).toContain("未通过");
  });
  it("按真实章节分组完整正文，拒绝缺失和错序内容", () => {
    expect(compiledChapters(candidate, plan, renders)).toEqual([
      { id: "c1", title: "雨声", text: "窗外下起雨。" }, { id: "c2", title: "来客", text: "门被推开。" },
    ]);
    expect(() => compiledChapters(candidate, plan, renders.slice(0, 1))).toThrow("不一致");
    expect(() => compiledChapters({ ...candidate, merged_text: "错误正文" }, plan, renders)).toThrow("不一致");
  });
  it("读取同一次编译的已接受场景并保留稳定初稿身份", async () => {
    const complete = { ...run(), artifacts: [artifact,
      { artifact_id: 5, schema_id: "compiler.novel-plan.v1", content_hash: "p" },
      ...[1, 2].map((n) => ({ artifact_id: n + 5, schema_id: "compiler.scene-render.v1", content_hash: `r${n}` })),
    ] };
    vi.mocked(getCompileArtifactContent).mockImplementation(async (_actor, _project, _run, id) => ({
      schema_id: id === 4 ? candidate.schema_id : "other", content_hash: id === 4 ? "candidate" : `r${id - 5}`,
      content: id === 4 ? candidate : id === 5 ? plan : renders[id - 6],
    }) as never);
    const result = await loadCompiledNovel(7, complete, "雨夜");
    expect(result.id).toBe("compile-3-4");
    expect(result.chapters).toHaveLength(2);
    expect(result.chapters[1].text).toBe("门被推开。");
  });
});

describe("小说编译工作表面", () => {
  it("明确说明模型输出截断原因", () => {
    const failed = run("failed", "pending");
    failed.execution.error_code = "compiler_model_output_truncated";
    expect(novelCompileStatus(failed)).toContain("方案被截断");
  });
  it.each(["queued", "running", "cancelling", "succeeded", "failed", "cancelled"])("编译动效跟随真实任务状态：%s", async (status) => {
    vi.mocked(apiRequest).mockResolvedValue([run(status, status === "succeeded" ? "succeeded" : "pending")]);
    const { container } = render(<NovelCompilerPanel scope={scope} title="雨夜" hasDraft={false} onLoad={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(container.querySelector(`[data-execution-state="${status}"]`)).toBeInTheDocument());
    const indicator = container.querySelector("[data-compile-activity]");
    if (["queued", "running", "cancelling"].includes(status)) expect(indicator).toHaveAttribute("aria-hidden", "true");
    else expect(indicator).not.toBeInTheDocument();
  });
  it("用户不用填数字，推荐后只生成方案，不自动写正文", async () => {
    const recommendation = { ...settings, concept: "围绕失踪来客的紧凑谜案", rationale: "将调查和揭晓分开呈现。" };
    const queued = { ...run("queued", "disabled"), prose_renderer_shadow: false };
    vi.mocked(apiRequest).mockImplementation(async (url, options) => {
      if (url.endsWith("novel-recommendation")) return recommendation;
      if (url.endsWith("compiler-profiles")) return { current_version_id: 25 };
      if (options.method === "POST") return queued;
      return [];
    });
    render(<NovelCompilerPanel scope={scope} title="雨夜" hasDraft={false} onLoad={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "让 Agent 推荐小说方案" })).toBeEnabled());
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "让 Agent 推荐小说方案" }));
    await screen.findByText(recommendation.concept);
    expect(apiRequest).toHaveBeenCalledWith("/projects/7/compile-runs", expect.objectContaining({ body: expect.objectContaining({
      prose_renderer_shadow: false, scene_compiler_shadow: true,
    }) }));
    expect(vi.mocked(apiRequest).mock.calls.filter(([, options]) =>
      (options.body as { prose_renderer_shadow?: boolean } | undefined)?.prose_renderer_shadow)).toHaveLength(0);
  });
  it("按章节给出具体场景的人物、地点、事件和叙事作用", () => {
    const scene = { ...plan.scenes[0], intent: "林岚在候船室核验离港记录", purpose: "investigation",
      participant_refs: [{ object_id: "person" }], pov_ref: { object_id: "person" },
      location_ref: { object_id: "place" }, event_refs: [{ object_id: "event" }],
    } as NovelPlanIR["scenes"][number];
    render(<NovelPlanOutline preview={{ plan: { ...plan, scenes: [scene] }, names: {
      person: "林岚", place: "候船室", event: "核对最后一班渡轮的离港记录",
    } }} />);
    expect(screen.getByText("林岚在候船室核验离港记录")).toBeInTheDocument();
    expect(screen.getByText("候船室")).toBeInTheDocument();
    expect(screen.getByText("核对最后一班渡轮的离港记录")).toBeInTheDocument();
    expect(screen.getByText("叙事作用")).toBeInTheDocument();
  });
  it("真实工作稿显示编译入口，已有小说编辑稿保持可读", () => {
    render(<NovelWorkspace seed={defaultWorkbenchSeed} scopeKey="test" compileScope={scope} onBack={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "小说编译" }));
    expect(screen.getByRole("dialog", { name: "小说编译" })).toBeTruthy();
  });
  it("恢复运行中的记录，禁止重复启动，支持停止", async () => {
    vi.mocked(apiRequest).mockResolvedValue([run("running", "running")]);
    render(<NovelCompilerPanel scope={scope} title="雨夜" hasDraft={false} onLoad={vi.fn()} onClose={vi.fn()} />);
    await screen.findByRole("button", { name: "停止编译" });
    expect(screen.getByRole("button", { name: "让 Agent 推荐小说方案" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "停止编译" }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/projects/7/tasks/8/cancel", expect.objectContaining({ method: "POST" })));
  });
  it("读取失败明确报错并阻止启动，刷新后可恢复", async () => {
    vi.mocked(apiRequest).mockRejectedValueOnce(new Error("服务暂不可用")).mockResolvedValue([]);
    render(<NovelCompilerPanel scope={scope} title="雨夜" hasDraft onLoad={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂不可用");
    expect(screen.getByRole("button", { name: "让 Agent 推荐小说方案" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "让 Agent 推荐小说方案" })).toBeEnabled());
  });
});
