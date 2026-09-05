import type { NarrativeIR, NovelCandidate, NovelPlanIR, NovelProfileV2, NovelRecommendation, SceneRender } from "@casefile/contracts";
import { apiRequest, getCompileArtifactContent, type TaskView } from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";
import type { NovelManuscript } from "./novel-document";

export interface NovelCompileScope { projectId: number; draftId: number; revision: number }
export interface NovelCompileRun {
  compile_run_id: number;
  draft_id: number;
  prose_renderer_shadow: boolean;
  compiler_profile_version_id: number;
  created_at: string;
  execution: TaskView;
  prose_shadow: { status: string; completed_scene_count?: number };
  artifacts: { artifact_id: number; schema_id: string; content_hash: string }[];
}
export interface NovelSettings { chapters: number; scenes: number; style: string }

export function novelProfile(settings: NovelSettings): NovelProfileV2 {
  if (!Number.isInteger(settings.chapters) || !Number.isInteger(settings.scenes) ||
      settings.chapters < 1 || settings.chapters > 100 || settings.scenes < settings.chapters ||
      settings.scenes > 500 || !settings.style.trim() || settings.style.trim().length > 2000) {
    throw new Error("请填写有效的章节数、场景数和写作风格；场景数不能少于章节数。");
  }
  return {
    schema_id: "compiler.novel-profile.v2",
    structure: { strategy: "three_act", target_chapters: settings.chapters, target_scenes: settings.scenes },
    allowed_presentation_modes: ["linear"], exposure_policy: "planner_default",
    prose: { language: "zh-CN", narrative_person: "third_person_limited", narrative_tense: "past",
      target_scene_chars: { min: 300, max: 1200 }, dialogue_ratio: { min: 0.1, max: 0.5 },
      description_density: "balanced", pacing: "balanced", style_brief: settings.style.trim(),
      forbidden_style_patterns: [] },
  };
}

export async function startNovelCompile(scope: NovelCompileScope, settings: NovelSettings, planningOnly = false) {
  const payload = novelProfile(settings);
  const profile = await apiRequest<{ current_version_id: number }>(
    `/projects/${scope.projectId}/compiler-profiles`, {
      actorId: LOCAL_ACTOR_ID, method: "POST",
      body: { profile_key: `novel.${crypto.randomUUID()}`, name: "小说编译", schema_id: payload.schema_id, payload },
    });
  return apiRequest<NovelCompileRun>(`/projects/${scope.projectId}/compile-runs`, {
    actorId: LOCAL_ACTOR_ID, method: "POST",
    body: { mode: "preview", expected_draft_id: scope.draftId, expected_draft_revision: scope.revision,
      compiler_profile_version_id: profile.current_version_id, planner_provider: "deepseek", prose_renderer_shadow: !planningOnly,
      ...(planningOnly ? { scene_compiler_shadow: true } : {}) },
  });
}

export async function listNovelCompiles(scope: NovelCompileScope) {
  const runs = await apiRequest<NovelCompileRun[]>(`/projects/${scope.projectId}/compile-runs`, { actorId: LOCAL_ACTOR_ID });
  return runs.filter((run) => run.draft_id === scope.draftId)
    .sort((a, b) => b.compile_run_id - a.compile_run_id);
}

export function requestNovelRecommendation(scope: NovelCompileScope, preferences: string) {
  return apiRequest<NovelRecommendation>(`/projects/${scope.projectId}/novel-recommendation`, {
    actorId: LOCAL_ACTOR_ID, method: "POST", body: { expected_draft_id: scope.draftId,
      expected_draft_revision: scope.revision, preferences },
  });
}

export function confirmNovelPlan(scope: NovelCompileScope, run: NovelCompileRun) {
  return apiRequest<NovelCompileRun>(`/projects/${scope.projectId}/compile-runs`, {
    actorId: LOCAL_ACTOR_ID, method: "POST", body: { mode: "preview", expected_draft_id: scope.draftId,
      expected_draft_revision: scope.revision, compiler_profile_version_id: run.compiler_profile_version_id,
      planner_provider: "deepseek", prose_renderer_shadow: true, approved_plan_run_id: run.compile_run_id },
  });
}

