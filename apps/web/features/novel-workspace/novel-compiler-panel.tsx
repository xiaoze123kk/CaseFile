"use client";

import { useEffect, useRef, useState } from "react";
import type { NovelRecommendation } from "@casefile/contracts";
import { NovelPlanOutline } from "./novel-plan-preview";
import { cancelTask } from "@/features/case-session/case-session-api";
import { errorMessage } from "@/lib/api-client";
import { Dialog } from "./novel-workspace-panels";
import { completedNovelArtifact, listNovelCompiles, loadCompiledNovel, startNovelCompile,
  requestNovelRecommendation, confirmNovelPlan, loadNovelPlan, resumeNovelCompile,
  type NovelPlanPreview, type NovelCompileRun, type NovelCompileScope } from "./novel-compiler-api";
import type { NovelManuscript } from "./novel-document";
import styles from "./novel-compiler.module.css";

const active = (run: NovelCompileRun) => ["queued", "running", "cancelling"].includes(run.execution.status);
export function novelCompileStatus(run: NovelCompileRun) {
  if (completedNovelArtifact(run)) return "小说已完成";
  if (!run.prose_renderer_shadow && run.execution.status === "succeeded") return run.artifacts.some((a) => a.schema_id === "compiler.novel-plan.v1") ? "小说方案已就绪" : "本次未生成章节方案";
  if (run.execution.status === "cancelled") return "已停止";
  if (run.execution.status === "cancelling") return "正在停止";
  if (run.execution.status === "failed") {
    const reasons: Record<string, string> = {
      compiler_skeleton_proposal_invalid: "小说结构方案不完整或格式不符合要求，未进入正文生成",
      compiler_model_output_truncated: "模型输出达到长度上限，方案被截断，请重新编译",
      compiler_model_output_invalid_json: "模型未返回完整的结构化方案，请重新编译",
      compiler_model_output_incomplete: "模型未完成方案输出，请重新编译",
    };
    return reasons[run.execution.error_code ?? ""] ?? run.execution.failure?.message ?? "编译失败，请检查设置或工作稿后重新编译";
  }
  if (run.prose_shadow.status === "semantic_rejected") return "正文未通过一致性校验";
  if (run.prose_shadow.status === "blocked_precondition") return "正文生成条件未满足";
  if (run.prose_shadow.status === "inconclusive_infrastructure") return "正文生成中断，可重新编译";
  if (run.execution.status === "queued") return "排队中，等待编译服务";
  if (run.execution.status === "succeeded") return "本次未生成完整小说";
  if (run.artifacts.some((a) => a.schema_id === "compiler.scene-plan.v2")) return run.prose_renderer_shadow ? "正在撰写、校验与润色正文" : "正在校验场景方案";
  if (run.artifacts.some((a) => a.schema_id === "compiler.novel-plan.v1")) return "正在编排场景";
  return "正在规划小说结构";
}

