"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  apiRequest,
  errorMessage,
  streamTaskEvents,
  type BriefContent,
  type BriefVersionView,
  type BriefView,
  type DraftView,
  type ProviderSettingView,
  type TaskEventView,
  type TaskView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./workflow.module.css";

const editableFields: Array<{
  key: keyof Pick<
    BriefContent,
    "source_text" | "one_line_concept" | "core_mystery" | "player_goal" | "gameplay_loop"
  >;
  label: string;
}> = [
  { key: "source_text", label: "原始创意" },
  { key: "one_line_concept", label: "一句话概念" },
  { key: "core_mystery", label: "核心谜题" },
  { key: "player_goal", label: "玩家目标" },
  { key: "gameplay_loop", label: "玩法循环" },
];

function asBriefContent(value: BriefView["content"]): BriefContent | null {
  return "source_text" in value ? (value as BriefContent) : null;
}

function mergeEvent(current: TaskEventView[], incoming: TaskEventView) {
  if (current.some((event) => event.sequence_no === incoming.sequence_no)) return current;
  return [...current, incoming].sort((left, right) => left.sequence_no - right.sequence_no);
}

function eventSummary(event: TaskEventView) {
  const payload = event.payload;
  const parts = [payload.message, payload.tool ? `工具：${payload.tool}` : null];
  if (payload.model_id) parts.push(`模型：${payload.model_id}`);
  if (payload.object_count !== undefined) parts.push(`对象：${payload.object_count}`);
  if (payload.valid !== undefined) parts.push(payload.valid ? "结构有效" : "结构无效");
  if (payload.content_hash) parts.push(`HASH ${String(payload.content_hash).slice(0, 12)}…`);
  if (payload.usage) parts.push(`用量：${JSON.stringify(payload.usage)}`);
  return parts.filter(Boolean).join(" · ") || "阶段状态已更新";
}

