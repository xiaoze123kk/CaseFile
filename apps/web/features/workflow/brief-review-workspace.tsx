"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  CaseSpine,
  DocumentHeader,
  PanelHeader,
  StatusBadge,
} from "@/components/archive-ui";
import {
  apiRequest,
  errorMessage,
  type BriefAnchor,
  type BriefAnchorExtractResult,
  type BriefContent,
  type BriefVersionView,
  type BriefView,
  type ConstraintStrength,
  type CreativeConstraint,
  type DraftView,
  type ProviderSettingView,
  type ResolutionMode,
  type SourceRecordView,
  type TaskEventView,
  type TaskFailure,
  type TaskView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./brief-workspace.module.css";
import {
  useRecoverableTask,
  useTaskEventStream,
} from "./task-recovery";

const terminalTaskStatuses = new Set(["succeeded", "failed", "cancelled"]);

const taskStatusLabels: Record<TaskView["status"], string> = {
  queued: "排队中",
  running: "运行中",
  cancelling: "取消中",
  succeeded: "已完成",
  failed: "已失败",
  cancelled: "已取消",
};

const taskStageLabels: Record<string, string> = {
  queued: "等待执行",
  preparing: "准备输入",
  polishing: "润色原稿",
  extracting: "拆解约束",
  planning: "规划对象",
  generating: "生成草稿",
  repairing: "修复结构",
  validating: "校验结构",
  completed: "任务完成",
  failed: "任务失败",
};

const taskEventLabels: Record<string, string> = {
  "task.recovered": "任务已恢复",
  "task.started": "任务已启动",
  "model.started": "模型开始处理",
  "model.completed": "模型处理完成",
  "model.repair_started": "模型开始修复",
  "tool.started": "工具开始执行",
  "tool.completed": "工具执行完成",
  "validation.started": "开始结构校验",
  "validation.failed": "结构校验未通过",
  "validation.completed": "结构校验完成",
  "task.succeeded": "任务执行成功",
  "task.failed": "任务执行失败",
};

function taskStatusLabel(status: TaskView["status"] | undefined) {
  return status ? taskStatusLabels[status] : "尚未创建";
}

function taskStageLabel(stage: string) {
  return taskStageLabels[stage] ?? "处理中";
}

function taskEventLabel(eventType: string) {
  return taskEventLabels[eventType] ?? "任务状态更新";
}

function taskToolLabel(tool: unknown) {
  const toolName = String(tool);
  const labels: Record<string, string> = {
    plan_object_ids: "规划对象编号",
    validate_casefile_candidate: "校验 CaseFile 候选稿",
  };
  return labels[toolName] ?? "内部工具";
}

function validationIssueMessage(message: string) {
  if (/[\u3400-\u9fff]/u.test(message)) return message;
  if (/candidate must be a JSON object/iu.test(message)) {
    return "候选内容必须是 JSON 对象。";
  }
  if (/invalid JSON/iu.test(message)) {
    return "候选内容不是有效的 JSON。";
  }
  if (/field required/iu.test(message)) return "缺少必填字段。";
  if (/extra inputs are not permitted/iu.test(message)) {
    return "包含契约不允许的字段。";
  }
  return "字段内容不符合 CaseFile 契约要求。";
}

const resolutionModes: Array<{
  value: ResolutionMode;
  label: string;
}> = [
  { value: "author_anchored", label: "按作者底牌展开" },
  { value: "agent_proposed", label: "由 Agent 提出候选结论" },
  { value: "open", label: "保持未决" },
];

type AtomicOrigin = "saved" | "agent" | "manual";

interface AnchorReviewRow extends BriefAnchor {
  origin: AtomicOrigin;
}

interface ConstraintReviewRow extends CreativeConstraint {
  origin: AtomicOrigin;
}

let manualAtomicSequence = 0;

function isBriefContent(
  content: BriefView["content"] | undefined,
): content is BriefContent {
  return Boolean(content && "creative_intent" in content);
}

function isExtractResult(
  task: TaskView | null | undefined,
): task is TaskView & { result: BriefAnchorExtractResult } {
  return Boolean(
    task?.task_type === "brief_anchor_extract" &&
      task.status === "succeeded" &&
      task.result &&
      "author_anchors" in task.result,
  );
}

export function normalizeBriefReviewContent(
  content: BriefContent,
  anchors: BriefAnchor[],
  constraints: CreativeConstraint[],
): BriefContent {
  const authorAnswer =
    content.resolution_mode === "author_anchored"
      ? content.author_answer?.trim() || null
      : null;
  const boundaryText = content.boundary_text?.trim() || null;
  return {
    ...content,
    creative_intent: content.creative_intent.trim(),
    reasoning_proposition: content.reasoning_proposition.trim(),
    author_answer: authorAnswer,
    author_anchors: authorAnswer
      ? anchors
          .map(({ anchor_id, statement }) => ({
            anchor_id,
            statement: statement.trim(),
          }))
          .filter((anchor) => Boolean(anchor.statement))
      : [],
    boundary_text: boundaryText,
    creative_constraints: boundaryText
      ? constraints
          .map(({ constraint_id, statement, strength }) => ({
            constraint_id,
            statement: statement.trim(),
            strength,
          }))
          .filter((constraint) => Boolean(constraint.statement))
      : [],
  };
}

export function extractionMatchesBrief(
  task: TaskView,
  result: BriefAnchorExtractResult,
  briefRevision: number,
  contentDirty: boolean,
) {
  return Boolean(
    !contentDirty &&
      task.input_brief_revision === briefRevision &&
      task.input_hash === result.input_hash,
  );
}

function eventIssueSummary(payload: Record<string, unknown>) {
  const issues = Array.isArray(payload.issues) ? payload.issues : [];
  const first = issues[0];
  if (!first || typeof first !== "object") return null;
  const issue = first as Record<string, unknown>;
  const path = typeof issue.path === "string" && issue.path ? issue.path : "/";
  const message =
    typeof issue.message === "string"
      ? validationIssueMessage(issue.message)
      : "结构校验失败";
  return `${path} ${message}${issues.length > 1 ? `（另有 ${issues.length - 1} 项）` : ""}`;
}

export function eventSummary(event: TaskEventView) {
  const payload = event.payload;
  const failure =
    payload.failure && typeof payload.failure === "object"
      ? (payload.failure as Record<string, unknown>)
      : null;
  const parts = [
    payload.message,
    payload.tool ? `工具：${taskToolLabel(payload.tool)}` : null,
    payload.model_id ? `模型：${String(payload.model_id)}` : null,
    payload.repair_no !== undefined
      ? `修复轮次：${String(payload.repair_no)}`
      : null,
    payload.object_count !== undefined
      ? `对象：${String(payload.object_count)}`
      : null,
    payload.valid !== undefined
      ? payload.valid
        ? "结构有效"
        : "结构无效"
      : null,
    payload.issue_count !== undefined
      ? `问题：${String(payload.issue_count)}`
      : null,
    eventIssueSummary(payload),
    failure?.retryable === true ? "可重试" : null,
    payload.content_hash
      ? `哈希 ${String(payload.content_hash).slice(0, 12)}…`
      : null,
    payload.usage ? `用量：${JSON.stringify(payload.usage)}` : null,
  ];
  return parts.filter(Boolean).join(" · ") || "阶段状态已更新";
}

function TaskFailureDetails({ failure }: { failure: TaskFailure }) {
  return (
    <div className={styles.failurePanel} role="alert">
      <div>
        <b>生成失败</b>
        <span>{failure.retryable ? "可重新发起" : "需要先处理配置"}</span>
      </div>
      <p>{failure.message}</p>
      {failure.issues.length ? (
        <ol>
          {failure.issues.map((issue, index) => (
            <li key={`${issue.code}-${issue.path}-${index}`}>
              <code>{issue.path || "/"}</code>
              <span>{validationIssueMessage(issue.message)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function taskAnchorId(taskRunId: number, index: number) {
  return `anchor_task_${taskRunId}_${String(index + 1).padStart(2, "0")}`;
}

function taskConstraintId(taskRunId: number, index: number) {
  return `constraint_task_${taskRunId}_${String(index + 1).padStart(2, "0")}`;
}

function manualAtomicId(kind: "anchor" | "constraint") {
  manualAtomicSequence += 1;
  return `${kind}_manual_${Date.now()}_${manualAtomicSequence}`;
}

function savedAnchorRows(content: BriefContent): AnchorReviewRow[] {
  return content.author_anchors.map((anchor) => ({
    ...anchor,
    origin: "saved",
  }));
}

function savedConstraintRows(
  content: BriefContent,
): ConstraintReviewRow[] {
  return content.creative_constraints.map((constraint) => ({
    ...constraint,
    origin: "saved",
  }));
}

export function BriefReviewWorkspace() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [content, setContent] = useState<BriefContent | null>(null);
  const [anchorRows, setAnchorRows] = useState<AnchorReviewRow[]>([]);
  const [constraintRows, setConstraintRows] = useState<
    ConstraintReviewRow[]
  >([]);
  const [contentDirty, setContentDirty] = useState(false);
  const [atomicsDirty, setAtomicsDirty] = useState(false);
  const [dismissedCompletionTaskId, setDismissedCompletionTaskId] =
    useState<number | null>(null);
  const hydratedBriefRevisionRef = useRef<number | null>(null);
  const seededExtractionTaskRef = useRef<number | null>(null);

  const briefQuery = useQuery({
    queryKey: ["brief", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<BriefView>(`/projects/${workflow.projectId}/brief`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });
  const sourceQuery = useQuery({
    queryKey: ["sources", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<SourceRecordView[]>(
        `/projects/${workflow.projectId}/sources`,
        { actorId: workflow.actorId },
      ),
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
    queryKey: ["provider-setting", workflow.actorId, workflow.provider],
    queryFn: () =>
      apiRequest<ProviderSettingView | null>(
        `/settings/provider?provider=${workflow.provider}`,
        { actorId: workflow.actorId },
      ),
    enabled: workflow.ready,
  });

  const extractRecovery = useRecoverableTask(
    workflow.projectId,
    workflow.actorId,
    "brief_anchor_extract",
    workflow.taskRunIds.brief_anchor_extract,
    workflow.ready,
  );
  const generationRecovery = useRecoverableTask(
    workflow.projectId,
    workflow.actorId,
    "brief_to_draft",
    workflow.taskRunIds.brief_to_draft,
    workflow.ready,
  );
  const extractTask = extractRecovery.task;
  const generationTask = generationRecovery.task;
  const visibleTask =
    [extractTask, generationTask]
      .filter((task): task is TaskView => Boolean(task))
      .sort((left, right) => right.task_run_id - left.task_run_id)[0] ?? null;
  const eventStream = useTaskEventStream(
    workflow.projectId,
    workflow.actorId,
    visibleTask?.task_run_id ?? null,
  );

  useEffect(() => {
    if (
      !isBriefContent(briefQuery.data?.content) ||
      hydratedBriefRevisionRef.current === briefQuery.data.draft_revision
    ) {
      return;
    }
    const loaded = briefQuery.data.content;
    setContent({
      ...loaded,
      source_record_ids: [...loaded.source_record_ids],
      author_anchors: [...loaded.author_anchors],
      creative_constraints: [...loaded.creative_constraints],
    });
    setAnchorRows(savedAnchorRows(loaded));
    setConstraintRows(savedConstraintRows(loaded));
    setContentDirty(false);
    setAtomicsDirty(false);
    hydratedBriefRevisionRef.current = briefQuery.data.draft_revision;
  }, [briefQuery.data]);

  useEffect(() => {
    if (
      !content ||
      contentDirty ||
      !briefQuery.data ||
      !isExtractResult(extractTask) ||
      seededExtractionTaskRef.current === extractTask.task_run_id ||
      extractTask.input_brief_revision !== briefQuery.data.draft_revision ||
      extractTask.input_hash !== extractTask.result.input_hash
    ) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      const hasExtractionInput = Boolean(
        content.author_answer || content.boundary_text,
      );
      setAnchorRows(
        content.author_answer
          ? extractTask.result.author_anchors.map((anchor, index) => ({
              anchor_id: taskAnchorId(extractTask.task_run_id, index),
              statement: anchor.statement,
              origin: "agent",
            }))
          : [],
      );
      setConstraintRows(
        content.boundary_text
          ? extractTask.result.creative_constraints.map(
              (constraint, index) => ({
                constraint_id: taskConstraintId(
                  extractTask.task_run_id,
                  index,
                ),
                statement: constraint.statement,
                strength: constraint.suggested_strength,
                origin: "agent",
              }),
            )
          : [],
      );
      if (hasExtractionInput) setAtomicsDirty(true);
      seededExtractionTaskRef.current = extractTask.task_run_id;
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [
    briefQuery.data,
    content,
    contentDirty,
    extractTask,
  ]);

  useEffect(() => {
    if (
      extractTask &&
      workflow.taskRunIds.brief_anchor_extract !==
        extractTask.task_run_id
    ) {
      workflow.setTask(
        "brief_anchor_extract",
        extractTask.task_run_id,
      );
    }
  }, [extractTask, workflow]);

  useEffect(() => {
    if (
      generationTask &&
      workflow.taskRunIds.brief_to_draft !==
        generationTask.task_run_id
    ) {
      workflow.setTask("brief_to_draft", generationTask.task_run_id);
    }
  }, [generationTask, workflow]);

  useEffect(() => {
    if (generationTask?.status !== "succeeded") return;
    void queryClient.invalidateQueries({
      queryKey: ["draft", workflow.actorId, workflow.projectId],
    });
  }, [
    generationTask?.status,
    queryClient,
    workflow.actorId,
    workflow.projectId,
  ]);

  const extractionMutation = useMutation({
    mutationFn: (briefRevision: number) => {
      if (workflow.projectId === null) {
        throw new Error("当前没有可拆解的真实项目。");
      }
      if (!providerQuery.data) {
        throw new Error("请先配置当前 Agent 模型服务。");
      }
      return apiRequest<TaskView>(
        `/projects/${workflow.projectId}/tasks/brief-anchor-extract`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            expected_brief_revision: briefRevision,
            provider: workflow.provider,
          },
        },
      );
    },
    onSuccess: (task) => {
      seededExtractionTaskRef.current = null;
      workflow.setTask("brief_anchor_extract", task.task_run_id);
      queryClient.setQueryData(
        [
          "latest-task",
          workflow.actorId,
          workflow.projectId,
          "brief_anchor_extract",
        ],
        task,
      );
      queryClient.setQueryData(
        [
          "task",
          workflow.actorId,
          workflow.projectId,
          task.task_run_id,
        ],
        task,
      );
    },
  });

  const normalizedContent = useMemo<BriefContent | null>(() => {
    if (!content) return null;
    return normalizeBriefReviewContent(
      content,
      anchorRows,
      constraintRows,
    );
  }, [anchorRows, constraintRows, content]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (
        workflow.projectId === null ||
        !briefQuery.data ||
        !normalizedContent
      ) {
        throw new Error("创作简报尚未读取完成。");
      }
      if (!normalizedContent.creative_intent) {
        throw new Error("创作意图不能为空。");
      }
      if (!normalizedContent.reasoning_proposition) {
        throw new Error("核心推理命题不能为空。");
      }
      if (
        normalizedContent.resolution_mode === "author_anchored" &&
        !normalizedContent.author_answer
      ) {
        throw new Error("按作者底牌展开时必须保留作者底牌原文。");
      }
      const saved = await apiRequest<BriefView>(
        `/projects/${workflow.projectId}/brief`,
        {
          actorId: workflow.actorId,
          method: "PUT",
          body: {
            expected_revision: briefQuery.data.draft_revision,
            content: normalizedContent,
          },
        },
      );
      const shouldExtract =
        (Boolean(normalizedContent.author_answer) &&
          normalizedContent.author_anchors.length === 0) ||
        (Boolean(normalizedContent.boundary_text) &&
          normalizedContent.creative_constraints.length === 0);
      return { saved, shouldExtract };
    },
    onSuccess: ({ saved, shouldExtract }) => {
      queryClient.setQueryData(
        ["brief", workflow.actorId, workflow.projectId],
        saved,
      );
      if (isBriefContent(saved.content)) {
        setContent(saved.content);
        setAnchorRows(savedAnchorRows(saved.content));
        setConstraintRows(savedConstraintRows(saved.content));
      }
      setContentDirty(false);
      setAtomicsDirty(false);
      hydratedBriefRevisionRef.current = saved.draft_revision;
      if (shouldExtract) extractionMutation.mutate(saved.draft_revision);
    },
  });

  const confirmMutation = useMutation({
    mutationFn: () => {
      if (
        workflow.projectId === null ||
        !briefQuery.data ||
        !normalizedContent
      ) {
        throw new Error("创作简报尚未读取完成。");
      }
      if (contentDirty || atomicsDirty) {
        throw new Error("请先保存当前审阅修改。");
      }
      if (
        normalizedContent.author_answer &&
        normalizedContent.author_anchors.length === 0
      ) {
        throw new Error("作者底牌必须至少确认一条原子硬约束。");
      }
      if (
        normalizedContent.boundary_text &&
        normalizedContent.creative_constraints.length === 0
      ) {
        throw new Error("创作边界必须至少确认一条原子约束。");
      }
      return apiRequest<BriefVersionView>(
        `/projects/${workflow.projectId}/brief/confirm`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            expected_revision: briefQuery.data.draft_revision,
          },
        },
      );
    },
    onSuccess: async (version) => {
      queryClient.setQueryData(
        ["confirmed-brief", workflow.actorId, workflow.projectId],
        version,
      );
      await queryClient.invalidateQueries({
        queryKey: ["brief", workflow.actorId, workflow.projectId],
      });
    },
  });

  const generationMutation = useMutation({
    mutationFn: () => {
      if (
        workflow.projectId === null ||
        !briefQuery.data?.current_version_id ||
        !draftQuery.data
      ) {
        throw new Error("请先冻结当前创作简报，并等待草稿状态读取完成。");
      }
      if (!providerQuery.data) {
        throw new Error("请先配置当前 Agent 模型服务。");
      }
      if (draftQuery.data.content) {
        throw new Error("当前草稿已有内容，不能再次执行全量生成。");
      }
      return apiRequest<TaskView>(
        `/projects/${workflow.projectId}/tasks/generate`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            brief_version_id: briefQuery.data.current_version_id,
            expected_draft_revision: draftQuery.data.revision,
            provider: workflow.provider,
          },
        },
      );
    },
    onSuccess: (task) => {
      setDismissedCompletionTaskId(null);
      workflow.setTask("brief_to_draft", task.task_run_id);
      queryClient.setQueryData(
        [
          "latest-task",
          workflow.actorId,
          workflow.projectId,
          "brief_to_draft",
        ],
        task,
      );
      queryClient.setQueryData(
        [
          "task",
          workflow.actorId,
          workflow.projectId,
          task.task_run_id,
        ],
        task,
      );
    },
  });

  const dirty = contentDirty || atomicsDirty;
  const extractionRunning = Boolean(
    extractTask && !terminalTaskStatuses.has(extractTask.status),
  );
  const generationRunning = Boolean(
    generationTask &&
      !terminalTaskStatuses.has(generationTask.status),
  );
  const extractionResult = isExtractResult(extractTask)
    ? extractTask.result
    : null;
  const extractionCurrent = Boolean(
    extractionResult &&
      briefQuery.data &&
      extractTask &&
      extractionMatchesBrief(
        extractTask,
        extractionResult,
        briefQuery.data.draft_revision,
        contentDirty,
      ),
  );
  const atomicReviewComplete = Boolean(
    normalizedContent &&
      (!normalizedContent.author_answer ||
        normalizedContent.author_anchors.length > 0) &&
      (!normalizedContent.boundary_text ||
        normalizedContent.creative_constraints.length > 0),
  );
  const needsConfirm = briefQuery.data?.current_version_id === null;
  const frozen = Boolean(
    briefQuery.data?.current_version_id && !dirty,
  );
  const duplicateGenerationBlocked = Boolean(
    generationTask &&
      draftQuery.data &&
      generationTask.input_draft_revision === draftQuery.data.revision &&
      !["failed", "cancelled"].includes(generationTask.status),
  );
  const displayedError =
    saveMutation.error ??
    extractionMutation.error ??
    confirmMutation.error ??
    generationMutation.error ??
    extractRecovery.error ??
    generationRecovery.error ??
    providerQuery.error;
  const completionVisible = Boolean(
    generationTask?.status === "succeeded" &&
      dismissedCompletionTaskId !== generationTask.task_run_id,
  );
  const sourceRecords = sourceQuery.data ?? [];
  const originalSources = sourceRecords.filter(
    (source) => source.source_kind === "human_original",
  );
  const adoptedSources = sourceRecords.filter(
    (source) =>
      normalizedContent?.source_record_ids.includes(
        source.source_record_id,
      ) && source.source_kind !== "human_original",
  );

  function markRawInputChanged() {
    seededExtractionTaskRef.current = null;
    setAnchorRows([]);
    setConstraintRows([]);
    setAtomicsDirty(true);
  }

  function addAnchor() {
    setAnchorRows((current) => [
      ...current,
      {
        anchor_id: manualAtomicId("anchor"),
        statement: "",
        origin: "manual",
      },
    ]);
    setAtomicsDirty(true);
  }

  function addConstraint() {
    setConstraintRows((current) => [
      ...current,
      {
        constraint_id: manualAtomicId("constraint"),
        statement: "",
        strength: "hard",
        origin: "manual",
      },
    ]);
    setAtomicsDirty(true);
  }

  if (
    !workflow.ready ||
    briefQuery.isLoading ||
    sourceQuery.isLoading ||
    draftQuery.isLoading
  ) {
    return (
      <main className={styles.centerState}>
        <p>正在恢复创作简报、来源记录与任务…</p>
      </main>
    );
  }

  if (workflow.projectId === null) {
    return (
      <main className={styles.centerState}>
        <p>尚未创建真实项目。</p>
        <button
          className={styles.primaryButton}
          onClick={() => router.push("/")}
          type="button"
        >
          返回建案中心
        </button>
      </main>
    );
  }

  if (briefQuery.isError || sourceQuery.isError || draftQuery.isError) {
    return (
      <main className={styles.centerState}>
        <p role="alert">
          {errorMessage(
            briefQuery.error ?? sourceQuery.error ?? draftQuery.error,
          )}
        </p>
        <button
          className={styles.primaryButton}
          onClick={() => {
            void briefQuery.refetch();
            void sourceQuery.refetch();
            void draftQuery.refetch();
          }}
          type="button"
        >
          重新读取
        </button>
      </main>
    );
  }

  if (!content || !normalizedContent) {
    return (
      <main className={styles.centerState}>
        <p>创作简报尚无可审阅内容，请返回建案中心保存。</p>
        <button
          className={styles.primaryButton}
          onClick={() => router.push("/")}
          type="button"
        >
          返回建案中心
        </button>
      </main>
    );
  }

  return (
    <main className={`document ${styles.document}`}>
      <DocumentHeader
        action={
          <button
            className={styles.backButton}
            onClick={() => router.push("/")}
            type="button"
          >
            ← 返回建案
          </button>
        }
        eyebrow="创作简报审阅 · 作者控制"
        meta={[
          {
            label: "项目",
            value: `项目-${workflow.projectId}`,
          },
          {
            label: "简报草稿",
            value: `版本 ${briefQuery.data?.draft_revision ?? "—"}`,
          },
          {
            label: "冻结状态",
            value: frozen ? "已冻结" : dirty ? "有待保存修改" : "待确认",
            tone: dirty ? "critical" : "default",
          },
        ]}
        title={normalizedContent.creative_intent}
      />

      <CaseSpine current="brief" />

      <div className={styles.briefSpread}>
        <form
          className={`paper-panel ${styles.briefSheet}`}
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <span className={styles.briefWatermark}>简报</span>
          <header className={styles.briefSheetHead}>
            <div>
              <span>创作简报核心 · 目标无关</span>
              <strong>{normalizedContent.reasoning_proposition}</strong>
            </div>
            <div
              className={`${styles.candidateStamp} ${
                frozen ? styles.candidateStampApproved : ""
              }`}
            >
              <span>{frozen ? "已冻结" : "候选稿"}</span>
              <b>{frozen ? "人工已确认" : dirty ? "待保存" : "待冻结"}</b>
            </div>
          </header>

          <div className={styles.fieldGrid}>
            <label className={styles.briefField}>
              <span className={styles.briefFieldHead}>
                <i>01</i>
                <span>
                  <strong>创作意图</strong>
                  <small>创作意图说明</small>
                </span>
                <em>作者原文</em>
              </span>
              <textarea
                aria-label="创作意图"
                onChange={(event) => {
                  setContent((current) =>
                    current
                      ? {
                          ...current,
                          creative_intent: event.target.value,
                        }
                      : current,
                  );
                  setContentDirty(true);
                }}
                required
                value={content.creative_intent}
              />
              <span className={styles.fieldFoot}>
                <span>定义作品要建立什么</span>
                <b>必填</b>
              </span>
            </label>

            <label className={styles.briefField}>
              <span className={styles.briefFieldHead}>
                <i>02</i>
                <span>
                  <strong>核心推理命题</strong>
                  <small>推理命题说明</small>
                </span>
                <em>作者原文</em>
              </span>
              <textarea
                aria-label="核心推理命题"
                onChange={(event) => {
                  setContent((current) =>
                    current
                      ? {
                          ...current,
                          reasoning_proposition: event.target.value,
                        }
                      : current,
                  );
                  setContentDirty(true);
                }}
                required
                value={content.reasoning_proposition}
              />
              <span className={styles.fieldFoot}>
                <span>定义要探索或判断什么</span>
                <b>必填</b>
              </span>
            </label>

            <label className={styles.briefField}>
              <span className={styles.briefFieldHead}>
                <i>03</i>
                <span>
                  <strong>结论处理方式</strong>
                  <small>结论处理说明</small>
                </span>
                <em>作者决定</em>
              </span>
              <select
                aria-label="结论处理方式"
                className={styles.modeSelect}
                onChange={(event) => {
                  const mode = event.target.value as ResolutionMode;
                  setContent((current) =>
                    current
                      ? {
                          ...current,
                          resolution_mode: mode,
                          author_answer:
                            mode === "author_anchored"
                              ? current.author_answer
                              : null,
                        }
                      : current,
                  );
                  if (mode !== "author_anchored") {
                    setAnchorRows([]);
                    setAtomicsDirty(true);
                  }
                  setContentDirty(true);
                }}
                value={content.resolution_mode}
              >
                {resolutionModes.map((mode) => (
                  <option key={mode.value} value={mode.value}>
                    {mode.label}
                  </option>
                ))}
              </select>
              <span className={styles.fieldFoot}>
                <span>不预设媒介或最终成品</span>
                <b>必填</b>
              </span>
            </label>

            <label className={styles.briefField}>
              <span className={styles.briefFieldHead}>
                <i>04</i>
                <span>
                  <strong>作者底牌</strong>
                  <small>作者底牌原文</small>
                </span>
                <em>
                  {content.resolution_mode === "author_anchored"
                    ? "硬约束原文"
                    : "当前不适用"}
                </em>
              </span>
              <textarea
                aria-label="作者底牌"
                disabled={
                  content.resolution_mode !== "author_anchored"
                }
                onChange={(event) => {
                  markRawInputChanged();
                  setContent((current) =>
                    current
                      ? {
                          ...current,
                          author_answer: event.target.value,
                          author_anchors: [],
                          creative_constraints: [],
                        }
                      : current,
                  );
                  setContentDirty(true);
                }}
                placeholder="作者底牌原稿不会被 Agent 静默改写。"
                required={
                  content.resolution_mode === "author_anchored"
                }
                value={content.author_answer ?? ""}
              />
              <span className={styles.fieldFoot}>
                <span>修改后原子拆解自动失效</span>
                <b>
                  {content.resolution_mode === "author_anchored"
                    ? "作者锁定"
                    : "不适用"}
                </b>
              </span>
            </label>

            <label
              className={`${styles.briefField} ${styles.sourceField}`}
            >
              <span className={styles.briefFieldHead}>
                <i>05</i>
                <span>
                  <strong>创作边界</strong>
                  <small>创作边界原文</small>
                </span>
                <em>允许硬约束或软偏好</em>
              </span>
              <textarea
                aria-label="创作边界"
                onChange={(event) => {
                  markRawInputChanged();
                  setContent((current) =>
                    current
                      ? {
                          ...current,
                          boundary_text: event.target.value,
                          author_anchors: [],
                          creative_constraints: [],
                        }
                      : current,
                  );
                  setContentDirty(true);
                }}
                placeholder="保留作者的边界原文；下方逐条确认拆解结果。"
                value={content.boundary_text ?? ""}
              />
              <span className={styles.fieldFoot}>
                <span>原文与原子约束同时保留</span>
                <b>选填</b>
              </span>
            </label>

            <section
              className={`${styles.briefField} ${styles.atomicField}`}
              aria-label="原子底牌审阅"
            >
              <header className={styles.atomicHead}>
                <span>
                  <strong>原子底牌</strong>
                  <small>作者锚点 / 固定为硬约束</small>
                </span>
                <button onClick={addAnchor} type="button">
                  ＋ 人工新增
                </button>
              </header>
              <div className={styles.atomicList}>
                {anchorRows.length ? (
                  anchorRows.map((anchor) => (
                    <div
                      className={styles.atomicRow}
                      key={anchor.anchor_id}
                    >
                      <span
                        className={styles.atomicOrigin}
                        data-origin={anchor.origin}
                      >
                        {anchor.origin === "agent"
                          ? "Agent 拆解"
                          : anchor.origin === "manual"
                            ? "人工新增"
                            : "已保存"}
                      </span>
                      <input
                        aria-label={`底牌原子项 ${anchor.anchor_id}`}
                        onChange={(event) => {
                          setAnchorRows((current) =>
                            current.map((item) =>
                              item.anchor_id === anchor.anchor_id
                                ? {
                                    ...item,
                                    statement: event.target.value,
                                  }
                                : item,
                            ),
                          );
                          setAtomicsDirty(true);
                        }}
                        value={anchor.statement}
                      />
                      <b>硬约束</b>
                      <button
                        aria-label={`删除底牌原子项 ${anchor.anchor_id}`}
                        onClick={() => {
                          setAnchorRows((current) =>
                            current.filter(
                              (item) =>
                                item.anchor_id !== anchor.anchor_id,
                            ),
                          );
                          setAtomicsDirty(true);
                        }}
                        type="button"
                      >
                        ×
                      </button>
                    </div>
                  ))
                ) : (
                  <p className={styles.atomicEmpty}>
                    {content.author_answer
                      ? "等待 Agent 拆解，或由作者手工新增。"
                      : "当前没有需要原子化的作者底牌。"}
                  </p>
                )}
              </div>
              <span className={styles.fieldFoot}>
                <span>只有保存后的项目才进入简报硬约束</span>
                <b>{anchorRows.length} 项</b>
              </span>
            </section>

            <section
              className={`${styles.briefField} ${styles.atomicField} ${styles.constraintsField}`}
              aria-label="原子创作约束审阅"
            >
              <header className={styles.atomicHead}>
                <span>
                  <strong>原子创作约束</strong>
                  <small>逐条确认的创作边界</small>
                </span>
                <button onClick={addConstraint} type="button">
                  ＋ 人工新增
                </button>
              </header>
              <div className={styles.atomicList}>
                {constraintRows.length ? (
                  constraintRows.map((constraint) => (
                    <div
                      className={styles.atomicRow}
                      key={constraint.constraint_id}
                    >
                      <span
                        className={styles.atomicOrigin}
                        data-origin={constraint.origin}
                      >
                        {constraint.origin === "agent"
                          ? "Agent 拆解"
                          : constraint.origin === "manual"
                            ? "人工新增"
                            : "已保存"}
                      </span>
                      <input
                        aria-label={`创作约束 ${constraint.constraint_id}`}
                        onChange={(event) => {
                          setConstraintRows((current) =>
                            current.map((item) =>
                              item.constraint_id ===
                              constraint.constraint_id
                                ? {
                                    ...item,
                                    statement: event.target.value,
                                  }
                                : item,
                            ),
                          );
                          setAtomicsDirty(true);
                        }}
                        value={constraint.statement}
                      />
                      <select
                        aria-label={`约束强度 ${constraint.constraint_id}`}
                        onChange={(event) => {
                          setConstraintRows((current) =>
                            current.map((item) =>
                              item.constraint_id ===
                              constraint.constraint_id
                                ? {
                                    ...item,
                                    strength: event.target
                                      .value as ConstraintStrength,
                                  }
                                : item,
                            ),
                          );
                          setAtomicsDirty(true);
                        }}
                        value={constraint.strength}
                      >
                        <option value="hard">硬约束</option>
                        <option value="soft">软偏好</option>
                      </select>
                      <button
                        aria-label={`删除创作约束 ${constraint.constraint_id}`}
                        onClick={() => {
                          setConstraintRows((current) =>
                            current.filter(
                              (item) =>
                                item.constraint_id !==
                                constraint.constraint_id,
                            ),
                          );
                          setAtomicsDirty(true);
                        }}
                        type="button"
                      >
                        ×
                      </button>
                    </div>
                  ))
                ) : (
                  <p className={styles.atomicEmpty}>
                    {content.boundary_text
                      ? "等待 Agent 拆解，或由作者手工新增。"
                      : "当前没有需要原子化的创作边界。"}
                  </p>
                )}
              </div>
              <span className={styles.fieldFoot}>
                <span>强度由作者逐条决定</span>
                <b>{constraintRows.length} 项</b>
              </span>
            </section>
          </div>

          <section className={styles.sourceLedger}>
            <PanelHeader
              code="来源记录 · 不可变"
              title="来源台账"
              trailing={
                <StatusBadge tone="neutral">
                  {sourceRecords.length} 条记录
                </StatusBadge>
              }
            />
            <div>
              <span>
                <b>{originalSources.length}</b>
                <small>人工原稿完整保留</small>
              </span>
              <span>
                <b>{adoptedSources.length}</b>
                <small>已纳入创作简报的候选或人工修订</small>
              </span>
              <span>
                <b>
                  {extractTask
                    ? `任务 #${extractTask.task_run_id}`
                    : "尚无任务"}
                </b>
                <small>
                  {extractTask
                    ? taskStatusLabel(extractTask.status)
                    : "尚未创建原子拆解任务"}
                </small>
              </span>
            </div>
          </section>

          <footer className={styles.sheetActions}>
            <div>
              <span>
                {dirty
                  ? "当前修改尚未写入 PostgreSQL"
                  : frozen
                    ? "当前创作简报版本已由作者冻结"
                    : atomicReviewComplete
                      ? "原子约束已保存，可冻结版本"
                      : "等待拆解与人工审阅"}
              </span>
              <b>
                原稿不覆盖 · Agent 只提交候选 · 作者决定硬约束
              </b>
            </div>
            <button
              className={styles.secondaryButton}
              disabled={!dirty || saveMutation.isPending}
              type="submit"
            >
              {saveMutation.isPending ? "保存中…" : "保存审阅"}
            </button>
            <button
              className={styles.primaryButton}
              disabled={
                dirty ||
                !atomicReviewComplete ||
                confirmMutation.isPending ||
                !needsConfirm ||
                extractionRunning
              }
              onClick={() => confirmMutation.mutate()}
              type="button"
            >
              {confirmMutation.isPending
                ? "冻结中…"
                : frozen
                  ? "版本已冻结"
                  : "确认并冻结"}
            </button>
          </footer>
        </form>

        <aside className={`paper-panel ${styles.generationPanel}`}>
          <header className={styles.generationHead}>
            <div>
              <span>创作简报 → 核心草稿</span>
              <h2>生成与审计控制台</h2>
            </div>
            <StatusBadge
              tone={
                generationRunning
                  ? "red"
                  : frozen
                    ? "dark"
                    : "neutral"
              }
            >
              {generationRunning
                ? "Agent 运行中"
                : frozen
                  ? "可以生成"
                  : "尚未解锁"}
            </StatusBadge>
          </header>

          <section className={styles.gateSection}>
            <PanelHeader
              code="3 道作者门禁"
              title="生成门禁"
            />
            <ol className={styles.gateList}>
              <li
                className={
                  atomicReviewComplete
                    ? styles.gateComplete
                    : styles.gateCurrent
                }
              >
                <b>{atomicReviewComplete ? "✓" : "1"}</b>
                <span>
                  <strong>原子拆解</strong>
                  <small>
                    {atomicReviewComplete ? "已人工确认" : "等待审阅"}
                  </small>
                </span>
              </li>
              <li
                className={
                  frozen
                    ? styles.gateComplete
                    : atomicReviewComplete
                      ? styles.gateCurrent
                      : undefined
                }
              >
                <b>{frozen ? "✓" : "2"}</b>
                <span>
                  <strong>冻结创作简报</strong>
                  <small>{frozen ? "版本不可变" : "等待作者确认"}</small>
                </span>
              </li>
              <li
                className={
                  generationRunning
                    ? styles.gateCurrent
                    : generationTask?.status === "succeeded"
                      ? styles.gateComplete
                      : undefined
                }
              >
                <b>
                  {generationTask?.status === "succeeded" ? "✓" : "3"}
                </b>
                <span>
                  <strong>生成草稿</strong>
                  <small>
                    {generationTask
                      ? taskStatusLabel(generationTask.status)
                      : "尚未启动"}
                  </small>
                </span>
              </li>
            </ol>
          </section>

          <section className={styles.runtimeSection}>
            <PanelHeader
              code="任务运行 / PostgreSQL"
              title="运行状态"
              trailing={
                <StatusBadge tone={providerQuery.data ? "dark" : "red"}>
                  {providerQuery.data
                    ? workflow.provider === "deepseek"
                      ? "DeepSeek"
                      : "OpenAI"
                    : "未配置模型"}
                </StatusBadge>
              }
            />
            <dl className={styles.generationFacts}>
              <div>
                <dt>当前模型</dt>
                <dd>{providerQuery.data?.model_id ?? "尚未配置"}</dd>
              </div>
              <div>
                <dt>创作简报版本</dt>
                <dd>
                  {briefQuery.data?.current_version_id
                    ? `#${briefQuery.data.current_version_id}`
                    : "未冻结"}
                </dd>
              </div>
              <div>
                <dt>拆解任务</dt>
                <dd>
                  {extractTask
                    ? `#${extractTask.task_run_id} / ${taskStatusLabel(extractTask.status)}`
                    : "尚无任务"}
                </dd>
              </div>
              <div>
                <dt>生成任务</dt>
                <dd>
                  {generationTask
                    ? `#${generationTask.task_run_id} / ${taskStatusLabel(generationTask.status)}`
                    : "尚无任务"}
                </dd>
              </div>
            </dl>

            {extractionCurrent && extractionResult?.warnings.length ? (
              <div className={styles.warningList}>
                <b>Agent 提醒</b>
                <ul>
                  {extractionResult.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {isExtractResult(extractTask) && !extractionCurrent && !atomicReviewComplete ? (
              <p className={styles.panelWarning}>
                当前拆解候选对应旧创作简报版本或输入哈希，已禁止进入硬约束。
              </p>
            ) : null}
            {extractTask?.status === "failed" ? (
              <p className={styles.panelWarning}>
                原子拆解失败：
                {extractTask.failure?.message ??
                  "任务未能完成，请稍后重试"}
                。原稿仍完整保留，可重试或人工补录。
              </p>
            ) : null}
            {generationTask?.status === "failed" &&
            generationTask.failure ? (
              <TaskFailureDetails failure={generationTask.failure} />
            ) : null}
            {!providerQuery.data ? (
              <p className={styles.panelWarning}>
                请从左下角设置中配置当前模型服务，才能运行 Agent。
              </p>
            ) : null}
            {displayedError ? (
              <p className={styles.formError} role="alert">
                {errorMessage(displayedError)}
              </p>
            ) : null}

            <div className={styles.taskActions}>
              <button
                disabled={
                  dirty ||
                  extractionMutation.isPending ||
                  extractionRunning ||
                  (!normalizedContent.author_answer &&
                    !normalizedContent.boundary_text) ||
                  !providerQuery.data
                }
                onClick={() => {
                  if (briefQuery.data) {
                    extractionMutation.mutate(
                      briefQuery.data.draft_revision,
                    );
                  }
                }}
                type="button"
              >
                {dirty
                  ? "先保存再拆解"
                  : extractionRunning
                    ? "拆解运行中…"
                    : "重新拆解底牌与边界"}
              </button>
              <button
                className={styles.generateButton}
                disabled={
                  dirty ||
                  !atomicReviewComplete ||
                  !frozen ||
                  !providerQuery.data ||
                  generationMutation.isPending ||
                  generationRunning ||
                  duplicateGenerationBlocked ||
                  Boolean(draftQuery.data?.content)
                }
                onClick={() => generationMutation.mutate()}
                type="button"
              >
                <span>
                  {generationMutation.isPending
                    ? "正在入队…"
                    : generationRunning
                      ? "Agent 正在生成"
                      : draftQuery.data?.content
                        ? "草稿已存在"
                        : "启动创作简报 → 草稿"}
                </span>
                <b>生成核心草稿 →</b>
              </button>
            </div>
          </section>

          <section
            className={styles.auditTrail}
            aria-label="任务可恢复审计轨迹"
          >
            <PanelHeader
              code={
                visibleTask
                  ? `任务 #${visibleTask.task_run_id}`
                  : "等待任务"
              }
              title="可恢复审计轨迹"
              trailing={
                visibleTask ? (
                  <StatusBadge tone="neutral">
                    {taskStageLabel(visibleTask.stage)}
                  </StatusBadge>
                ) : undefined
              }
            />
            {eventStream.events.length ? (
              <ol>
                {eventStream.events.map((event) => (
                  <li key={event.sequence_no}>
                    <span>
                      {String(event.sequence_no).padStart(2, "0")}
                    </span>
                    <div>
                      <b>{taskEventLabel(event.event_type)}</b>
                      <small>{taskStageLabel(event.stage)}</small>
                      <p>{eventSummary(event)}</p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className={styles.emptyTrail}>
                任务创建后，这里会从不可变事件表回放阶段、工具摘要、校验结果与用量。
              </p>
            )}
            {eventStream.streamError ? (
              <div className={styles.streamError}>
                <p>{eventStream.streamError}</p>
                <button
                  onClick={eventStream.reconnect}
                  type="button"
                >
                  按最后序号重连
                </button>
              </div>
            ) : null}
          </section>
        </aside>
      </div>

      <footer className={styles.documentFooter}>
        <span>
          <b>真实模式：</b>PostgreSQL / 任务运行 / SSE
        </span>
        <span>
          来源记录：{sourceRecords.length}
        </span>
        <span>
          {dirty
            ? "修改尚未保存，冻结与生成门禁保持关闭。"
            : "候选只有经作者保存确认后才进入创作简报。"}
        </span>
        <span>本阶段不进入编译</span>
      </footer>

      {completionVisible && generationTask ? (
        <div className={styles.modalBackdrop} role="presentation">
          <section
            aria-modal="true"
            className={styles.completionDialog}
            role="dialog"
          >
            <small>任务成功 · 核心草稿已就绪</small>
            <h2>创作简报已生成真实草稿</h2>
            <p>
              任务运行、规范化数据库投影与快照均已完成；本轮不进入下游编译。
            </p>
            <dl>
              <div>
                <dt>任务编号</dt>
                <dd>#{generationTask.task_run_id}</dd>
              </div>
              <div>
                <dt>模型供应商</dt>
                <dd>{generationTask.provider}</dd>
              </div>
              <div>
                <dt>执行次数</dt>
                <dd>{generationTask.attempt_count}</dd>
              </div>
            </dl>
            <button
              onClick={() => {
                setDismissedCompletionTaskId(
                  generationTask.task_run_id,
                );
                router.push("/workbench");
              }}
              type="button"
            >
              进入 CaseFile 工作台 →
            </button>
          </section>
        </div>
      ) : null}
    </main>
  );
}