export function NovelCompilerPanel({ scope, title, hasDraft, onLoad, onClose }: {
  scope: NovelCompileScope; title: string; hasDraft: boolean;
  onLoad: (manuscript: NovelManuscript) => boolean; onClose: () => void;
}) {
  const [runs, setRuns] = useState<NovelCompileRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [preferences, setPreferences] = useState("");
  const [recommendation, setRecommendation] = useState<NovelRecommendation | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [previewLoad, setPreviewLoad] = useState<{ id: number; data?: NovelPlanPreview; error?: string } | null>(null);
  const lock = useRef(false);
  const mounted = useRef(true);
  const { projectId, draftId, revision } = scope;
  const novelAttempts = runs.filter((run) => run.prose_renderer_shadow && !active(run) && run.execution.status !== "cancelled");
  const novelReady = novelAttempts.filter((run) => completedNovelArtifact(run)).length;
  const measuredAttempts = novelAttempts.filter((run) => run.stability);
  const firstPass = measuredAttempts.filter((run) => run.stability?.first_pass_success).length;
  const repairs = runs.reduce((sum, run) => sum + (run.stability?.repair_attempts ?? 0), 0);
  const repaired = runs.reduce((sum, run) => sum + (run.stability?.repair_successes ?? 0), 0);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await listNovelCompiles({ projectId, draftId, revision });
        if (disposed) return;
        setRuns(next);
        setAvailable(true);
      } catch (cause) {
        if (!disposed) { setAvailable(false); setError(errorMessage(cause)); }
      } finally {
        if (!disposed) {
          setLoading(false);
          timer = setTimeout(() => { void poll(); }, 3000);
        }
      }
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [projectId, draftId, revision, refresh]);

  const selectedRun = runs.find((run) => run.compile_run_id === selectedId) ?? runs.find((run) =>
    !run.prose_renderer_shadow && run.execution.status === "succeeded" && run.artifacts.some((a) => a.schema_id === "compiler.novel-plan.v1"));
  const selectedRunId = selectedRun?.compile_run_id;
  const planArtifactId = selectedRun?.artifacts.find((a) => a.schema_id === "compiler.novel-plan.v1")?.artifact_id;
  const preview = previewLoad?.id === selectedRunId ? previewLoad?.data : undefined;
  const previewError = previewLoad?.id === selectedRunId ? previewLoad?.error : undefined;
  useEffect(() => {
    let disposed = false;
    if (selectedRun && planArtifactId && previewLoad?.id !== selectedRunId) {
      void loadNovelPlan(projectId, selectedRun).then((data) => {
        if (!disposed) setPreviewLoad({ id: selectedRun.compile_run_id, data });
      }).catch((cause) => {
        if (!disposed) setPreviewLoad({ id: selectedRun.compile_run_id, error: errorMessage(cause) });
      });
    }
    return () => { disposed = true; };
  }, [projectId, selectedRun, selectedRunId, planArtifactId, previewLoad?.id]);

  async function action(work: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try { await work(); }
    catch (cause) { if (mounted.current) setError(errorMessage(cause)); }
    finally {
      lock.current = false;
      if (mounted.current) { setBusy(false); setRefresh((n) => n + 1); }
    }
  }
  return <Dialog title="小说编译" onClose={onClose}>
    <div className={styles.content}>
      <div className={styles.introduction}><span>先看推荐，再确认规划</span><h3>让 Agent 帮你判断，这个故事适合怎样写。</h3>
        <p>根据卷宗中的谜题、人物和事件，推荐篇幅与文风。确认推荐后再规划章节与具体场景，审阅章节后再生成正文。</p></div>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (loading || !available || runs.some(active)) return;
        void action(async () => {
          setRecommendation(null);
          const next = await requestNovelRecommendation(scope, preferences);
          if (!mounted.current) return;
          setRecommendation(next);
        });
      }}>
        <fieldset disabled={busy || loading || !available || runs.some(active)} className={styles.fields}>
          <label className={styles.style}>你想要的阅读感受（选填）<textarea maxLength={2000} value={preferences}
            placeholder="还没想好可以留空。也可以说：希望一口气读完，偏重推理，结尾不要解释太多。"
            onChange={(e) => setPreferences(e.target.value)} /></label>
          <button type="submit" className={styles.prepareButton} aria-busy={busy}>
            {busy ? <span className={styles.prepareSpinner} aria-hidden="true" /> : null}
            {busy ? "Agent 正在准备…" : "让 Agent 推荐小说方案"}
          </button>
          <p>先获取推荐，确认后才开始章节规划。</p>
        </fieldset>
      </form>
      {recommendation ? <section className={styles.recommendation} aria-label="Agent 推荐">
        <span>Agent 推荐</span><h3>{recommendation.concept}</h3><p>{recommendation.rationale}</p>
        <p><strong>文风建议：</strong>{recommendation.style}</p>
        <button type="button" className={styles.confirm} disabled={busy || !available || runs.some(active)}
          onClick={() => void action(async () => {
            const run = await startNovelCompile(scope, recommendation, true);
            if (mounted.current) {
              setRuns((items) => [run, ...items.filter((item) => item.compile_run_id !== run.compile_run_id)]);
              setSelectedId(run.compile_run_id);
              setRecommendation(null);
            }
          })}>确认推荐，开始章节规划</button>
      </section> : null}
      {selectedRun && !preview && planArtifactId && !previewError ? <p role="status">正在读取章节与场景…</p> : null}
      {previewError ? <p role="alert">{previewError}</p> : null}
      {preview && selectedRun ? <>
        <NovelPlanOutline preview={preview} />
        {selectedRun.execution.input_draft_revision !== revision ? <p role="alert">工作稿已更新，这份方案仅供查看。请重新推荐后再生成正文。</p> :
          <button type="button" className={styles.confirm} disabled={busy || runs.some(active) || selectedRun.execution.status !== "succeeded"}
            onClick={() => void action(async () => {
              const run = await confirmNovelPlan(scope, selectedRun);
              if (mounted.current) setRuns((items) => [run, ...items]);
            })}>按这份方案生成小说</button>}
      </> : null}
      {error ? <p role="alert">{error}</p> : null}
      <div className={styles.heading}><h3>编译记录</h3><button type="button" disabled={busy} onClick={() => { setError(""); setPreviewLoad(null); setRefresh((n) => n + 1); }}>刷新</button></div>
      {novelAttempts.length ? <p aria-label="小说生成统计">
        完整小说 {novelReady}/{novelAttempts.length} 次（{Math.round(novelReady / novelAttempts.length * 100)}%）
        {measuredAttempts.length ? ` · 首次通过 ${firstPass}/${measuredAttempts.length} 次` : ""}
        {repairs ? ` · 修复调用成功 ${repaired}/${repairs} 次` : ""}。仅统计已结束的正文生成，方案规划与主动取消不计入。
      </p> : null}
      {loading ? <p role="status">正在读取编译记录…</p> : !runs.length ? <p>当前工作稿还没有小说编译记录。</p> : null}
      <ul className={styles.runs}>{runs.map((run) => <li key={run.compile_run_id} data-execution-state={run.execution.status}>
        {active(run) ? <span className={styles.compileActivity} aria-hidden="true" data-compile-activity>
          <span /><span /><span />
        </span> : null}
        <div><strong role="status">{novelCompileStatus(run)}</strong><small>{new Date(run.created_at).toLocaleString("zh-CN")} · 工作稿版本 {run.execution.input_draft_revision}</small></div>
        {run.stability && Object.keys(run.stability.failure_stages).length ? <small>
          {Object.entries(run.stability.failure_stages).map(([stage, count]) => `${stage}失败 ${count} 次`).join(" · ")}
        </small> : null}
        {run.artifacts.some((a) => a.schema_id === "compiler.novel-plan.v1") ? <button type="button" disabled={busy}
          onClick={() => { setSelectedId(run.compile_run_id); setRecommendation(null); }}>查看场景方案</button> : null}
        {active(run) ? <button type="button" disabled={busy || run.execution.status === "cancelling"} onClick={() => void action(async () => { await cancelTask(projectId, run.execution.task_run_id); })}>停止编译</button> : null}
        {run.prose_shadow.plan_issues?.length ? <p role="alert">场景衔接需要调整：{run.prose_shadow.plan_issues.join("；")}</p> : null}
        {run.prose_renderer_shadow && run.prose_shadow.completed_scene_count ? <p>已保存 {run.prose_shadow.completed_scene_count} 个场景，继续时保留已完成正文。</p> : null}
        {(run.execution.status === "failed" || run.prose_shadow.resume_available) ? <button type="button" disabled={busy || runs.some(active)}
          onClick={() => void action(async () => {
            const resumed = await resumeNovelCompile(scope, run.compile_run_id);
            if (mounted.current) setRuns((items) => items.map((item) =>
              item.compile_run_id === resumed.compile_run_id ? resumed : item));
          })}>从失败处继续</button> : null}
        {completedNovelArtifact(run) ? <button type="button" disabled={busy} onClick={() => void action(async () => {
          const manuscript = await loadCompiledNovel(projectId, run, title);
          if (mounted.current) {
            if (onLoad(manuscript)) onClose();
            else throw new Error("旧稿备份失败，请先关闭编译窗口并导出旧稿，再载入小说。");
          }
        })}>载入小说</button> : null}
      </li>)}</ul>
      <p>{hasDraft ? "载入时会备份当前本地编辑稿；可在历史稿中恢复。" : "完成的小说将作为独立初稿载入，不会修改卷宗。"} 使用已配置的 DeepSeek；方案推荐和正文生成会产生模型调用费用。关闭窗口后，已提交的编译仍会继续。</p>
    </div>
  </Dialog>;
}