export interface NovelPlanPreview { plan: NovelPlanIR; names: Record<string, string> }
export async function loadNovelPlan(projectId: number, run: NovelCompileRun): Promise<NovelPlanPreview> {
  const artifact = run.artifacts.find((item) => item.schema_id === "compiler.novel-plan.v1");
  const source = run.artifacts.find((item) => item.schema_id === "compiler.narrative-ir.v1");
  if (!artifact || !source) throw new Error("方案尚未完成，请稍后查看。");
  const [planResult, sourceResult] = await Promise.all([
    getCompileArtifactContent(LOCAL_ACTOR_ID, projectId, run.compile_run_id, artifact.artifact_id),
    getCompileArtifactContent(LOCAL_ACTOR_ID, projectId, run.compile_run_id, source.artifact_id),
  ]);
  if (planResult.content_hash !== artifact.content_hash || sourceResult.content_hash !== source.content_hash) {
    throw new Error("方案读取不一致，请刷新后重试。");
  }
  const plan = planResult.content as unknown as NovelPlanIR;
  const ir = sourceResult.content as unknown as NarrativeIR;
  const names: Record<string, string> = {};
  for (const group of Object.values(ir.objects)) for (const item of group) {
    const value = item.value as Record<string, unknown>;
    const name = value.name ?? value.title ?? value.statement ?? value.description;
    if (typeof name === "string" && typeof item.object_ref.object_id === "string") names[item.object_ref.object_id] = name;
  }
  return { plan, names };
}

export function completedNovelArtifact(run: NovelCompileRun) {
  return run.execution.status === "succeeded" && run.prose_shadow.status === "succeeded"
    ? run.artifacts.find((artifact) => artifact.schema_id === "compiler.novel-candidate.v1") : undefined;
}

export async function loadCompiledNovel(projectId: number, run: NovelCompileRun, title: string): Promise<NovelManuscript> {
  const artifact = completedNovelArtifact(run);
  if (!artifact) throw new Error("这次编译尚未生成完整小说，请查看编译状态。");
  const result = await getCompileArtifactContent(LOCAL_ACTOR_ID, projectId, run.compile_run_id, artifact.artifact_id);
  const candidate = result.content as unknown as NovelCandidate;
  if (result.schema_id !== "compiler.novel-candidate.v1" || result.content_hash !== artifact.content_hash ||
      candidate.schema_id !== "compiler.novel-candidate.v1" || typeof candidate.merged_text !== "string" || !candidate.merged_text.trim()) {
    throw new Error("小说产物内容不完整，无法载入。");
  }
  const planArtifact = run.artifacts.find((item) => item.schema_id === "compiler.novel-plan.v1");
  if (!planArtifact) throw new Error("小说章节结构缺失，无法载入。");
  const planResult = await getCompileArtifactContent(LOCAL_ACTOR_ID, projectId, run.compile_run_id, planArtifact.artifact_id);
  const plan = planResult.content as unknown as NovelPlanIR;
  const renders: SceneRender[] = [];
  for (let offset = 0; offset < candidate.accepted_scenes.length; offset += 8) {
    renders.push(...await Promise.all(candidate.accepted_scenes.slice(offset, offset + 8).map(async (scene) => {
      const item = run.artifacts.find((a) => a.schema_id === "compiler.scene-render.v1" && a.content_hash === scene.render_hash);
      if (!item) throw new Error("小说场景正文缺失，无法载入。");
      const response = await getCompileArtifactContent(LOCAL_ACTOR_ID, projectId, run.compile_run_id, item.artifact_id);
      const render = response.content as unknown as SceneRender;
      if (response.content_hash !== scene.render_hash || render.stage !== "accepted" || render.scene_id !== scene.scene_id ||
          render.source.scene_plan_hash !== candidate.scene_plan_hash) throw new Error("小说场景来源不一致，无法载入。");
      return render;
    })));
  }
  const chapters = compiledChapters(candidate, plan, renders);
  return { title, chapters,
    id: `compile-${run.compile_run_id}-${artifact.artifact_id}`, sourceLabel: "小说编译初稿" };
}

export function compiledChapters(candidate: NovelCandidate, plan: NovelPlanIR, renders: SceneRender[]) {
  const ordered = [...renders].sort((a, b) => a.scene_ordinal - b.scene_ordinal);
  const scenes = [...plan.scenes].sort((a, b) => a.discourse_order - b.discourse_order);
  const text = (render: SceneRender) => render.blocks.map((block) => block.text).join("\n\n");
  if (ordered.length !== candidate.scene_count || scenes.length !== ordered.length ||
      ordered.some((render, index) => render.scene_id !== scenes[index].scene_id) ||
      ordered.map(text).join("\n\n") !== candidate.merged_text) {
    throw new Error("小说正文与章节结构不一致，无法载入。");
  }
  const chapters = [...plan.chapters].sort((a, b) => a.ordinal - b.ordinal).map((chapter) => ({
    id: chapter.chapter_id, title: chapter.title,
    text: ordered.filter((_, index) => scenes[index].chapter_id === chapter.chapter_id).map(text).join("\n\n"),
  }));
  if (chapters.some((chapter) => !chapter.text.trim()) || chapters.map((chapter) => chapter.text).join("\n\n") !== candidate.merged_text) {
    throw new Error("小说章节不完整，无法载入。");
  }
  return chapters;
}
