"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
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
  type BriefContent,
  type BriefPolishResult,
  type BriefView,
  type ProjectView,
  type ProviderSettingView,
  type ResolutionMode,
  type SourceRecordView,
  type TaskView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./intake-workspace.module.css";
import {
  useRecoverableTask,
  useTaskEventStream,
} from "./task-recovery";

const emptyBrief: BriefContent = {
  source_record_ids: [],
  creative_intent: "",
  reasoning_proposition: "",
  resolution_mode: "agent_proposed",
  author_answer: null,
  author_anchors: [],
  boundary_text: null,
  creative_constraints: [],
};

const resolutionModes: Array<{
  value: ResolutionMode;
  label: string;
  code: string;
  detail: string;
}> = [
  {
    value: "agent_proposed",
    label: "Agent 提出候选结论",
    code: "候选结论",
    detail: "让 Agent 构造可审阅答案，但不直接写成正式版本。",
  },
  {
    value: "author_anchored",
    label: "按作者底牌展开",
    code: "作者底牌",
    detail: "底牌作为硬约束，拆解后仍需逐条人工确认。",
  },
  {
    value: "open",
    label: "保持未决",
    code: "保持未决",
    detail: "建立事实与问题空间，不要求草稿预先收束。",
  },
];

const intakeRoutes = [
  {
    code: "01",
    title: "我有一个想法",
    detail: "从命题建立创作简报",
    enabled: true,
  },
  {
    code: "02",
    title: "帮我想一个",
    detail: "比较多个创意候选",
    enabled: false,
  },
  {
    code: "03",
    title: "整理已有内容",
    detail: "抽取实体、事件与信息",
    enabled: false,
  },
  {
    code: "04",
    title: "专业模板起稿",
    detail: "从结构化空白卷宗开始",
    enabled: false,
  },
] as const;

const currentIntakeRoute = intakeRoutes[0];
const plannedIntakeRoutes = intakeRoutes.slice(1);
const terminalTaskStatuses = new Set(["succeeded", "failed", "cancelled"]);

function isBriefContent(
  content: BriefView["content"] | undefined,
): content is BriefContent {
  return Boolean(content && "creative_intent" in content);
}

function isPolishResult(
  task: TaskView | null | undefined,
): task is TaskView & { result: BriefPolishResult } {
  return Boolean(
    task?.task_type === "brief_polish" &&
      task.status === "succeeded" &&
      task.result &&
      "polished_text" in task.result,
  );
}

function uniqueIds(ids: Array<number | null | undefined>) {
  return [...new Set(ids.filter((id): id is number => typeof id === "number"))];
}

function titleFrom(brief: BriefContent, sourceText: string) {
  return (
    brief.creative_intent.trim() ||
    sourceText.trim().split(/\r?\n/u)[0]?.slice(0, 80) ||
    "未命名推理卷宗"
  ).slice(0, 200);
}

export function prepareBriefForSave(
  brief: BriefContent,
  savedBrief: BriefContent | null,
  sourceRecordIds: number[],
) {
  const authorAnswer =
    brief.resolution_mode === "author_anchored"
      ? brief.author_answer?.trim() || null
      : null;
  const boundaryText = brief.boundary_text?.trim() || null;
  const extractionInputChanged =
    savedBrief !== null &&
    (savedBrief.author_answer !== authorAnswer ||
      savedBrief.boundary_text !== boundaryText);
  return {
    extractionInputChanged,
    content: {
      ...brief,
      source_record_ids: sourceRecordIds,
      creative_intent: brief.creative_intent.trim(),
      reasoning_proposition: brief.reasoning_proposition.trim(),
      author_answer: authorAnswer,
      author_anchors: extractionInputChanged ? [] : brief.author_anchors,
      boundary_text: boundaryText,
      creative_constraints: extractionInputChanged
        ? []
        : brief.creative_constraints,
    } satisfies BriefContent,
  };
}

export function polishCandidateMatchesInput(
  task: Pick<TaskView, "input_source_record_id" | "input_hash">,
  result: BriefPolishResult,
  inputSource: SourceRecordView | null | undefined,
  currentText: string,
) {
  return Boolean(
    task.input_source_record_id !== null &&
      inputSource?.source_record_id === task.input_source_record_id &&
      inputSource.content_text === currentText.trim() &&
      task.input_hash === inputSource.content_hash &&
      result.input_hash === inputSource.content_hash,
  );
}

export function polishProposalWasAdopted(
  result: BriefPolishResult,
  sources: SourceRecordView[],
) {
  return sources.some(
    (source) =>
      source.source_kind === "human_revision" &&
      source.parent_source_record_id ===
        result.proposal_source_record.source_record_id,
  );
}