export function BriefWorkspace() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [editedContent, setContent] = useState<BriefContent | null>(null);
  const [dirty, setDirty] = useState(false);
  const [confirmationRequired, setNeedsConfirm] = useState<boolean | null>(null);
  const [events, setEvents] = useState<TaskEventView[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [completionOpen, setCompletionOpen] = useState(false);
  const lastEventIdRef = useRef(0);

  const briefQuery = useQuery({
    queryKey: ["brief", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<BriefView>(`/projects/${workflow.projectId}/brief`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });
  const draftQuery = useQuery({
    queryKey: ["draft", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<DraftView>(`/projects/${workflow.projectId}/draft`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });
  const providerQuery = useQuery({
    queryKey: ["provider-setting", workflow.actorId],
    queryFn: () =>
      apiRequest<ProviderSettingView | null>("/settings/provider", { actorId: workflow.actorId }),
  });
  const taskQuery = useQuery({
    queryKey: ["task", workflow.actorId, workflow.projectId, workflow.taskRunId],
    queryFn: () =>
      apiRequest<TaskView>(
        `/projects/${workflow.projectId}/tasks/${workflow.taskRunId}`,
        { actorId: workflow.actorId },
      ),
    enabled: workflow.projectId !== null && workflow.taskRunId !== null,
    refetchInterval: (query) =>
      query.state.data && ["succeeded", "failed", "cancelled"].includes(query.state.data.status)
        ? false
        : 1_500,
  });

  const loadedContent = briefQuery.data ? asBriefContent(briefQuery.data.content) : null;
  const content = editedContent ?? loadedContent;
  const needsConfirm =
    confirmationRequired ?? briefQuery.data?.current_version_id === null;
  const completionVisible = completionOpen || taskQuery.data?.status === "succeeded";

  useEffect(() => {
    if (workflow.projectId === null || workflow.taskRunId === null) return;
    const controller = new AbortController();
    streamTaskEvents(
      `/projects/${workflow.projectId}/tasks/${workflow.taskRunId}/stream`,
      workflow.actorId,
      (event) => {
        lastEventIdRef.current = Math.max(lastEventIdRef.current, event.sequence_no);
        setEvents((current) => mergeEvent(current, event));
        if (["task.succeeded", "task.failed"].includes(event.event_type)) {
          void queryClient.invalidateQueries({
            queryKey: ["task", workflow.actorId, workflow.projectId, workflow.taskRunId],
          });
          void queryClient.invalidateQueries({
            queryKey: ["draft", workflow.actorId, workflow.projectId],
          });
        }
        if (event.event_type === "task.succeeded") setCompletionOpen(true);
      },
      controller.signal,
      lastEventIdRef.current,
    ).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setStreamError(errorMessage(error));
    });
    return () => controller.abort();
  }, [queryClient, workflow.actorId, workflow.projectId, workflow.taskRunId]);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiRequest<BriefView>(`/projects/${workflow.projectId}/brief`, {
        actorId: workflow.actorId,
        method: "PUT",
        body: {
          expected_revision: briefQuery.data?.draft_revision,
          content,
        },
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(["brief", workflow.actorId, workflow.projectId], saved);
      setDirty(false);
      setNeedsConfirm(true);
    },
  });

  const confirmMutation = useMutation({
    mutationFn: () =>
      apiRequest<BriefVersionView>(`/projects/${workflow.projectId}/brief/confirm`, {
        actorId: workflow.actorId,
        method: "POST",
        body: { expected_revision: briefQuery.data?.draft_revision },
      }),
    onSuccess: async (version) => {
      setNeedsConfirm(false);
      queryClient.setQueryData(["confirmed-brief", workflow.projectId], version);
      await queryClient.invalidateQueries({
        queryKey: ["brief", workflow.actorId, workflow.projectId],
      });
    },
  });

  const generateMutation = useMutation({
    mutationFn: async () => {
      const briefVersionId = briefQuery.data?.current_version_id;
      if (!briefVersionId || !draftQuery.data) throw new Error("请先确认 Brief 并等待 Draft 状态读取完成。");
      return apiRequest<TaskView>(`/projects/${workflow.projectId}/tasks/generate`, {
        actorId: workflow.actorId,
        method: "POST",
        body: {
          brief_version_id: briefVersionId,
          expected_draft_revision: draftQuery.data.revision,
        },
      });
    },
    onSuccess: (task) => {
      lastEventIdRef.current = 0;
      setEvents([]);
      setStreamError(null);
      setCompletionOpen(false);
      workflow.setTask(task.task_run_id);
    },
  });

  const activeTask = taskQuery.data;
  const running = activeTask && !["succeeded", "failed", "cancelled"].includes(activeTask.status);
  const displayedError = useMemo(
    () => saveMutation.error ?? confirmMutation.error ?? generateMutation.error,
    [confirmMutation.error, generateMutation.error, saveMutation.error],
  );

  if (!workflow.ready || briefQuery.isLoading || draftQuery.isLoading) {
    return <main className={styles.centerState}>正在读取 Brief 契约与 Draft 状态…</main>;
  }
  if (workflow.projectId === null) {
    return (
      <main className={styles.centerState}>
        <p>尚未创建真实项目。</p>
        <button className={styles.primaryButton} onClick={() => router.push("/")} type="button">
          返回建案中心
        </button>
      </main>
    );
  }
  if (!content) {
    return <main className={styles.centerState}>Brief 尚无可审阅内容，请返回建案中心重新保存。</main>;
  }

  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}>
        <div>
          <small>BRIEF CONTRACT / PROJECT #{workflow.projectId}</small>
          <h1>确认生成输入，再启动单 Agent</h1>
          <p>保存只更新 Brief 草稿；“确认”会冻结一个不可变 BriefVersion；“开始生成”才创建 TaskRun。</p>
        </div>
        <div className={styles.headerFacts}>
          <span>BRIEF REV.{briefQuery.data?.draft_revision}</span>
          <span>DRAFT REV.{draftQuery.data?.revision}</span>
        </div>
      </header>

      <div className={styles.briefGrid}>
        <form
          className={styles.briefSheet}
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <header>
            <div>
              <small>FORMAL BRIEF / V1</small>
              <h2>{content.one_line_concept}</h2>
            </div>
            <span>{dirty ? "未保存" : needsConfirm ? "待确认" : "已确认"}</span>
          </header>
          {editableFields.map((field) => (
            <label key={field.key}>
              <span>{field.label}</span>
              <textarea
                onChange={(event) => {
                  setDirty(true);
                  setContent((current) =>
                    current ? { ...current, [field.key]: event.target.value } : current,
                  );
                }}
                required
                rows={field.key === "source_text" ? 4 : 3}
                value={content[field.key]}
              />
            </label>
          ))}
          <label>
            <span>硬性约束（每行一项）</span>
            <textarea
              onChange={(event) => {
                setDirty(true);
                setContent((current) =>
                  current
                    ? { ...current, constraints: event.target.value.split("\n").filter(Boolean) }
                    : current,
                );
              }}
              rows={3}
              value={content.constraints.join("\n")}
            />
          </label>
          <div className={styles.sheetActions}>
            <button className={styles.secondaryButton} disabled={!dirty || saveMutation.isPending} type="submit">
              {saveMutation.isPending ? "保存中…" : "保存 Brief"}
            </button>
            <button
              className={styles.primaryButton}
              disabled={dirty || confirmMutation.isPending || !needsConfirm}
              onClick={() => confirmMutation.mutate()}
              type="button"
            >
              {confirmMutation.isPending ? "确认中…" : needsConfirm ? "确认并冻结版本" : "版本已确认"}
            </button>
          </div>
        </form>

        <aside className={styles.generationPanel}>
          <header>
            <small>AGENT GENERATION</small>
            <h2>生成控制台</h2>
          </header>
          <dl className={styles.generationFacts}>
            <div><dt>运行方式</dt><dd>单 Agent · Agents SDK 工具循环</dd></div>
            <div><dt>任务队列</dt><dd>PostgreSQL Worker / Lease</dd></div>
            <div><dt>模型</dt><dd>{providerQuery.data?.model_id ?? "尚未配置"}</dd></div>
            <div><dt>可见过程</dt><dd>阶段、工具摘要、Validator、用量</dd></div>
          </dl>
          {!providerQuery.data ? (
            <p className={styles.panelWarning}>请先从左下角三个点打开“设置 → 模型与 API”，保存用户级 API Key。</p>
          ) : null}
          <button
            className={styles.generateButton}
            disabled={
              dirty ||
              needsConfirm ||
              !providerQuery.data ||
              generateMutation.isPending ||
              Boolean(running) ||
              draftQuery.data?.content !== null
            }
            onClick={() => generateMutation.mutate()}
            type="button"
          >
            {generateMutation.isPending ? "正在入队…" : running ? "Agent 正在运行" : "开始生成 CaseFile"}
          </button>
          {draftQuery.data?.content ? (
            <p className={styles.panelWarning}>当前 Draft 已有内容，全量生成被锁定；请进入工作台继续编辑。</p>
          ) : null}
          {displayedError ? <p className={styles.formError}>{errorMessage(displayedError)}</p> : null}

          <section className={styles.auditTrail} aria-label="生成审计轨迹">
            <header>
              <span>审计轨迹</span>
              <small>{activeTask ? `${activeTask.status} / ${activeTask.stage}` : "等待 TaskRun"}</small>
            </header>
            {events.length ? (
              <ol>
                {events.map((event) => (
                  <li key={event.sequence_no}>
                    <span>{String(event.sequence_no).padStart(2, "0")}</span>
                    <div>
                      <b>{event.event_type}</b>
                      <small>{event.stage}</small>
                      <p>{eventSummary(event)}</p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className={styles.emptyTrail}>启动后，这里会通过可回放 SSE 显示安全的运行摘要。</p>
            )}
            {streamError ? <p className={styles.formError}>{streamError}</p> : null}
          </section>
        </aside>
      </div>

      {completionVisible ? (
        <div className={styles.modalBackdrop} role="presentation">
          <section aria-modal="true" className={styles.completionDialog} role="dialog">
            <small>TASK SUCCEEDED</small>
            <h2>CaseFile 已生成并写入工作台</h2>
            <p>结构化候选、数据库投影与 Snapshot Hash 已完成一致性校验。</p>
            <dl>
              <div><dt>TaskRun</dt><dd>#{workflow.taskRunId}</dd></div>
              <div><dt>模型</dt><dd>{activeTask?.model_id}</dd></div>
              <div><dt>Attempt</dt><dd>{activeTask?.attempt_count}</dd></div>
            </dl>
            <button
              className={styles.primaryButton}
              onClick={() => {
                setCompletionOpen(false);
                router.push("/workbench");
              }}
              type="button"
            >
              确定并进入工作台
            </button>
          </section>
        </div>
      ) : null}
    </main>
  );
}