export function buildAuthorSourceCreateBody(
  contentText: string,
  parentSource: Pick<SourceRecordView, "source_record_id"> | null,
) {
  return parentSource
    ? {
        source_kind: "human_revision" as const,
        content_text: contentText,
        parent_source_record_id: parentSource.source_record_id,
      }
    : {
        source_kind: "human_original" as const,
        content_text: contentText,
      };
}

export function IntakeWorkspace() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [brief, setBrief] = useState<BriefContent>(() => ({
    ...emptyBrief,
    source_record_ids: [],
    author_anchors: [],
    creative_constraints: [],
  }));
  const [sourceText, setSourceText] = useState("");
  const [originalSource, setOriginalSource] = useState<SourceRecordView | null>(
    null,
  );
  const [workingSource, setWorkingSource] = useState<SourceRecordView | null>(
    null,
  );
  const [polishDraft, setPolishDraft] = useState("");
  const [polishReviewOpen, setPolishReviewOpen] = useState(false);
  const [boundaryOpen, setBoundaryOpen] = useState(false);
  const hydratedProjectRef = useRef<number | null>(null);
  const hydratedSourceProjectRef = useRef<number | null>(null);
  const reviewedPolishTaskRef = useRef<number | null>(null);

  const currentProject = useQuery({
    queryKey: ["project", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<ProjectView>(`/projects/${workflow.projectId}`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });
  const currentBrief = useQuery({
    queryKey: ["brief", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<BriefView>(`/projects/${workflow.projectId}/brief`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });
  const sourceRecordsQuery = useQuery({
    queryKey: ["sources", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<SourceRecordView[]>(
        `/projects/${workflow.projectId}/sources`,
        { actorId: workflow.actorId },
      ),
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

  const persistedWorkflowLoading =
    workflow.projectId !== null &&
    (currentBrief.isLoading || sourceRecordsQuery.isLoading);
  const persistedWorkflowError =
    currentBrief.error ?? sourceRecordsQuery.error;

  useEffect(() => {
    if (
      workflow.projectId === null ||
      hydratedProjectRef.current === workflow.projectId ||
      !isBriefContent(currentBrief.data?.content)
    ) {
      return;
    }
    setBrief(currentBrief.data.content);
    hydratedProjectRef.current = workflow.projectId;
  }, [currentBrief.data?.content, workflow.projectId]);

  useEffect(() => {
    if (
      workflow.projectId === null ||
      hydratedSourceProjectRef.current === workflow.projectId ||
      !sourceRecordsQuery.data ||
      !currentBrief.data
    ) {
      return;
    }
    const briefSourceIds = isBriefContent(currentBrief.data?.content)
      ? new Set(currentBrief.data.content.source_record_ids)
      : new Set<number>();
    const relevantSources = sourceRecordsQuery.data
      .filter(
        (source) =>
          briefSourceIds.size === 0 ||
          briefSourceIds.has(source.source_record_id),
      )
      .sort(
        (left, right) => left.source_record_id - right.source_record_id,
      );
    const recoveredOriginal = relevantSources.find(
      (source) => source.source_kind === "human_original",
    );
    const recoveredWorking = [...relevantSources]
      .reverse()
      .find(
        (source) =>
          source.source_record_id !== recoveredOriginal?.source_record_id &&
          source.source_kind !== "human_original" &&
          (briefSourceIds.has(source.source_record_id) ||
            source.source_kind === "human_revision"),
      );
    const timeoutId = window.setTimeout(() => {
      if (recoveredOriginal) {
        setOriginalSource(recoveredOriginal);
      }
      if (recoveredWorking) {
        setWorkingSource(recoveredWorking);
        setSourceText(recoveredWorking.content_text);
      } else if (recoveredOriginal) {
        setSourceText(recoveredOriginal.content_text);
      }
      hydratedSourceProjectRef.current = workflow.projectId;
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [
    currentBrief.data,
    sourceRecordsQuery.data,
    workflow.projectId,
  ]);

  async function ensureProject() {
    if (workflow.projectId !== null) return workflow.projectId;
    const project = await apiRequest<ProjectView>("/projects", {
      actorId: workflow.actorId,
      method: "POST",
      body: {
        title: titleFrom(brief, sourceText),
        description: brief.reasoning_proposition.trim() || null,
        profile: {},
      },
    });
    workflow.setProject(project.id);
    queryClient.setQueryData(
      ["project", workflow.actorId, project.id],
      project,
    );
    return project.id;
  }

  async function ensureAuthorSource(projectId: number) {
    const trimmed = sourceText.trim();
    if (!trimmed) return null;
    if (workflow.projectId !== null && persistedWorkflowLoading) {
      throw new Error("正在恢复已有来源与创作简报，请稍后再试。");
    }
    if (workflow.projectId !== null && persistedWorkflowError) {
      throw new Error("已有来源尚未恢复成功，请先重新读取。");
    }
    const knownOriginal =
      originalSource ??
      sourceRecordsQuery.data?.find(
        (source) => source.source_kind === "human_original",
      ) ??
      null;
    const currentAuthorSource = workingSource ?? knownOriginal;
    if (currentAuthorSource?.content_text === trimmed) {
      return currentAuthorSource;
    }
    const existingAuthorSource = sourceRecordsQuery.data?.find(
      (source) =>
        source.source_kind !== "agent_polish_proposal" &&
        source.content_text === trimmed,
    );
    if (existingAuthorSource) {
      if (existingAuthorSource.source_kind === "human_original") {
        setOriginalSource(existingAuthorSource);
        setWorkingSource(null);
      } else {
        setWorkingSource(existingAuthorSource);
      }
      return existingAuthorSource;
    }
    const isFirstSource = knownOriginal === null;
    const source = await apiRequest<SourceRecordView>(
      `/projects/${projectId}/sources`,
      {
        actorId: workflow.actorId,
        method: "POST",
        body: buildAuthorSourceCreateBody(
          trimmed,
          isFirstSource ? null : (currentAuthorSource ?? knownOriginal),
        ),
      },
    );
    if (isFirstSource) {
      setOriginalSource(source);
      setWorkingSource(null);
    } else {
      if (!originalSource && knownOriginal) setOriginalSource(knownOriginal);
      setWorkingSource(source);
    }
    queryClient.setQueryData<SourceRecordView[]>(
      ["sources", workflow.actorId, projectId],
      (current = []) =>
        current.some(
          (item) => item.source_record_id === source.source_record_id,
        )
          ? current
          : [...current, source],
    );
    return source;
  }

  const polishTaskId = workflow.taskRunIds.brief_polish;
  const polishRecovery = useRecoverableTask(
    workflow.projectId,
    workflow.actorId,
    "brief_polish",
    polishTaskId,
    workflow.ready,
  );
  const polishTask = polishRecovery.task;
  const polishEventStream = useTaskEventStream(
    workflow.projectId,
    workflow.actorId,
    polishTask?.task_run_id ?? null,
  );

  useEffect(() => {
    if (!isPolishResult(polishTask) || !sourceRecordsQuery.isSuccess) {
      return;
    }
    if (
      polishProposalWasAdopted(
        polishTask.result,
        sourceRecordsQuery.data,
      )
    ) {
      reviewedPolishTaskRef.current = polishTask.task_run_id;
      return;
    }
    if (reviewedPolishTaskRef.current === polishTask.task_run_id) return;
    reviewedPolishTaskRef.current = polishTask.task_run_id;
    setPolishDraft(polishTask.result.polished_text);
    setPolishReviewOpen(true);
  }, [polishTask, sourceRecordsQuery.data, sourceRecordsQuery.isSuccess]);

  useEffect(() => {
    if (
      polishTask &&
      workflow.taskRunIds.brief_polish !== polishTask.task_run_id
    ) {
      workflow.setTask("brief_polish", polishTask.task_run_id);
    }
  }, [polishTask, workflow]);

  const polishMutation = useMutation({
    mutationFn: async () => {
      if (!sourceText.trim()) throw new Error("请先写下原始创意。");
      if (!providerQuery.data) throw new Error("请先配置当前 Agent 模型。");
      const projectId = await ensureProject();
      const source = await ensureAuthorSource(projectId);
      if (!source) throw new Error("原始创意尚未形成可用的来源记录。");
      return apiRequest<TaskView>(
        `/projects/${projectId}/tasks/brief-polish`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: {
            source_record_id: source.source_record_id,
            provider: workflow.provider,
          },
        },
      );
    },
    onSuccess: (task) => {
      reviewedPolishTaskRef.current = null;
      workflow.setTask("brief_polish", task.task_run_id);
      queryClient.setQueryData(
        [
          "latest-task",
          workflow.actorId,
          task.project_id,
          "brief_polish",
        ],
        task,
      );
      queryClient.setQueryData(
        [
          "task",
          workflow.actorId,
          task.project_id,
          task.task_run_id,
        ],
        task,
      );
    },
  });

  const adoptPolishMutation = useMutation({
    mutationFn: async () => {
      const task = polishTask;
      if (!isPolishResult(task)) {
        throw new Error("当前没有可采用的润色提案。");
      }
      const taskInputSource =
        sourceRecordsQuery.data?.find(
          (source) =>
            source.source_record_id ===
            task.input_source_record_id,
        ) ?? originalSource;
      if (
        !polishCandidateMatchesInput(
          task,
          task.result,
          taskInputSource,
          sourceText,
        )
      ) {
        throw new Error(
          "原稿已在润色任务之后发生变化；请丢弃旧候选并重新运行 Agent 润色。",
        );
      }
      const result = task.result;
      const edited = polishDraft.trim();
      if (!edited) throw new Error("润色工作稿不能为空。");
      if (workflow.projectId === null) throw new Error("润色任务缺少所属项目。");
      return apiRequest<SourceRecordView>(
        `/projects/${workflow.projectId}/sources`,
        {
          actorId: workflow.actorId,
          method: "POST",
          body: buildAuthorSourceCreateBody(
            edited,
            result.proposal_source_record,
          ),
        },
      );
    },
    onSuccess: (source) => {
      setWorkingSource(source);
      setSourceText(source.content_text);
      if (workflow.projectId !== null) {
        queryClient.setQueryData<SourceRecordView[]>(
          ["sources", workflow.actorId, workflow.projectId],
          (current = []) =>
            current.some(
              (item) => item.source_record_id === source.source_record_id,
            )
              ? current
              : [...current, source],
        );
      }
      setPolishReviewOpen(false);
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!sourceText.trim() && brief.source_record_ids.length === 0) {
        throw new Error("请先填写原始创意。");
      }
      const projectId = await ensureProject();
      const source = await ensureAuthorSource(projectId);
      const sourceRecordIds = uniqueIds([
        ...brief.source_record_ids,
        source?.source_record_id,
        workingSource?.source_record_id,
      ]);
      const savedBrief = isBriefContent(currentBrief.data?.content)
        ? currentBrief.data.content
        : null;
      const {
        content: normalizedBrief,
        extractionInputChanged,
      } = prepareBriefForSave(brief, savedBrief, sourceRecordIds);
      if (!normalizedBrief.creative_intent) {
        throw new Error("请填写一句话创作意图。");
      }
      if (!normalizedBrief.reasoning_proposition) {
        throw new Error("请填写核心推理命题。");
      }
      if (
        normalizedBrief.resolution_mode === "author_anchored" &&
        !normalizedBrief.author_answer
      ) {
        throw new Error("选择“按作者底牌展开”后，需要填写作者底牌。");
      }

      await apiRequest<ProjectView>(`/projects/${projectId}`, {
        actorId: workflow.actorId,
        method: "PATCH",
        body: {
          title: titleFrom(normalizedBrief, sourceText),
          description: normalizedBrief.reasoning_proposition,
          profile: {},
        },
      });
      const saved = await apiRequest<BriefView>(
        `/projects/${projectId}/brief`,
        {
          actorId: workflow.actorId,
          method: "PUT",
          body: {
            expected_revision: currentBrief.data?.draft_revision ?? 1,
            content: normalizedBrief,
          },
        },
      );
      queryClient.setQueryData(
        ["brief", workflow.actorId, projectId],
        saved,
      );
      if (isBriefContent(saved.content)) setBrief(saved.content);

      let extractTask: TaskView | null = null;
      const extractionIncomplete =
        (Boolean(normalizedBrief.author_answer) &&
          normalizedBrief.author_anchors.length === 0) ||
        (Boolean(normalizedBrief.boundary_text) &&
          normalizedBrief.creative_constraints.length === 0);
      if (
        (normalizedBrief.author_answer || normalizedBrief.boundary_text) &&
        (extractionInputChanged || extractionIncomplete)
      ) {
        try {
          extractTask = await apiRequest<TaskView>(
            `/projects/${projectId}/tasks/brief-anchor-extract`,
            {
              actorId: workflow.actorId,
              method: "POST",
              body: {
                expected_brief_revision: saved.draft_revision,
                provider: workflow.provider,
              },
            },
          );
        } catch (error) {
          throw new Error(
            `创作简报已保存，但自动拆解未能启动：${errorMessage(error)}`,
          );
        }
      }
      return { projectId, extractTask };
    },
    onSuccess: ({ extractTask }) => {
      if (extractTask) {
        workflow.setTask("brief_anchor_extract", extractTask.task_run_id);
      }
      router.push("/brief");
    },
  });

  const structuredComplete =
    Number(Boolean(brief.creative_intent.trim())) +
    Number(Boolean(brief.reasoning_proposition.trim())) +
    1;
  const sourceLength = sourceText.trim().length;
  const projectTitle =
    currentProject.data?.title ??
    (workflow.projectId ? `项目 #${workflow.projectId}` : null);
  const documentTitle =
    projectTitle ??
    (brief.creative_intent.trim() || "未命名卷宗");
  const polishRunning =
    polishMutation.isPending ||
    Boolean(
      polishTask &&
        !terminalTaskStatuses.has(polishTask.status),
    );
  const polishResult = isPolishResult(polishTask)
    ? polishTask.result
    : null;
  const polishResultAdopted = Boolean(
    polishResult &&
      sourceRecordsQuery.data &&
      polishProposalWasAdopted(polishResult, sourceRecordsQuery.data),
  );
  const polishInputSource =
    sourceRecordsQuery.data?.find(
      (source) =>
        source.source_record_id ===
        polishTask?.input_source_record_id,
    ) ?? originalSource;
  const polishCandidateStale = Boolean(
    polishResult &&
      polishTask &&
      !polishCandidateMatchesInput(
        polishTask,
        polishResult,
        polishInputSource,
        sourceText,
      ),
  );
  const displayedError = useMemo(
    () =>
      createMutation.error ??
      polishMutation.error ??
      adoptPolishMutation.error ??
      persistedWorkflowError ??
      providerQuery.error ??
      polishRecovery.error,
    [
      adoptPolishMutation.error,
      createMutation.error,
      persistedWorkflowError,
      polishMutation.error,
      polishRecovery.error,
      providerQuery.error,
    ],
  );
  const saveStatus = createMutation.isPending
    ? "写入中"
    : workflow.projectId
      ? "已建案"
      : "未提交";

  function resetIntake() {
    workflow.clear();
    hydratedProjectRef.current = null;
    hydratedSourceProjectRef.current = null;
    reviewedPolishTaskRef.current = null;
    setBrief({
      ...emptyBrief,
      source_record_ids: [],
      author_anchors: [],
      creative_constraints: [],
    });
    setSourceText("");
    setOriginalSource(null);
    setWorkingSource(null);
    setPolishDraft("");
    setPolishReviewOpen(false);
  }

  return (
    <main
      aria-busy={!workflow.ready || createMutation.isPending}
      className={`document ${styles.homeDocument}`}
    >
      <DocumentHeader
        eyebrow="建案中心 · 真实工作流"
        meta={[
          {
            label: "记录编号",
            value: workflow.projectId
              ? `项目-${workflow.projectId}`
              : "待分配",
          },
          { label: "数据位置", value: "PostgreSQL" },
          {
            label: "保存状态",
            value: saveStatus,
            tone: displayedError ? "critical" : "default",
          },
        ]}
        title={`新卷宗：${documentTitle}`}
      />

      <CaseSpine current="idea" />

      <form
        className={styles.homeGrid}
        onSubmit={(event) => {
          event.preventDefault();
          createMutation.mutate();
        }}
      >
        <aside className={styles.ledgerColumn}>
          <section className={`paper-panel ${styles.intakeLedger}`}>
            <PanelHeader
              code="建案 / 01"
              title="建案入口"
              trailing={<StatusBadge tone="red">当前路径</StatusBadge>}
            />
            <div className={styles.routeList}>
              <div
                aria-current="true"
                aria-label={`当前选择：${currentIntakeRoute.title}`}
                className={styles.primaryRoute}
              >
                <span className={styles.routeCode}>
                  {currentIntakeRoute.code}
                </span>
                <span>
                  <strong>{currentIntakeRoute.title}</strong>
                  <small>{currentIntakeRoute.detail}</small>
                </span>
                <em>正在使用</em>
              </div>
              <div className={styles.plannedRoutes}>
                <div className={styles.plannedCaption}>
                  <span>更多建案方式</span>
                  <b>规划中 / 03</b>
                </div>
                <div className={styles.plannedRouteGrid}>
                  {plannedIntakeRoutes.map((route) => (
                    <button
                      aria-label={`${route.title}尚在规划：${route.detail}`}
                      disabled
                      key={route.code}
                      title={route.detail}
                      type="button"
                    >
                      <span>{route.code}</span>
                      <strong>{route.title}</strong>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section className={`paper-panel ${styles.processLedger}`}>
            <PanelHeader
              code="真实写入路径"
              title="真实写入流程"
              trailing={<StatusBadge tone="dark">人工建案</StatusBadge>}
            />
            <ol className={styles.processList}>
              <li>
                <b>01</b>
                <span>
                  <strong>建立来源与通用简报</strong>
                  <small>原稿进入不可变来源记录，不再混入创作简报。</small>
                </span>
              </li>
              <li>
                <b>02</b>
                <span>
                  <strong>Agent 预处理</strong>
                  <small>保存后自动拆解底牌与创作边界。</small>
                </span>
              </li>
              <li>
                <b>03</b>
                <span>
                  <strong>人工审阅原子约束</strong>
                  <small>逐条确认后才冻结创作简报版本。</small>
                </span>
              </li>
            </ol>
            <div className={styles.processState}>
              <span>当前状态</span>
              <b>
                {workflow.projectId
                  ? "已有真实卷宗可恢复"
                  : "等待填写并提交"}
              </b>
            </div>
          </section>

          {workflow.projectId ? (
            <section className={`paper-panel ${styles.recentCase}`}>
              <PanelHeader
                code={`项目-${workflow.projectId}`}
                title="当前真实卷宗"
                trailing={<StatusBadge tone="red">可恢复</StatusBadge>}
              />
              <div className={styles.recentCaseBody}>
                <span className={styles.miniOrbit} aria-hidden="true" />
                <span>
                  <strong>{projectTitle}</strong>
                  <small>来源记录与创作简报已在 PostgreSQL 建立</small>
                </span>
              </div>
              <div className={styles.resumeActions}>
                <button onClick={resetIntake} type="button">
                  开始新案
                </button>
                <Link href="/brief">继续审阅创作简报 ↗</Link>
              </div>
            </section>
          ) : (
            <section className={`paper-panel ${styles.recentCase}`}>
              <PanelHeader
                code="项目待建立"
                title="当前真实卷宗"
                trailing={
                  <StatusBadge tone="neutral">尚未建立</StatusBadge>
                }
              />
              <div className={styles.emptyRecent}>
                <span className={styles.miniOrbit} aria-hidden="true" />
                <p>润色或提交后，真实卷宗会在这里提供恢复入口。</p>
              </div>
            </section>
          )}
        </aside>

        <section
          className={styles.blueprintStage}
          aria-label="真实创意建案工作区"
        >
          <div className={styles.blueprint} aria-hidden="true">
            <span className={styles.orbitOne} />
            <span className={styles.orbitTwo} />
            <span className={styles.stationAxis} />
            <span className={styles.stationCore}>01</span>
            <i className={styles.crosshairOne} />
            <i className={styles.crosshairTwo} />
          </div>
          <span className={styles.coordinateTop}>实时数据库 · 契约 V1</span>
          <span className={styles.coordinateSide}>
            来源记录 / 创作简报 / 核心草稿
          </span>

          <div className={styles.heroCopy}>
            <span>卷宗用途 / 001</span>
            <h2>
              把一句念头，
              <br />
              立成一份卷宗。
            </h2>
            <p>
              创作简报定义创作意图与推理命题；媒介、玩家和成品形态不在这里预设。
            </p>
          </div>

          <div className={styles.recordComparison}>
            <section className={styles.ideaRecord}>
              <header>
                <span>原始创意 / 作者原稿</span>
                <StatusBadge tone="dark">人工原稿</StatusBadge>
              </header>
              <label htmlFor="casefile-source-text">
                原始创意
                <small>
                  原稿将成为不可变来源记录；Agent 润色只生成独立提案。
                </small>
              </label>
              <textarea
                aria-label="原始创意"
                id="casefile-source-text"
                onChange={(event) => {
                  setSourceText(event.target.value);
                  if (
                    originalSource &&
                    originalSource.content_text !== event.target.value.trim()
                  ) {
                    setWorkingSource(null);
                  }
                }}
                placeholder={
                  brief.source_record_ids.length
                    ? `已保留 ${brief.source_record_ids.length} 条来源记录；可在此追加新版原稿。`
                    : "例如：一艘渡轮每天午夜都会重新驶回同一座码头……"
                }
                required={brief.source_record_ids.length === 0}
                rows={8}
                value={sourceText}
              />
              <footer>
                <span>字符：{sourceLength}</span>
                <span>
                  来源：
                  {workingSource
                    ? "人工确认工作稿"
                    : originalSource
                      ? "来源记录已建立"
                      : "人工输入"}
                </span>
                <span>写入：来源记录</span>
              </footer>
              <div className={styles.recordActions}>
                <button
                  aria-label="让 Agent 对原始创意做语义保真润色"
                  className={styles.polishAction}
                  disabled={
                    polishRunning ||
                    persistedWorkflowLoading ||
                    !sourceText.trim() ||
                    !providerQuery.data
                  }
                  onClick={() => polishMutation.mutate()}
                  title={
                    providerQuery.data
                      ? "生成独立润色提案，不覆盖原稿"
                      : "请先在设置中配置当前模型"
                  }
                  type="button"
                >
                  {polishRunning ? "Agent 润色中…" : "Agent 润色"}
                </button>
                <button
                  aria-label="保存通用简报并进入原子约束审阅"
                  disabled={
                    createMutation.isPending || persistedWorkflowLoading
                  }
                  type="submit"
                >
                  <span>
                    {createMutation.isPending
                      ? "正在保存与预处理…"
                      : "保存并审阅创作简报"}
                  </span>
                  <b>→</b>
                </button>
              </div>
            </section>

            <section className={styles.briefStructure}>
              <header>
                <span>创作简报核心 / 3 项必填</span>
                <StatusBadge
                  tone={structuredComplete === 3 ? "dark" : "red"}
                >
                  {structuredComplete}/3 已填写
                </StatusBadge>
              </header>
              <div className={styles.structureFields}>
                <label
                  className={styles.structureField}
                  htmlFor="casefile-creative-intent"
                >
                  <span className={styles.fieldIndex}>01</span>
                  <span className={styles.fieldCopy}>
                    <b>一句话创作意图</b>
                    <small>这份推理内容整体准备建立什么。</small>
                  </span>
                  <textarea
                    aria-label="一句话创作意图"
                    id="casefile-creative-intent"
                    onChange={(event) =>
                      setBrief((current) => ({
                        ...current,
                        creative_intent: event.target.value,
                      }))
                    }
                    placeholder="例如：建立一个关于保护与自主选择冲突的循环航行故事。"
                    required
                    rows={2}
                    value={brief.creative_intent}
                  />
                </label>

                <label
                  className={styles.structureField}
                  htmlFor="casefile-reasoning-proposition"
                >
                  <span className={styles.fieldIndex}>02</span>
                  <span className={styles.fieldCopy}>
                    <b>核心推理命题</b>
                    <small>卷宗希望探索、判断或解释什么。</small>
                  </span>
                  <textarea
                    aria-label="核心推理命题"
                    id="casefile-reasoning-proposition"
                    onChange={(event) =>
                      setBrief((current) => ({
                        ...current,
                        reasoning_proposition: event.target.value,
                      }))
                    }
                    placeholder="解释渡轮为何不断回航，以及这一行为是否应被终止。"
                    required
                    rows={2}
                    value={brief.reasoning_proposition}
                  />
                </label>

                <fieldset className={styles.resolutionField}>
                  <legend>
                    <span className={styles.fieldIndex}>03</span>
                    <span className={styles.fieldCopy}>
                      <b>结论处理方式</b>
                      <small>决定草稿如何面对尚未形成的答案。</small>
                    </span>
                  </legend>
                  <div className={styles.resolutionOptions}>
                    {resolutionModes.map((mode) => (
                      <label
                        className={
                          brief.resolution_mode === mode.value
                            ? styles.resolutionOptionActive
                            : styles.resolutionOption
                        }
                        key={mode.value}
                      >
                        <input
                          checked={brief.resolution_mode === mode.value}
                          name="resolution-mode"
                          onChange={() =>
                            setBrief((current) => ({
                              ...current,
                              resolution_mode: mode.value,
                              author_answer:
                                mode.value === "author_anchored"
                                  ? current.author_answer
                                  : null,
                            }))
                          }
                          type="radio"
                          value={mode.value}
                        />
                        <span>
                          <b>{mode.label}</b>
                          <small>{mode.detail}</small>
                        </span>
                        <em>{mode.code}</em>
                      </label>
                    ))}
                  </div>
                </fieldset>

                {brief.resolution_mode === "author_anchored" ? (
                  <label
                    className={`${styles.structureField} ${styles.optionalField}`}
                    htmlFor="casefile-author-answer"
                  >
                    <span className={styles.fieldIndex}>04</span>
                    <span className={styles.fieldCopy}>
                      <b>作者底牌</b>
                      <small>
                        当前版本的硬约束原文；保存后由 Agent 自动拆解。
                      </small>
                    </span>
                    <textarea
                      aria-label="作者底牌"
                      id="casefile-author-answer"
                      onChange={(event) =>
                        setBrief((current) => ({
                          ...current,
                          author_answer: event.target.value,
                        }))
                      }
                      placeholder="例如：AI 主动让渡轮回航，目的是保护乘客，船长并不知情。"
                      required
                      rows={3}
                      value={brief.author_answer ?? ""}
                    />
                  </label>
                ) : null}

                <details
                  className={styles.boundaryDisclosure}
                  onToggle={(event) => {
                    if (!brief.boundary_text) {
                      setBoundaryOpen(event.currentTarget.open);
                    }
                  }}
                  open={boundaryOpen || Boolean(brief.boundary_text)}
                >
                  <summary>
                    <span className={styles.fieldIndex}>05</span>
                    <span className={styles.fieldCopy}>
                      <b>创作边界</b>
                      <small>可选；审阅时逐条标记硬约束或软偏好。</small>
                    </span>
                    <em>{brief.boundary_text ? "已填写" : "可选展开"}</em>
                  </summary>
                  <textarea
                    aria-label="创作边界"
                    onChange={(event) =>
                      setBrief((current) => ({
                        ...current,
                        boundary_text: event.target.value,
                      }))
                    }
                    placeholder="例如：不能出现超自然力量；整体氛围尽量克制。"
                    rows={3}
                    value={brief.boundary_text ?? ""}
                  />
                </details>
              </div>
              <footer className={styles.structureFooter}>
                <span>
                  保存后自动拆解底牌与边界；确认前不会写入硬约束。
                </span>
                <b>创作简报核心 · V1</b>
              </footer>
            </section>
          </div>

          {displayedError ||
          polishTask?.status === "failed" ? (
            <p className={styles.formError} role="alert">
              <b>
                {createMutation.isError
                  ? "建案失败"
                  : polishTask?.status === "failed"
                    ? "润色失败"
                    : "Agent 操作失败"}
              </b>
              <span>
                {displayedError
                  ? errorMessage(displayedError)
                  : polishTask?.failure?.message ??
                    "润色任务未能完成，请稍后重试。"}
              </span>
            </p>
          ) : null}
          {polishEventStream.streamError ? (
            <p className={styles.formError} role="alert">
              <b>润色审计流已中断</b>
              <span>{polishEventStream.streamError}</span>
              <button onClick={polishEventStream.reconnect} type="button">
                按最后序号重连
              </button>
            </p>
          ) : null}
        </section>
      </form>

      <footer className={styles.documentFooter}>
        <span>
          <b>真实模式：</b>PostgreSQL 持久化
        </span>
        <span>创作简报核心：3 项必填</span>
        <span className={styles.footerNotice}>
          {workingSource
            ? "Agent 提案已由作者确认，原稿仍完整保留。"
            : workflow.projectId
              ? "当前卷宗可继续进入创作简报审阅。"
              : "先保留来源，再由作者确认"}
        </span>
        <span>CaseFile · 实时数据 V1</span>
      </footer>

      {polishReviewOpen && polishResult && !polishResultAdopted ? (
        <div className={styles.polishBackdrop} role="presentation">
          <section
            aria-label="Agent 润色提案审阅"
            aria-modal="true"
            className={styles.polishDialog}
            role="dialog"
          >
            <header>
              <div>
                <span>
                  Agent 润色校样 · 任务 #
                  {polishTask?.task_run_id ?? "—"}
                </span>
                <h2>语义保真润色校样</h2>
              </div>
              <StatusBadge tone="red">待人工决定</StatusBadge>
            </header>
            <div className={styles.polishComparison}>
              <section>
                <small>作者原稿 / 只读</small>
                <p>{polishInputSource?.content_text ?? sourceText.trim()}</p>
              </section>
              <label>
                <small>Agent 候选稿 / 可编辑</small>
                <textarea
                  aria-label="编辑 Agent 润色工作稿"
                  onChange={(event) => setPolishDraft(event.target.value)}
                  rows={9}
                  value={polishDraft}
                />
              </label>
            </div>
            <div className={styles.polishNotes}>
              <span>
                <b>保真摘要</b>
                <p>{polishResult.preserved_intent_summary}</p>
              </span>
              <span>
                <b>仍有歧义</b>
                <p>
                  {polishResult.ambiguities.length
                    ? polishResult.ambiguities.join("；")
                    : "未标出需要作者补充的歧义。"}
                </p>
              </span>
            </div>
            {polishCandidateStale ? (
              <p className={styles.dialogError} role="alert">
                原稿已在本次任务后发生变化。旧候选保留用于审计，但不能再采用；请关闭后重新运行润色。
              </p>
            ) : null}
            {adoptPolishMutation.isError ? (
              <p className={styles.dialogError}>
                {errorMessage(adoptPolishMutation.error)}
              </p>
            ) : null}
            <footer>
              <button
                onClick={() => {
                  setPolishReviewOpen(false);
                  setPolishDraft(polishResult.polished_text);
                }}
                type="button"
              >
                丢弃提案
              </button>
              <button
                disabled={
                  adoptPolishMutation.isPending || polishCandidateStale
                }
                onClick={() => adoptPolishMutation.mutate()}
                type="button"
              >
                {adoptPolishMutation.isPending
                  ? "正在记录…"
                  : polishDraft.trim() === polishResult.polished_text.trim()
                    ? "全部采用 →"
                    : "采用编辑稿 →"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </main>
  );
}
