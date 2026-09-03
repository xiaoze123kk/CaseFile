"use client";

import dynamic from "next/dynamic";
import Image from "next/image";
import Link from "next/link";
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  ApiError,
  errorMessage,
  fetchWorkbenchContext,
  rerunVerification,
  type AgentChatRoutingHint,
  type DraftCandidatePreviewView,
  type DraftView,
  type CaseFileDocument,
  type TimelineTemporalPosition,
  type TimelineTimePreviewView,
  type WorkbenchContextView,
} from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";

import {
  defaultWorkbenchSeed,
  getEvent,
  getObject,
  type IssueStatus,
  objectKindLabels,
  type WorkbenchCandidate,
  type WorkbenchSeed,
} from "./analyst-fixture";
import {
  type WorkbenchCandidateStatus,
  useCaseSession,
} from "@/features/case-session/case-session-provider";
import {
  fetchCaseDraft,
  fetchDraftCandidatePreview,
  previewCaseDraftEventTime,
} from "@/features/case-session/case-session-api";
import styles from "./analyst-workbench.module.css";
import gateStyles from "./workbench-gate.module.css";
import { AgentLivePanel } from "./workbench-agent-live-panel";
import { AgentPanel } from "./workbench-agent-panel";
import {
  WorkbenchAgentSurface,
  type AgentSurface,
} from "./workbench-agent-surface";
import { clamp } from "./workbench-geometry";
import { WorkbenchIcon } from "./workbench-icon";
import {
  DraftSwitcher,
  ProjectSwitcher,
} from "./workbench-scope-switcher";
import {
  type DirectoryObjectKind,
  directoryObjectKind,
  fixtureObjectKinds,
  productionObjectKinds,
  WorkbenchObjectDirectory,
} from "./workbench-object-directory";
import {
  createObjectNavigationHistory,
  moveObjectHistoryBack,
  moveObjectHistoryForward,
  objectFocusBackTarget,
  objectFocusForwardTarget,
  recordObjectFocus,
  type ObjectFocusFrame,
} from "./workbench-navigation-history";
import { WorkbenchContextInspector } from "./workbench-context-inspector";
import { type WorkbenchContextState } from "./workbench-context-panels";
import {
  mapCaseFileToWorkbenchModel,
  mapFixtureToWorkbenchModel,
  type WorkbenchModel,
} from "./workbench-real-data";
import { ReasoningGraphView } from "./workbench-reasoning-graph";
import { RelationshipGraph } from "./workbench-relationship-graph";
import { EvidenceComparisonView } from "./workbench-evidence-comparison";
import { ValidationIssuePanel } from "./workbench-validation-issues";
import { TimelineOverview } from "./timeline/timeline-overview";
import {
  CompileCenterView,
  ExportView,
} from "./workbench-secondary-views";
import {
  workbenchViewOptions,
  type WorkbenchView,
} from "./workbench-views";
import {
  useWorkbenchObjectPersistence,
  type ObjectSaveResult,
} from "./workbench-object-persistence";
import type {
  ReloadedSpatialLocation,
  SpatialPositionPayload,
  SpatialPositionSaveResult,
} from "./workbench-real-data-types";

const SpatialMapView = dynamic(
  () =>
    import("./spatial-map/spatial-map-view").then((module) => module.SpatialMapView),
  {
    ssr: false,
    loading: () => (
      <section aria-busy="true" className={styles.realEmptyState}>
        <strong>正在载入空间卷宗</strong>
        <p>地图渲染器只在进入地图视图后加载。</p>
      </section>
    ),
  },
);

type MobileRegion = "objects" | "canvas" | "inspector";
type WorkspaceMode = "workbench" | "dossier" | "analysis" | "compile";

interface AuditEntry {
  id: string;
  time: string;
  actor: string;
  action: string;
  detail: string;
}

function createIssueStatuses(seed: WorkbenchSeed) {
  return Object.fromEntries(
    seed.validationIssues.map((issue) => [issue.id, "open"]),
  ) as Record<string, IssueStatus>;
}

const mobileRegions: Array<{ id: MobileRegion; label: string }> = [
  { id: "objects", label: "对象" },
  { id: "canvas", label: "主画布" },
  { id: "inspector", label: "上下文" },
];

const analysisViewOptions = workbenchViewOptions.filter((option) =>
  ["timeline", "relations", "reasoning", "evidence", "map"].includes(option.id),
);

const compileViewOptions = workbenchViewOptions.filter((option) =>
  ["compile", "export"].includes(option.id),
);

const workspaceModeCopy: Record<
  WorkspaceMode,
  { label: string; eyebrow: string; description: string }
> = {
  workbench: {
    label: "工作台",
    eyebrow: "当前工作",
    description: "从最值得处理的问题继续",
  },
  dossier: {
    label: "对象档案",
    eyebrow: "工作台",
    description: "浏览人物、事件、地点与信息",
  },
  analysis: {
    label: "分析",
    eyebrow: "故事结构",
    description: "检查时间、关系、推理与空间",
  },
  compile: {
    label: "编译作品",
    eyebrow: "阶段出口",
    description: "把已确认的卷宗转成作品",
  },
};

function currentClock() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

function workbenchErrorMessage(error: unknown) {
  const message = errorMessage(error);
  return /[\u3400-\u9fff]/u.test(message)
    ? message
    : "工作台数据读取失败，请检查连接后重试。";
}

function FocusTrapDialog({
  open,
  query,
  onQueryChange,
  onClose,
  modalRef,
  inputRef,
  children,
}: {
  open: boolean;
  query: string;
  onQueryChange: (value: string) => void;
  onClose: () => void;
  modalRef: RefObject<HTMLElement | null>;
  inputRef: RefObject<HTMLInputElement | null>;
  children: ReactNode;
}) {
  if (!open) return null;

  return (
    <div className={styles.paletteBackdrop} onMouseDown={onClose} role="presentation">
      <section
        aria-labelledby="command-palette-title"
        aria-modal="true"
        className={styles.palette}
        onMouseDown={(event) => event.stopPropagation()}
        ref={modalRef}
        role="dialog"
      >
        <header className={styles.paletteHeader}>
          <div>
            <span>全局命令</span>
            <strong id="command-palette-title">定位对象、视图或问题</strong>
          </div>
          <button aria-label="关闭命令面板" onClick={onClose} type="button">
            <WorkbenchIcon name="close" />
          </button>
        </header>
        <label className={styles.paletteSearch}>
          <WorkbenchIcon name="search" />
          <span className={styles.srOnly}>搜索命令或对象</span>
          <input
            aria-label="搜索命令或对象"
            autoComplete="off"
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="输入对象、问题、来源或命令…"
            ref={inputRef}
            value={query}
          />
          <kbd>ESC</kbd>
        </label>
        <div className={styles.paletteResults}>{children}</div>
        <footer className={styles.paletteFooter}>
          <span>↑↓ 浏览</span>
          <span>Enter 打开</span>
          <span>Tab 循环焦点</span>
        </footer>
      </section>
    </div>
  );
}

const DEFAULT_RAIL_WIDTH = 224;
const DEFAULT_INSPECTOR_WIDTH = 344;
const WORKBENCH_HANDOFF_MIN_MS = 1200;

function waitForWorkbenchHandoff(startedAt: number) {
  if (process.env.NODE_ENV === "test") return Promise.resolve();
  const remaining = Math.max(
    0,
    WORKBENCH_HANDOFF_MIN_MS - (performance.now() - startedAt),
  );
  return remaining === 0
    ? Promise.resolve()
    : new Promise<void>((resolve) => window.setTimeout(resolve, remaining));
}

export function AnalystWorkbench({
  requestedProjectId,
  requestedPreviewTaskRunId = null,
  invalidProjectId = false,
  invalidPreviewTaskRunId = false,
}: {
  /** `undefined` is reserved for the isolated fixture harness used by component tests. */
  requestedProjectId?: number | null;
  requestedPreviewTaskRunId?: number | null;
  invalidProjectId?: boolean;
  invalidPreviewTaskRunId?: boolean;
}) {
  const {
    activeProjectId,
    activeCandidate,
    adoptCandidate,
    candidateStatus,
    loadProject,
  } = useCaseSession();
  const fixtureMode = requestedProjectId === undefined;
  const projectId = fixtureMode ? null : requestedProjectId;
  const previewTaskRunId = fixtureMode ? null : requestedPreviewTaskRunId;
  const previewMode = previewTaskRunId !== null;
  const [draftLoad, setDraftLoad] = useState<{
    projectId: number;
    draft: DraftView | null;
    error: string | null;
  } | null>(null);
  const [reloadDraft, setReloadDraft] = useState(0);
  const [previewLoad, setPreviewLoad] = useState<{
    projectId: number;
    taskRunId: number;
    preview: DraftCandidatePreviewView | null;
    error: string | null;
  } | null>(null);
  const [reloadPreview, setReloadPreview] = useState(0);
  const [contextLoad, setContextLoad] = useState<{
    projectId: number;
    context: WorkbenchContextView | null;
    error: string | null;
  } | null>(null);
  const [reloadContext, setReloadContext] = useState(0);

  useEffect(() => {
    if (
      fixtureMode ||
      invalidProjectId ||
      invalidPreviewTaskRunId ||
      previewMode ||
      projectId === null
    ) {
      return;
    }
    let active = true;
    const handoffStartedAt = performance.now();
    if (activeProjectId !== projectId) {
      void loadProject(projectId).catch(() => undefined);
    }
    void fetchCaseDraft(projectId)
      .then(async (nextDraft) => {
        await waitForWorkbenchHandoff(handoffStartedAt);
        if (active) {
          setDraftLoad({ projectId, draft: nextDraft, error: null });
        }
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setDraftLoad({
          projectId,
          draft: null,
          error: workbenchErrorMessage(caught),
        });
      });
    return () => {
      active = false;
    };
  }, [
    activeProjectId,
    fixtureMode,
    invalidPreviewTaskRunId,
    invalidProjectId,
    loadProject,
    previewMode,
    projectId,
    reloadDraft,
  ]);

  useEffect(() => {
    if (
      fixtureMode ||
      invalidProjectId ||
      invalidPreviewTaskRunId ||
      projectId === null ||
      previewTaskRunId === null
    ) {
      return;
    }
    let active = true;
    const handoffStartedAt = performance.now();
    void fetchDraftCandidatePreview(projectId, previewTaskRunId)
      .then(async (preview) => {
        await waitForWorkbenchHandoff(handoffStartedAt);
        if (active) {
          setPreviewLoad({
            projectId,
            taskRunId: previewTaskRunId,
            preview,
            error: null,
          });
        }
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setPreviewLoad({
          projectId,
          taskRunId: previewTaskRunId,
          preview: null,
          error: workbenchErrorMessage(caught),
        });
      });
    return () => {
      active = false;
    };
  }, [
    fixtureMode,
    invalidPreviewTaskRunId,
    invalidProjectId,
    previewTaskRunId,
    projectId,
    reloadPreview,
  ]);

  useEffect(() => {
    if (
      fixtureMode ||
      invalidProjectId ||
      invalidPreviewTaskRunId ||
      previewMode ||
      projectId === null
    ) {
      return;
    }
    let active = true;
    void fetchWorkbenchContext(LOCAL_ACTOR_ID, projectId)
      .then((context) => {
        if (active) setContextLoad({ projectId, context, error: null });
      })
      .catch((caught: unknown) => {
        if (active) {
          setContextLoad({
            projectId,
            context: null,
            error: workbenchErrorMessage(caught),
          });
        }
      });
    return () => {
      active = false;
    };
  }, [
    fixtureMode,
    invalidPreviewTaskRunId,
    invalidProjectId,
    previewMode,
    projectId,
    reloadContext,
  ]);

  const fixtureSeed = mapFixtureToWorkbenchModel(
    activeCandidate?.workbenchSeed ?? defaultWorkbenchSeed,
  );
  const activeCandidateStatus = activeCandidate
    ? candidateStatus(activeCandidate)
    : null;
  const currentDraftLoad =
    projectId !== null && draftLoad?.projectId === projectId ? draftLoad : null;
  const draft = currentDraftLoad?.draft ?? null;
  const draftError = currentDraftLoad?.error ?? null;
  const currentPreviewLoad =
    projectId !== null &&
    previewTaskRunId !== null &&
    previewLoad?.projectId === projectId &&
    previewLoad.taskRunId === previewTaskRunId
      ? previewLoad
      : null;
  const currentContextLoad =
    projectId !== null && contextLoad?.projectId === projectId
      ? contextLoad
      : null;
  const contextRevisionMismatch = Boolean(
    currentContextLoad?.context &&
      draft &&
      (currentContextLoad.context.draft_id !== draft.draft_id ||
        currentContextLoad.context.draft_revision !== draft.revision),
  );
  const realContextState: WorkbenchContextState = {
    data: contextRevisionMismatch ? null : currentContextLoad?.context ?? null,
    error: contextRevisionMismatch
      ? "当前工作稿已更新，请重新读取来源与审计事实。"
      : currentContextLoad?.error ?? null,
    loading: currentContextLoad === null,
  };

  function refreshContext() {
    if (previewMode) return;
    setContextLoad(null);
    setReloadContext((value) => value + 1);
  }

  const {
    reloadSpatialLocation,
    saveObject,
    saveSpatialPosition,
    savingObject,
    transitionConclusion,
  } = useWorkbenchObjectPersistence({
    draft,
    projectId,
    onDraftLoaded(latest) {
      setDraftLoad({ projectId: latest.project_id, draft: latest, error: null });
    },
    onRefreshContext: refreshContext,
  });

  if (fixtureMode) {
    return (
      <AnalystWorkbenchSurface
        activeCandidate={activeCandidate}
        activeCandidateStatus={activeCandidateStatus}
        adoptCandidate={adoptCandidate}
        key={fixtureSeed.id}
        projectId={null}
        seed={fixtureSeed}
      />
    );
  }

  if (invalidProjectId) {
    return <WorkbenchGate title="项目标识不合法" detail="请从候选页重新进入分析师工作台。" />;
  }
  if (projectId === null) {
    return <WorkbenchGate title="工作台需要项目 ID" detail="采用一份候选后，系统会带着项目标识进入这里。" />;
  }
  if (invalidPreviewTaskRunId) {
    return (
      <WorkbenchGate
        title="候选预览标识不合法"
        detail="请从候选页重新打开这份只读预览。"
      />
    );
  }
  if (previewMode) {
    if (currentPreviewLoad === null) {
      return (
        <WorkbenchGate
          title="正在读取候选预览"
          detail={`项目 ${projectId} · 候选任务 #${previewTaskRunId}`}
          loading
        />
      );
    }
    if (currentPreviewLoad.error) {
      return (
        <WorkbenchGate
          detail={currentPreviewLoad.error}
          onRetry={() => {
            setPreviewLoad(null);
            setReloadPreview((value) => value + 1);
          }}
          title="候选预览加载失败"
        />
      );
    }
    if (!currentPreviewLoad.preview) {
      return (
        <WorkbenchGate
          title="候选预览不可用"
          detail="这份候选尚未成功生成，或已无法恢复。"
        />
      );
    }
    const preview = currentPreviewLoad.preview;
    const previewSeed = mapCaseFileToWorkbenchModel(preview.content, 0);
    const seed = {
      ...previewSeed,
      caseMeta: {
        ...previewSeed.caseMeta,
        subtitle: `CaseFile ${preview.content.schema_version} · 候选预览`,
        revision: `候选 #${preview.task_run_id}`,
        branchLabel: "候选预览",
      },
    };
    return (
      <AnalystWorkbenchSurface
        activeCandidate={null}
        activeCandidateStatus={null}
        adoptCandidate={adoptCandidate}
        key={`preview-${projectId}-${preview.task_run_id}`}
        previewCandidate={preview}
        previewProjectId={projectId}
        projectId={projectId}
        readOnlyPreview
        realDocument={preview.content}
        seed={seed}
      />
    );
  }
  if (currentDraftLoad === null) {
    return (
      <WorkbenchGate
        detail={`项目 ${projectId} · 正在连接卷宗数据`}
        loading
        projectId={projectId}
        projectTitle={`项目 ${projectId}`}
        title="正在读取当前工作稿"
      />
    );
  }
  if (draftError) {
    return (
      <WorkbenchGate
        detail={draftError}
        onRetry={() => {
          setDraftLoad(null);
          setReloadDraft((value) => value + 1);
        }}
        projectId={projectId}
        projectTitle={`项目 ${projectId}`}
        title="当前工作稿加载失败"
      />
    );
  }
  if (!draft?.content) {
    return (
      <WorkbenchGate
        actionHref={`/?project=${projectId}`}
        actionLabel="返回建案中心生成工作稿"
        detail="请先选择创作策略并采用一份候选；已有项目与 Brief 会继续保留。"
        projectId={projectId}
        projectTitle={draft?.title ?? `项目 ${projectId}`}
        title="这个项目还没有已生成的工作稿"
      />
    );
  }

  const loadedProjectId = projectId;
  const loadedDraft = draft;
  const loadedDocument = draft.content;
  const seed = mapCaseFileToWorkbenchModel(
    loadedDocument,
    loadedDraft.revision,
    realContextState.data?.validation ?? null,
  );

  async function previewEventTime(
    eventId: string,
    proposedTime: TimelineTemporalPosition,
  ): Promise<TimelineTimePreviewView> {
    try {
      return await previewCaseDraftEventTime(
        loadedProjectId,
        eventId,
        loadedDraft.draft_id,
        loadedDraft.revision,
        proposedTime,
      );
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.status === 409 || caught.body.code === "draft_revision_conflict")
      ) {
        const latest = await fetchCaseDraft(loadedProjectId);
        setDraftLoad({ projectId: loadedProjectId, draft: latest, error: null });
        refreshContext();
        throw new Error("当前工作稿已更新，请基于最新时间轴重新预览。");
      }
      throw caught;
    }
  }

  async function handleDraftActivated(activated: DraftView) {
    setDraftLoad({ projectId: loadedProjectId, draft: activated, error: null });
    setContextLoad(null);
    setReloadContext((value) => value + 1);
    await loadProject(loadedProjectId).catch(() => undefined);
  }

  async function handleCurrentDraftChanged() {
    await handleDraftActivated(await fetchCaseDraft(loadedProjectId));
  }

  return (
    <AnalystWorkbenchSurface
      activeCandidate={activeCandidate}
      activeCandidateStatus={activeCandidateStatus}
      adoptCandidate={adoptCandidate}
      currentDraft={draft}
      draftRevision={draft.revision}
      key={`project-${projectId}-draft-${draft.draft_id}`}
      onCurrentDraftChanged={handleCurrentDraftChanged}
      onDraftActivated={handleDraftActivated}
      onPreviewEventTime={previewEventTime}
      onReloadSpatialLocation={reloadSpatialLocation}
      onSaveObject={saveObject}
      onSaveSpatialPosition={saveSpatialPosition}
      onTransitionConclusion={transitionConclusion}
      projectId={projectId}
      realDocument={loadedDocument}
      realContextState={realContextState}
      onReloadContext={refreshContext}
      savingObject={savingObject}
      seed={seed}
    />
  );
}

function WorkbenchGate({
  title,
  detail,
  loading = false,
  onRetry,
  actionHref = "/",
  actionLabel = "返回建案中心",
  projectId = null,
  projectTitle = "载入项目",
}: {
  title: string;
  detail: string;
  loading?: boolean;
  onRetry?: () => void;
  actionHref?: string;
  actionLabel?: string;
  projectId?: number | null;
  projectTitle?: string;
}) {
  const gate = (
    <main className={gateStyles.gate} data-loading={loading}>
      <article aria-busy={loading} className={gateStyles.panel}>
        {loading ? (
          <div aria-hidden="true" className={gateStyles.loadingVisual}>
            <div className={gateStyles.orbit}>
              <i /><i /><i /><i />
            </div>
            <div className={gateStyles.folioMark}>
              <i className={gateStyles.folioShadow} />
              <i className={gateStyles.folioSheet} />
              <div className={gateStyles.folioFace}>
                <span /><span /><span />
                <b>卷</b>
              </div>
              <i className={gateStyles.scanLine} />
            </div>
          </div>
        ) : null}

        <div className={gateStyles.gateCopy}>
          <div className={gateStyles.kicker}>
            <span>
              {loading
                ? (projectId === null ? "分析工作台" : `项目 ${projectId}`)
                : "当前工作稿"}
            </span>
            {!loading ? (
              <small>{projectId === null ? "CaseFile" : `项目 ${projectId}`}</small>
            ) : null}
          </div>
          <h1>{title}</h1>
          {!loading ? <p>{detail}</p> : null}
          {loading ? (
            <div
              aria-label="正在连接项目、装载工作稿并准备分析上下文"
              className={gateStyles.loadingStatus}
              role="status"
            >
              <div><i /></div>
              <span aria-hidden="true">正在进入分析工作台</span>
            </div>
          ) : null}
          {onRetry || !loading ? (
            <div className={gateStyles.gateActions}>
              {onRetry ? <button onClick={onRetry} type="button">重新读取</button> : null}
              {!loading ? <Link href={actionHref}>{actionLabel}</Link> : null}
            </div>
          ) : null}
        </div>
      </article>
    </main>
  );
  if (projectId === null || loading) return gate;

  return (
    <div className={`${styles.workbench} ${gateStyles.gatedWorkbench}`}>
      <header className={`${styles.topbar} ${gateStyles.gatedHeader}`}>
        <div className={styles.brandBlock}>
          <span className={styles.brandMark} aria-hidden="true" />
        </div>
        <ProjectSwitcher
          currentProjectId={projectId}
          fallbackTitle={projectTitle}
        />
        <div aria-hidden="true" className={gateStyles.handoffRoute}>
          <span>建案中心</span>
          <i />
          <strong>分析工作台</strong>
        </div>
        <div aria-hidden="true" className={gateStyles.handoffPulse}>
          <i />
          <span>{loading ? "正在接管" : "工作区"}</span>
        </div>
        <div className={styles.topActions}>
          <Link href={`/?project=${projectId}`}>返回建案中心</Link>
        </div>
      </header>
      {gate}
    </div>
  );
}

function AnalystWorkbenchSurface({
  seed,
  activeCandidate,
  activeCandidateStatus,
  adoptCandidate,
  projectId = null,
  previewCandidate = null,
  previewProjectId = null,
  readOnlyPreview = false,
  realDocument = null,
  realContextState,
  currentDraft = null,
  draftRevision = null,
  savingObject = false,
  onReloadContext,
  onCurrentDraftChanged,
  onDraftActivated,
  onPreviewEventTime,
  onReloadSpatialLocation,
  onSaveObject,
  onSaveSpatialPosition,
  onTransitionConclusion,
}: {
  seed: WorkbenchModel;
  activeCandidate: WorkbenchCandidate | null;
  activeCandidateStatus: WorkbenchCandidateStatus | null;
  adoptCandidate: (candidateId: string) => Promise<number | false>;
  projectId?: number | null;
  previewCandidate?: DraftCandidatePreviewView | null;
  previewProjectId?: number | null;
  readOnlyPreview?: boolean;
  realDocument?: CaseFileDocument | null;
  realContextState?: WorkbenchContextState;
  currentDraft?: DraftView | null;
  draftRevision?: number | null;
  savingObject?: boolean;
  onReloadContext?: () => void;
  onCurrentDraftChanged?: () => Promise<void>;
  onDraftActivated?: (draft: DraftView) => Promise<void> | void;
  onPreviewEventTime?: (
    eventId: string,
    proposedTime: TimelineTemporalPosition,
  ) => Promise<TimelineTimePreviewView>;
  onReloadSpatialLocation?: (
    locationId: string,
  ) => Promise<ReloadedSpatialLocation>;
  onSaveObject?: (
    objectId: string,
    changes: Record<string, unknown>,
  ) => Promise<ObjectSaveResult>;
  onTransitionConclusion?: (
    resolutionId: string,
    action: "confirm" | "withdraw",
  ) => Promise<ObjectSaveResult>;
  onSaveSpatialPosition?: (
    locationId: string,
    position: SpatialPositionPayload,
  ) => Promise<SpatialPositionSaveResult>;
}) {
  const realData = realDocument !== null;
  const writeLocked = readOnlyPreview;
  const canvasLayoutScope =
    projectId === null
      ? `fixture:${seed.id}:current`
      : readOnlyPreview && previewCandidate
        ? `project:${projectId}:candidate:${previewCandidate.task_run_id}`
        : `project:${projectId}:draft:${currentDraft?.draft_id ?? "current"}`;
  const contextState = realContextState ?? {
    data: null,
    error: null,
    loading: false,
  };
  const [view, setView] = useState<WorkbenchView>("timeline");
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>(
    realData ? "workbench" : "analysis",
  );
  const [navigatorOpen, setNavigatorOpen] = useState(true);
  const [evidenceTab, setEvidenceTab] = useState<"matrix" | "issues">("matrix");
  const [selectedEventId, setSelectedEventId] = useState(seed.defaultEventId);
  const [selectedObjectId, setSelectedObjectId] = useState(seed.defaultObjectId);
  const [objectHistory, setObjectHistory] = useState(() =>
    createObjectNavigationHistory({
      objectId: seed.defaultObjectId,
      view: "timeline",
    }),
  );
  const [selectedIssueId, setSelectedIssueId] = useState(seed.defaultIssueId);
  const [issueStatuses, setIssueStatuses] = useState<Record<string, IssueStatus>>(
    () => createIssueStatuses(seed),
  );
  const [kindFilter, setKindFilter] = useState<DirectoryObjectKind | "all">(
    "all",
  );
  const [subtypeFilter, setSubtypeFilter] = useState<string | "all">("all");
  const [objectQuery, setObjectQuery] = useState("");
  const [objectEditorDirty, setObjectEditorDirty] = useState(false);
  const [spatialEditActive, setSpatialEditActive] = useState(false);
  const [spatialEditDirty, setSpatialEditDirty] = useState(false);
  const [objectEditorNavigationNotice, setObjectEditorNavigationNotice] =
    useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [mobileRegion, setMobileRegion] = useState<MobileRegion>("canvas");
  const [liveMessage, setLiveMessage] = useState(
    `分析师工作台已就绪。当前打开“${seed.caseMeta.title}”。`,
  );
  const [manualEditing, setManualEditing] = useState(false);
  const [manualValue, setManualValue] = useState(
    seed.validationIssues[0]?.patchAfter ?? "",
  );
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([
    ...seed.initialAuditEntries,
  ]);
  const [railWidth, setRailWidth] = useState<number | null>(null);
  const railResizeRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState<number | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(realData);
  const inspectorResizeRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);
  const [agentSurface, setAgentSurface] = useState<AgentSurface>("dock");
  const [agentFocusRequest, setAgentFocusRequest] = useState(0);
  const [agentKickoff, setAgentKickoff] = useState<{
    id: number;
    prompt: string;
    routingHint?: AgentChatRoutingHint;
  } | null>(null);
  const [agentInspectorHost, setAgentInspectorHost] = useState<HTMLElement | null>(null);
  const [agentThreadHost, setAgentThreadHost] = useState<HTMLElement | null>(null);
  const [agentFocusPatchSetId, setAgentFocusPatchSetId] = useState<number | null>(null);
  const [agentFocusFindingId, setAgentFocusFindingId] = useState<string | null>(null);
  const modalRef = useRef<HTMLElement>(null);
  const paletteInputRef = useRef<HTMLInputElement>(null);
  const commandTriggerRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const timersRef = useRef<number[]>([]);


  function startRailResize(event: ReactPointerEvent<HTMLDivElement>) {
    railResizeRef.current = {
      startX: event.clientX,
      startWidth: railWidth ?? DEFAULT_RAIL_WIDTH,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveRailResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = railResizeRef.current;
    if (!resize) return;
    const width = clamp(resize.startWidth + (event.clientX - resize.startX), 196, 320);
    setRailWidth(width);
  }

  function endRailResize() {
    railResizeRef.current = null;
  }

  function startInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    inspectorResizeRef.current = {
      startX: event.clientX,
      startWidth: inspectorWidth ?? DEFAULT_INSPECTOR_WIDTH,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function moveInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    const resize = inspectorResizeRef.current;
    if (!resize) return;
    const width = clamp(
      resize.startWidth + resize.startX - event.clientX,
      300,
      420,
    );
    setInspectorWidth(width);
  }

  function endInspectorResize() {
    inspectorResizeRef.current = null;
  }

  const selectedEvent = getEvent(seed, selectedEventId);
  const selectedObject = getObject(seed, selectedObjectId);
  const workbenchPersonCount = seed.caseFile
    ? seed.caseFile.entities.filter((entity) => entity.entity_type === "person").length
    : seed.caseObjects.filter((object) => object.id.startsWith("PER-")).length;
  const visibleSelectedIssueId = seed.validationIssues.some(
    (issue) => issue.id === selectedIssueId,
  )
    ? selectedIssueId
    : seed.defaultIssueId;
  const visibleIssueStatuses = Object.fromEntries(
    seed.validationIssues.map((issue) => [
      issue.id,
      issueStatuses[issue.id] ?? "open",
    ]),
  ) as Record<string, IssueStatus>;
  const selectedIssue =
    seed.validationIssues.find((item) => item.id === visibleSelectedIssueId) ??
    seed.validationIssues[0];
  const selectedStatus = selectedIssue
    ? visibleIssueStatuses[selectedIssue.id] ?? "open"
    : "open";
  const eventRelatedObjectIds = selectedEvent
    ? [selectedEvent.id, ...selectedEvent.relatedObjectIds]
    : [];
  const selectedRelatedEvents = selectedObject
    ? selectedObject.relatedEventIds.flatMap((eventId) => {
        const event = getEvent(seed, eventId);
        return event ? [event] : [];
      })
    : [];
  const selectedConclusion = seed.conclusions?.find(
    (conclusion) => conclusion.resolutionSpecId === selectedObjectId,
  );
  const conclusionRelatedEventIds = selectedConclusion
    ? selectedConclusion.relatedEventIds
    : [];
  const unresolvedCount = realData
    ? contextState.data?.validation.issue_count ?? 0
    : seed.validationIssues.filter((issue) => {
        const status = visibleIssueStatuses[issue.id];
        return status === "open" || status === "patch-ready";
      }).length;
  const realValidationLabel = contextState.loading
    ? "验证中"
    : writeLocked
      ? "生成门禁已通过"
      : contextState.error
        ? "读取失败"
        : contextState.data?.validation.status === "passed"
          ? "已通过"
          : contextState.data?.validation.status === "failed"
            ? `${unresolvedCount} 个问题`
            : "暂不可用";

  const timelineValidationStatus = !realData || writeLocked
    ? "passed"
    : contextState.loading
      ? "loading"
      : contextState.error
        ? "error"
        : contextState.data?.validation.status ?? "unavailable";

  function schedule(callback: () => void, delay: number) {
    const timer = window.setTimeout(callback, delay);
    timersRef.current.push(timer);
  }

  useEffect(() => () => timersRef.current.forEach((timer) => window.clearTimeout(timer)), []);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if (
        (event.ctrlKey || event.metaKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === "k"
      ) {
        event.preventDefault();
        if (!writeLocked) {
          setAgentSurface("dock");
          setAgentFocusRequest((version) => version + 1);
        }
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [writeLocked]);

  useEffect(() => {
    if (!paletteOpen) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    paletteInputRef.current?.focus();

    function handleDialogKeys(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setPaletteOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        modalRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleDialogKeys);
    return () => {
      document.removeEventListener("keydown", handleDialogKeys);
      previousFocusRef.current?.focus();
    };
  }, [paletteOpen]);

  function announce(message: string) {
    setLiveMessage(message);
  }

  function blockDirtyObjectNavigation(nextObjectId?: string | null) {
    if (!realData || (nextObjectId !== undefined && nextObjectId === selectedObjectId)) {
      return false;
    }
    if (!objectEditorDirty && !spatialEditActive) return false;
    const message = spatialEditActive
      ? spatialEditDirty
        ? "地图中有未保存的位置预览。请先保存或取消，再切换项目、工作稿、对象或视图。"
        : "地图位置编辑仍在进行。请先取消编辑，再切换项目、工作稿、对象或视图。"
      : "有未保存修改。请先保存或取消修改，再切换项目、工作稿或对象。";
    if (objectEditorDirty) {
      setMobileRegion("inspector");
    } else {
      setMobileRegion("canvas");
    }
    setObjectEditorNavigationNotice(message);
    announce(message);
    return true;
  }

  const objectHistoryBackFrame = objectFocusBackTarget(objectHistory);
  const objectHistoryForwardFrame = objectFocusForwardTarget(objectHistory);

  function objectFocusLabel(frame: ObjectFocusFrame | null) {
    if (!frame) return "";
    if (frame.objectId === null) return "空选择";
    return getObject(seed, frame.objectId)?.label ?? frame.objectId;
  }

  function commitObjectFocus(frame: ObjectFocusFrame) {
    setObjectHistory((history) => recordObjectFocus(history, frame));
  }

  function restoreObjectFocus(frame: ObjectFocusFrame): boolean {
    if (frame.objectId === null) {
      if (blockDirtyObjectNavigation(null)) return false;
      setSelectedEventId(null);
      setSelectedObjectId(null);
      setObjectEditorNavigationNotice(null);
    } else {
      const object = getObject(seed, frame.objectId);
      if (!object) return false;
      if (blockDirtyObjectNavigation(object.id)) return false;
      setSelectedObjectId(object.id);
      const conclusionEventId = seed.conclusions?.find(
        (conclusion) => conclusion.resolutionSpecId === object.id,
      )?.relatedEventIds[0];
      const eventId = object.kind === "event"
        ? object.id
        : conclusionEventId ?? object.relatedEventIds[0];
      setSelectedEventId(eventId ?? null);
      setObjectEditorNavigationNotice(null);
    }
    if (frame.view !== view) setView(frame.view);
    setWorkspaceMode(
      frame.view === "compile" || frame.view === "export" ? "compile" : "analysis",
    );
    setAgentSurface("dock");
    return true;
  }

  function navigateObjectHistory(direction: "back" | "forward") {
    const frame = direction === "back"
      ? objectHistoryBackFrame
      : objectHistoryForwardFrame;
    if (!frame) {
      announce(direction === "back"
        ? "已经是最早的对象上下文记录。"
        : "已经是最新的对象上下文记录。");
      return;
    }
    if (!restoreObjectFocus(frame)) return;
    setObjectHistory((history) => direction === "back"
      ? moveObjectHistoryBack(history)
      : moveObjectHistoryForward(history));
    announce(direction === "back"
      ? `已后退到${frame.objectId === null ? "空选择" : `对象“${objectFocusLabel(frame)}”`}。`
      : `已前进到${frame.objectId === null ? "空选择" : `对象“${objectFocusLabel(frame)}”`}。`);
  }

  function updateObjectEditorDirty(dirty: boolean) {
    setObjectEditorDirty(dirty);
    if (!dirty) setObjectEditorNavigationNotice(null);
  }

  function switchWorkspaceMode(nextMode: WorkspaceMode) {
    if (nextMode !== workspaceMode && blockDirtyObjectNavigation()) return;
    setWorkspaceMode(nextMode);
    if (nextMode === "workbench") {
      setInspectorOpen(true);
    }
    if (nextMode === "compile") {
      setInspectorOpen(false);
    }
    if (nextMode === "analysis" || nextMode === "compile") {
      setAgentSurface("dock");
    }
    if (nextMode === "analysis" && (view === "compile" || view === "export")) {
      setView("timeline");
    }
    if (nextMode === "compile" && view !== "compile" && view !== "export") {
      setView("compile");
    }
    setMobileRegion("canvas");
    announce(`已进入${workspaceModeCopy[nextMode].label}。`);
  }

  function switchWorkbenchView(nextView: WorkbenchView, label: string) {
    if (nextView !== view && blockDirtyObjectNavigation()) return;
    setView(nextView);
    if (nextView === "compile" || nextView === "export") {
      setInspectorOpen(false);
    }
    setWorkspaceMode(
      nextView === "compile" || nextView === "export" ? "compile" : "analysis",
    );
    setAgentSurface("dock");
    setMobileRegion("canvas");
    announce(`主画布已切换到${label}。`);
  }

  function appendAudit(actor: string, action: string, detail: string) {
    setAuditEntries((entries) => [
      { id: `AUD-${Date.now()}`, time: currentClock(), actor, action, detail },
      ...entries,
    ]);
  }

  function selectEvent(
    eventId: string,
    options: { preserveView?: boolean } = {},
  ): boolean {
    const event = getEvent(seed, eventId);
    if (!event || blockDirtyObjectNavigation(event.id)) return false;
    setSelectedEventId(event.id);
    setSelectedObjectId(event.id);
    setObjectEditorNavigationNotice(null);
    const issueId = event.issueIds[0];
    if (issueId) setSelectedIssueId(issueId);
    setObjectQuery("");
    setKindFilter("event");
    setSubtypeFilter("all");
    if (!options.preserveView) setView("timeline");
    setWorkspaceMode("analysis");
    if (!options.preserveView) setAgentSurface("dock");
    setInspectorOpen(true);
    setMobileRegion("canvas");
    commitObjectFocus({
      objectId: event.id,
      view: options.preserveView ? view : "timeline",
    });
    announce(
      options.preserveView
        ? `已在空间卷宗中选择事件“${event.label}”。`
        : `已选择事件“${event.label}”，关系图和对象上下文已同步定位。`,
    );
    return true;
  }

  function selectObject(
    objectId: string,
    revealInDirectory = false,
    preserveCanvas = false,
  ): boolean {
    const object = getObject(seed, objectId);
    if (!object || blockDirtyObjectNavigation(object.id)) return false;
    setSelectedObjectId(object.id);
    const conclusionEventId = seed.conclusions?.find(
      (conclusion) => conclusion.resolutionSpecId === object.id,
    )?.relatedEventIds[0];
    const eventId = object.kind === "event"
      ? object.id
      : conclusionEventId ?? object.relatedEventIds[0];
    setSelectedEventId(eventId ?? null);
    setObjectEditorNavigationNotice(null);
    setKindFilter(directoryObjectKind(object.kind));
    setSubtypeFilter("all");
    if (revealInDirectory) {
      setObjectQuery("");
    }
    if (realData) {
      if (!preserveCanvas) setMobileRegion("inspector");
    }
    setInspectorOpen(true);
    commitObjectFocus({ objectId: object.id, view });
    announce(`已选择${objectKindLabels[object.kind]}“${object.label}”，相关事件已高亮。`);
    return true;
  }

  function openObjectInConversation(objectId: string, revealInDirectory = false) {
    if (!selectObject(objectId, revealInDirectory, true)) return false;
    setWorkspaceMode("dossier");
    setAgentSurface("desk");
    setInspectorOpen(true);
    announce("已将当前对象加入对话上下文。");
    return true;
  }

  function clearMapSelection(): boolean {
    if (blockDirtyObjectNavigation(null)) return false;
    setSelectedEventId(null);
    setSelectedObjectId(null);
    setSelectedIssueId(null);
    setObjectEditorNavigationNotice(null);
    commitObjectFocus({ objectId: null, view });
    announce("已清除空间卷宗选择。");
    return true;
  }

  function openIssue(issueId: string) {
    const issue = seed.validationIssues.find((item) => item.id === issueId);
    if (!issue || (issue.eventId && blockDirtyObjectNavigation(issue.eventId))) return;
    setSelectedIssueId(issue.id);
    if (issue.eventId) setSelectedEventId(issue.eventId);
    const focusObjectId = issue.targetObjectId ?? issue.eventId ?? null;
    setSelectedObjectId(focusObjectId);
    setObjectQuery("");
    setKindFilter("event");
    setSubtypeFilter("all");
    setView("evidence");
    setWorkspaceMode("analysis");
    setAgentSurface("dock");
    setEvidenceTab("issues");
    setMobileRegion("canvas");
    setManualEditing(false);
    setManualValue(issue.patchAfter);
    commitObjectFocus({ objectId: focusObjectId, view: "evidence" });
    announce(`已打开${issue.severity}问题“${issue.title}”，主画布切换到证据与知识状态对照。`);
  }

  function sendIssueToAgent(issueId: string) {
    const issue = seed.validationIssues.find((item) => item.id === issueId);
    if (!issue) return;
    openIssue(issue.id);
    setAgentKickoff({
      id: Date.now(),
      prompt:
        `请处理当前焦点中的验证问题“${issue.title}”：先解释规则失败原因，` +
        "再针对该问题绑定的对象给出可逐项审阅的字段修改建议。",
      routingHint: { entrypoint: "issue_action" },
    });
    setAgentSurface("desk");
    announce(`已把验证问题“${issue.title}”交给 Agent 处理。`);
  }

  async function rerunCurrentVerification() {
    if (!realData || projectId === null || currentDraft === null) return;
    try {
      await rerunVerification(
        LOCAL_ACTOR_ID,
        projectId,
        currentDraft.draft_id,
        currentDraft.revision,
      );
      setAgentKickoff(null);
      setAgentSurface("desk");
      setAgentFocusRequest((version) => version + 1);
      announce("验证复查已进入队列；Agent 面板将显示真实进度和结果。");
    } catch (caught) {
      announce(errorMessage(caught));
    }
  }

  function requestPatch() {
    if (!selectedIssue) return;
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: "patch-ready" }));
    setView("evidence");
    appendAudit("Agent", "生成建议补丁", `${selectedIssue.id} · 等待人工批准`);
    announce("Agent 补丁已生成，仅作为建议展示，等待人工批准。");
  }

  function rejectPatch() {
    if (!selectedIssue) return;
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: "open" }));
    appendAudit(seed.caseMeta.protagonist, "拒绝 Agent 补丁", selectedIssue.id);
    announce("补丁已拒绝，验证问题保持待处理。");
  }

  function resolveIssue(action: "approve" | "manual" | "exception") {
    if (!selectedIssue) return;
    const nextStatus: IssueStatus = action === "exception" ? "exception" : "resolved";
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: nextStatus }));
    setManualEditing(false);
    const actionLabel = action === "approve" ? "批准 Agent 补丁" : action === "manual" ? "保存人工修正" : "标记已知例外";
    appendAudit(seed.caseMeta.protagonist, actionLabel, `${selectedIssue.id} · 局部重算`);
    announce(`${actionLabel}已记录，正在执行局部重算。`);
    schedule(() => {
      setLiveMessage(`${actionLabel}已完成。当前仍有 ${Math.max(0, unresolvedCount - 1)} 个待处理问题。`);
    }, 760);
  }

  function resetWorkbench() {
    if (blockDirtyObjectNavigation()) return;
    if (writeLocked) {
      announce("候选预览为只读，不执行重置。");
      return;
    }
    setView("timeline");
    setEvidenceTab("matrix");
    setSelectedEventId(seed.defaultEventId);
    setSelectedObjectId(seed.defaultObjectId);
    setSelectedIssueId(seed.defaultIssueId);
    setIssueStatuses(createIssueStatuses(seed));
    setKindFilter("all");
    setSubtypeFilter("all");
    setObjectQuery("");
    setManualEditing(false);
    setManualValue(seed.validationIssues[0]?.patchAfter ?? "");
    setAuditEntries([...seed.initialAuditEntries]);
    setAgentSurface("dock");
    commitObjectFocus({ objectId: seed.defaultObjectId, view: "timeline" });
    announce(`工作台数据已重置，已返回“${seed.caseMeta.title}”默认问题。`);
  }

  function runPaletteAction(action: () => void) {
    action();
    setPaletteOpen(false);
    setPaletteQuery("");
  }

  const paletteEntries = [
    {
      id: "view-timeline",
      label: "打开事件时间线",
      meta: "视图",
      action: () => switchWorkbenchView("timeline", "事件时间线"),
    },
    {
      id: "view-relations",
      label: "打开人物与证据关系图",
      meta: "视图",
      action: () => switchWorkbenchView("relations", "关系图"),
    },
    ...(realData ? [] : [{
      id: "open-issue",
      label: "定位最高优先级验证问题",
      meta: "S0",
      action: () => {
        if (seed.defaultIssueId) openIssue(seed.defaultIssueId);
      },
    }]),
  ];
  const normalizedPaletteQuery = paletteQuery.trim().toLocaleLowerCase("zh-CN");
  const matchingPaletteEntries = paletteEntries.filter((item) => !normalizedPaletteQuery || `${item.label} ${item.meta}`.toLocaleLowerCase("zh-CN").includes(normalizedPaletteQuery));
  const matchingPaletteObjects = seed.caseObjects.filter((object) => !normalizedPaletteQuery || `${object.label} ${object.code} ${object.id}`.toLocaleLowerCase("zh-CN").includes(normalizedPaletteQuery)).slice(0, 6);

  function handleTimelineKeys(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (view !== "timeline" || !seed.timelineEvents.length) return;
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const currentIndex = seed.timelineEvents.findIndex((item) => item.id === selectedEventId);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const nextIndex = Math.min(seed.timelineEvents.length - 1, Math.max(0, currentIndex + delta));
    const nextEvent = seed.timelineEvents[nextIndex];
    if (nextEvent) selectEvent(nextEvent.id);
  }

  function closeAgent() {
    setAgentSurface("dock");
    if (workspaceMode === "dossier") setWorkspaceMode("workbench");
    setAgentKickoff(null);
    setAgentFocusRequest((version) => version + 1);
  }

  function planNextStepsWithAgent() {
    setWorkspaceMode("workbench");
    setAgentKickoff({
      id: Date.now(),
      prompt:
        "请基于当前工作稿梳理下一步创作计划：指出最值得推进的情节、仍需补足的关键线索，并按优先级给出具体行动建议。",
      routingHint: { entrypoint: "free_text" },
    });
    setAgentSurface("desk");
    announce("已请 Agent 基于当前工作稿规划下一步。");
  }

  const agentLiveProps =
    realData && projectId !== null && currentDraft
      ? {
          draftId: currentDraft.draft_id,
          draftRevision: draftRevision ?? currentDraft.revision,
          disabled: writeLocked,
          focus: {
            object_ids: selectedObjectId ? [selectedObjectId] : [],
            event_ids: selectedEventId ? [selectedEventId] : [],
            validation_issue_ids: selectedIssueId ? [selectedIssueId] : [],
            view,
          },
          focusRequest: agentFocusRequest,
          kickoff: agentKickoff,
          onClose: closeAgent,
          onContinueInDesk: () => setAgentSurface("desk"),
          onLocateEvent: (eventId: string) => selectEvent(eventId, { preserveView: true }),
          onLocateObject: (objectId: string) => openObjectInConversation(objectId, true),
          onFocusPatch: (patchSetId: number) => {
            setAgentFocusPatchSetId(patchSetId);
            setAgentFocusFindingId(null);
            setMobileRegion("inspector");
            setInspectorOpen(true);
            announce("已将修改建议聚焦到对象上下文。");
          },
          onFocusFinding: (findingId: string) => {
            setAgentFocusFindingId(findingId);
            setAgentFocusPatchSetId(null);
            setMobileRegion("inspector");
            setInspectorOpen(true);
            announce("已将验证发现聚焦到对象上下文。");
          },
          focusPatchSetId: agentFocusPatchSetId,
          focusFindingId: agentFocusFindingId,
          inspectorHost: agentInspectorHost,
          threadHost: agentThreadHost,
          onDraftChanged: onCurrentDraftChanged ?? (async () => {}),
          projectId,
          referenceLabels: {
            objects: Object.fromEntries(seed.caseObjects.map((object) => [object.id, object.label])),
            events: Object.fromEntries(seed.timelineEvents.map((event) => [event.id, event.label])),
            issues: Object.fromEntries(seed.validationIssues.map((issue) => [issue.id, issue.title])),
          },
        }
      : null;

  const agentSurfaceContent = (
    <WorkbenchAgentSurface surface={agentSurface}>
      {agentLiveProps ? (
        <AgentLivePanel {...agentLiveProps} surface={agentSurface} />
      ) : (
        <AgentPanel
          contextChips={[
            selectedObject?.label,
            selectedEvent?.label,
            selectedIssue?.title,
            workbenchViewOptions.find((option) => option.id === view)?.label,
          ].filter((value): value is string => Boolean(value))}
          disabled={writeLocked}
          focusRequest={agentFocusRequest}
          onClose={closeAgent}
          onContinueInDesk={() => setAgentSurface("desk")}
          seed={seed}
          surface={agentSurface}
          unresolvedCount={unresolvedCount}
        />
      )}
    </WorkbenchAgentSurface>
  );

  return (
    <div
      className={styles.workbench}
      data-mobile-region={mobileRegion}
      data-read-only-preview={writeLocked}
      data-workbench-seed={seed.id}
    >
      <a className={styles.skipLink} href="#analyst-canvas">跳到主画布</a>
      <header className={styles.topbar}>
        <div className={styles.brandBlock}>
          <span className={styles.brandMark} aria-hidden="true" />
        </div>
        {projectId === null ? (
          <div className={styles.caseIdentity}>
            <span>本地项目预览</span>
            <div className={styles.caseIdentityTitleRow}>
              <strong>{seed.caseMeta.title}</strong>
              {activeCandidate ? (
                <>
                  <Link className={styles.candidateBackLink} href="/">← 返回候选卷</Link>
                  {!realData && activeCandidateStatus === "pending" ? (
                    <button
                      className={styles.candidateAdoptButton}
                      onClick={() => {
                        void adoptCandidate(activeCandidate.id)
                          .then((draftId) => {
                            if (draftId) announce("该候选已采用为当前工作稿。");
                          })
                          .catch((caught) => {
                            announce(
                              caught instanceof Error
                                ? caught.message
                                : "采用未完成，请稍后重试。",
                            );
                          });
                      }}
                      type="button"
                    >
                      采用为当前工作稿
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>
            <small>{seed.caseMeta.revision}</small>
          </div>
        ) : (
          <ProjectSwitcher
            currentProjectId={projectId}
            fallbackTitle={seed.caseMeta.title}
            onBeforeSwitch={() => !blockDirtyObjectNavigation()}
          />
        )}
        <nav
          aria-label="主要工作模式"
          className={styles.workspaceModes}
          role="tablist"
        >
          <button
            aria-selected={workspaceMode === "workbench" || workspaceMode === "dossier"}
            className={styles.mergedWorkbenchMode}
            onClick={() => switchWorkspaceMode("workbench")}
            role="tab"
            type="button"
          >
            <WorkbenchIcon name="archive" />
            <span>
              <strong>工作台</strong>
              <small>当前工作 · 对象档案</small>
            </span>
          </button>
          <button
            aria-selected={workspaceMode === "analysis"}
            className={styles.iconWorkspaceMode}
            onClick={() => switchWorkspaceMode("analysis")}
            role="tab"
            type="button"
          >
            <WorkbenchIcon name="command" />
            <span>
              <strong>{workspaceModeCopy.analysis.label}</strong>
              <small>{workspaceModeCopy.analysis.eyebrow}</small>
            </span>
          </button>
          <button
            aria-selected={workspaceMode === "compile"}
            className={styles.compileMode}
            disabled={writeLocked}
            onClick={() => switchWorkspaceMode("compile")}
            role="tab"
            type="button"
          >
            <strong>编译作品</strong>
            <WorkbenchIcon name="export" />
          </button>
        </nav>
        <div className={styles.topStatus} aria-label="卷宗状态">
          <button
            data-tone={writeLocked ? "success" : realData ? contextState.error || unresolvedCount > 0 ? "danger" : contextState.data?.validation.status === "passed" ? "success" : "muted" : unresolvedCount > 0 ? "danger" : "success"}
            disabled={realData || writeLocked}
            onClick={() => {
              if (realData || writeLocked) return;
              if (seed.defaultIssueId) openIssue(seed.defaultIssueId);
            }}
            title={realData ? "确定性验证详情将在质量中心提供" : writeLocked ? "候选预览只读" : "打开验证问题对照"}
            type="button"
          >
            <WorkbenchIcon name="validate" />
            <span><small>验证</small><strong>{realData ? realValidationLabel : unresolvedCount > 0 ? `${unresolvedCount} 个问题` : "已通过"}</strong></span>
          </button>
        </div>
        <button className={styles.globalSearch} onClick={() => setPaletteOpen(true)} ref={commandTriggerRef} type="button">
          <WorkbenchIcon name="search" />
          <span>搜索对象或命令</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className={styles.topActions}>
          <button aria-label={writeLocked ? "候选预览不可重置" : "重置工作台数据"} disabled={writeLocked} onClick={resetWorkbench} type="button"><WorkbenchIcon name="reset" /></button>
          <button
            aria-label="打开模型服务设置"
            onClick={() => window.dispatchEvent(new Event("casefile:open-settings"))}
            title="模型服务设置"
            type="button"
          >
            <WorkbenchIcon name="settings" />
          </button>
          <Link href="/">返回建案中心</Link>
        </div>
      </header>

      {writeLocked && previewCandidate ? (
        <section
          aria-label="候选预览只读提示"
          className={styles.previewBanner}
          role="status"
        >
          <div>
            <span>READ-ONLY CANDIDATE</span>
            <strong>候选预览，不是当前工作稿</strong>
            <p>
              {previewCandidate.candidate_strategy_label} · Brief V
              {previewCandidate.brief_version_no} · 任务 #{previewCandidate.task_run_id}
              。预览不会采用候选，也不会读取或修改当前工作稿。
            </p>
          </div>
          <div>
            <small>
              编辑、重置、重新验证、Agent、补丁、编译与导出均已锁定
            </small>
            {previewProjectId ? (
              <Link href={`/workbench?project=${previewProjectId}`}>
                查看当前工作稿
              </Link>
            ) : null}
            <Link href="/">返回候选卷</Link>
          </div>
        </section>
      ) : null}

      <nav aria-label="移动端工作台区域" className={styles.mobileRegionNav}>
        {mobileRegions.map((region) => (
          <button aria-pressed={mobileRegion === region.id} key={region.id} onClick={() => setMobileRegion(region.id)} type="button">{region.label}</button>
        ))}
      </nav>

      <div
        className={styles.workspaceBody}
        data-inspector-open={inspectorOpen}
        data-navigator-open={navigatorOpen}
        style={
          {
            "--rail-width": navigatorOpen
              ? `${railWidth ?? DEFAULT_RAIL_WIDTH}px`
              : "0px",
            "--inspector-width": inspectorOpen
              ? `${inspectorWidth ?? DEFAULT_INSPECTOR_WIDTH}px`
              : "0px",
          } as CSSProperties
        }
      >
        <div
          aria-hidden="true"
          className={styles.railResizeHandle}
          data-testid="rail-resize-handle"
          onPointerCancel={endRailResize}
          onPointerDown={startRailResize}
          onPointerMove={moveRailResize}
          onPointerUp={endRailResize}
        />
        <div
          aria-hidden="true"
          className={styles.inspectorResizeHandle}
          data-testid="inspector-resize-handle"
          onPointerCancel={endInspectorResize}
          onPointerDown={startInspectorResize}
          onPointerMove={moveInspectorResize}
          onPointerUp={endInspectorResize}
        />
        <aside aria-label="当前模式导航" className={styles.objectRail}>
          <header className={styles.modeRailHeader}>
            <div>
              <span>
                {workspaceMode === "workbench" || workspaceMode === "dossier"
                  ? "工作台"
                  : workspaceModeCopy[workspaceMode].eyebrow}
              </span>
              <strong>
                {workspaceMode === "workbench" || workspaceMode === "dossier"
                  ? "对象档案"
                  : workspaceModeCopy[workspaceMode].label}
              </strong>
            </div>
            <button
              aria-label="收起当前模式导航"
              onClick={() => setNavigatorOpen(false)}
              type="button"
            >
              <WorkbenchIcon name="chevron" />
            </button>
          </header>

          {workspaceMode === "workbench" || workspaceMode === "dossier" ? (
            <WorkbenchObjectDirectory
              key={selectedObjectId ?? "none"}
              kindFilter={kindFilter}
              kinds={realData ? productionObjectKinds : fixtureObjectKinds}
              objects={seed.caseObjects}
              onKindFilterChange={setKindFilter}
              onQueryChange={setObjectQuery}
              onSelectObject={(objectId) => {
                openObjectInConversation(objectId);
              }}
              onSubtypeFilterChange={setSubtypeFilter}
              query={objectQuery}
              relatedObjectIds={eventRelatedObjectIds}
              selectedObjectId={selectedObjectId}
              subtypeFilter={subtypeFilter}
            />
          ) : null}

          {workspaceMode === "analysis" ? (
            <nav
              aria-label="分析工具"
              className={styles.modeNavigation}
              role="tablist"
            >
              {analysisViewOptions.map((option) => (
                <button
                  aria-selected={view === option.id}
                  data-nested={option.id === "evidence"}
                  key={option.id}
                  onClick={() => switchWorkbenchView(option.id, option.label)}
                  role="tab"
                  type="button"
                >
                  <span>{option.shortLabel}</span>
                  <strong>{option.label}</strong>
                  <small>
                    {option.id === "timeline"
                      ? `${seed.timelineEvents.length} 个事件`
                      : option.id === "relations"
                        ? `${seed.caseObjects.length} 个对象`
                        : option.id === "reasoning"
                          ? `${seed.reasoningPaths.length} 条路径`
                          : option.id === "evidence"
                            ? `${unresolvedCount} 个待处理问题`
                            : `${seed.mapMarkers.length} 个位置`}
                  </small>
                </button>
              ))}
            </nav>
          ) : null}

          {workspaceMode === "compile" ? (
            <nav
              aria-label="编译工具"
              className={styles.modeNavigation}
              role="tablist"
            >
              {compileViewOptions.map((option) => (
                <button
                  aria-selected={view === option.id}
                  disabled={writeLocked}
                  key={option.id}
                  onClick={() => switchWorkbenchView(option.id, option.label)}
                  role="tab"
                  type="button"
                >
                  <span>{option.shortLabel}</span>
                  <strong>{option.label}</strong>
                  <small>{option.id === "compile" ? "结构与产物" : "发布前检查"}</small>
                </button>
              ))}
            </nav>
          ) : null}

        </aside>

        <main
          className={styles.canvas}
          data-agent-surface={agentSurface}
          data-draft-revision={seed.caseMeta.revision}
          data-selected-object-id={selectedObjectId ?? ""}
          data-workbench-view={view}
          id="analyst-canvas"
          onKeyDown={handleTimelineKeys}
          tabIndex={-1}
        >
          <header className={styles.canvasToolbar}>
            <div className={styles.canvasHeading}>
              <button
                aria-expanded={navigatorOpen}
                aria-label={navigatorOpen ? "收起当前模式导航" : "展开当前模式导航"}
                className={styles.canvasPanelToggle}
                onClick={() => setNavigatorOpen((open) => !open)}
                type="button"
              >
                <WorkbenchIcon name="command" />
              </button>
              <div>
                <span>{workspaceModeCopy[workspaceMode].eyebrow}</span>
                <strong>
                  {workspaceMode === "analysis" || workspaceMode === "compile"
                    ? workbenchViewOptions.find((option) => option.id === view)?.label
                    : workspaceModeCopy[workspaceMode].label}
                </strong>
              </div>
            </div>
            <div className={styles.canvasToolbarActions}>
              {realData ? (
                <div
                  aria-label="对话线程入口"
                  className={styles.agentThreadHost}
                  ref={setAgentThreadHost}
                  role="region"
                />
              ) : null}
              {projectId !== null && currentDraft && onDraftActivated && !writeLocked ? (
                <DraftSwitcher
                  currentDraft={currentDraft}
                  onActivated={onDraftActivated}
                  onBeforeSwitch={() => !blockDirtyObjectNavigation()}
                  onCurrentDraftChanged={onCurrentDraftChanged}
                  projectId={projectId}
                />
              ) : null}
              {agentSurface === "desk" ? (
                <button
                  aria-label="收起 Agent 对话"
                  className={styles.canvasPanelToggle}
                  onClick={closeAgent}
                  type="button"
                >
                  <WorkbenchIcon name="close" />
                </button>
              ) : null}
              <button
                aria-expanded={inspectorOpen}
                aria-label={inspectorOpen ? "收起对象上下文" : "展开对象上下文"}
                className={styles.canvasPanelToggle}
                onClick={() => setInspectorOpen((open) => !open)}
                type="button"
              >
                <WorkbenchIcon name="chevron" />
              </button>
            </div>
          </header>
          <div
            className={styles.canvasContent}
            data-conversation-active={agentSurface === "desk"}
            data-mode={agentSurface === "desk" ? "workbench" : workspaceMode}
            data-view={view}
          >
            <div
              className={styles.canvasWorkspaceContent}
              hidden={agentSurface === "desk"}
            >
            {workspaceMode === "workbench" ? (
              <section className={styles.workbenchHome}>
                <div className={styles.workbenchHomeMain}>
                  <header>
                    <h1>从故事未解之处继续</h1>
                  </header>
                  <div className={styles.workbenchHomeGrid}>
                    <button onClick={() => switchWorkspaceMode("analysis")} type="button">
                      <span>继续上次分析</span>
                      <strong>{seed.caseMeta.branchLabel}</strong>
                      <small>回到时间与因果结构</small>
                      <i>继续分析 →</i>
                    </button>
                    <button onClick={() => { setWorkspaceMode("analysis"); setView("evidence"); setEvidenceTab("issues"); }} type="button">
                      <span>检查故事逻辑</span>
                      <strong>{unresolvedCount} 个问题待判断</strong>
                      <small>证据、结论与确定性验证</small>
                      <i>查看问题 →</i>
                    </button>
                    <button onClick={planNextStepsWithAgent} type="button">
                      <span>规划下一步</span>
                      <strong>让 Agent 梳理创作优先级</strong>
                      <small>推进情节、补足线索与明确行动</small>
                      <i>开始规划 →</i>
                    </button>
                  </div>
                </div>
              </section>
            ) : null}

            {workspaceMode === "analysis" && view === "timeline" ? (
              seed.timelineEvents.length ? (
                selectedEvent ? (
                  <TimelineOverview
                    editable={Boolean(
                      realData &&
                        !writeLocked &&
                        realDocument?.schema_version === "2.0" &&
                        onPreviewEventTime &&
                        onSaveObject,
                    )}
                    draftId={
                      realData && !writeLocked && currentDraft
                        ? currentDraft.draft_id
                        : undefined
                    }
                    exposurePlanEditable={Boolean(
                      realData && !writeLocked && currentDraft,
                    )}
                    issueStatuses={visibleIssueStatuses}
                    onConfirmTime={async (eventId, time) => {
                      const result = await (
                        onSaveObject?.(eventId, { time }) ?? Promise.resolve("error")
                      );
                      return typeof result === "object" ? "error" : result;
                    }}
                    onPreviewTime={onPreviewEventTime}
                    onSelectEvent={selectEvent}
                    projectId={
                      realData && !writeLocked && currentDraft && projectId !== null
                        ? projectId
                        : undefined
                    }
                    saving={savingObject}
                    seed={seed}
                    selectedEventId={selectedEventId}
                    relatedConclusionEventIds={conclusionRelatedEventIds}
                    validationStatus={timelineValidationStatus}
                  />
                ) : (
                  <section className={styles.realEmptyState}><strong>此对象没有关联事件</strong><p>对象上下文仍显示当前对象详情；可以从对象树选择其他对象继续核对。</p></section>
                )
              ) : (
                <section className={styles.realEmptyState}><strong>当前工作稿还没有事件</strong><p>事件由已采用候选决定；这里不会补入样例时间线。</p></section>
              )
            ) : null}
            {workspaceMode === "analysis" && view === "relations" ? (
              <RelationshipGraph layoutScope={canvasLayoutScope} onSelectObject={(objectId) => selectObject(objectId, true)} seed={seed} selectedObjectId={selectedObjectId} />
            ) : null}
            {workspaceMode === "analysis" && view === "reasoning" ? (
              <ReasoningGraphView
                layoutScope={canvasLayoutScope}
                onSelectObject={(objectId) => selectObject(objectId, true)}
                onTransitionConclusion={onTransitionConclusion
                  ? (resolutionId, action) => {
                      void onTransitionConclusion(resolutionId, action).then((result) => {
                        setLiveMessage(
                          result === "saved"
                            ? action === "confirm"
                              ? "最终结论已由作者确认。"
                              : "最终结论已撤回为待确认。"
                            : result === "conflict"
                              ? "工作稿已更新，已重新载入最新结论。"
                            : typeof result === "object"
                              ? result.message
                              : "结论状态更新失败。",
                        );
                      });
                    }
                  : undefined}
                seed={seed}
                selectedObjectId={selectedObjectId}
                transitionBusy={savingObject}
              />
            ) : null}
            {workspaceMode === "analysis" && view === "map" ? (
              <SpatialMapView
                map={seed.map}
                meta={seed.caseMeta.mapMeta}
                note={seed.caseMeta.mapNote}
                onClearSelection={clearMapSelection}
                onOpenLocationDetails={(locationId) =>
                  selectObject(locationId, false, false)
                }
                onPositionEditStateChange={(active, dirty) => {
                  setSpatialEditActive(active);
                  setSpatialEditDirty(dirty);
                  if (!active) setObjectEditorNavigationNotice(null);
                }}
                onReloadSpatialLocation={onReloadSpatialLocation}
                onRequestPositionEdit={() => {
                  if (!objectEditorDirty) return true;
                  const message = "对象详情有未保存修改，请先保存或取消后再编辑位置。";
                  setMobileRegion("inspector");
                  setObjectEditorNavigationNotice(message);
                  announce(message);
                  return false;
                }}
                onSaveSpatialPosition={onSaveSpatialPosition}
                onSelectEvent={(eventId) =>
                  selectEvent(eventId, { preserveView: true })
                }
                onSelectLocation={(locationId) =>
                  selectObject(locationId, false, true)
                }
                selectedEventId={selectedEventId}
                selectedObjectId={selectedObjectId}
                readOnlyReason={
                  writeLocked
                    ? "候选预览只读；采用为当前工作稿后才能编辑位置。"
                    : !realData
                      ? "本地样例只读，不写入持久化位置。"
                      : !onSaveSpatialPosition
                        ? "当前工作稿没有可用的位置写入通道。"
                        : null
                }
                title={seed.caseMeta.mapTitle}
              />
            ) : null}
            {workspaceMode === "compile" && view === "export" ? <ExportView seed={seed} unresolvedCount={unresolvedCount} /> : null}
            {workspaceMode === "compile" && view === "compile" ? (
              <CompileCenterView seed={seed} unresolvedCount={unresolvedCount} />
            ) : null}
            {workspaceMode === "analysis" && view === "evidence" ? (
              <div className={styles.evidenceView}>
                <div
                  aria-label="证据对比子视图"
                  className={styles.evidenceTabs}
                  role="tablist"
                >
                  <button
                    aria-selected={evidenceTab === "matrix"}
                    onClick={() => setEvidenceTab("matrix")}
                    role="tab"
                    type="button"
                  >
                    证据矩阵
                  </button>
                  <button
                    aria-selected={evidenceTab === "issues"}
                    onClick={() => setEvidenceTab("issues")}
                    role="tab"
                    type="button"
                  >
                    验证问题
                  </button>
                </div>
                {evidenceTab === "matrix" ? (
                  <>
                    {realData && visibleSelectedIssueId ? (
                      <div className={styles.evidenceActions}>
                        <button
                          onClick={() => sendIssueToAgent(visibleSelectedIssueId)}
                          type="button"
                        >
                          让 Agent 处理
                        </button>
                      </div>
                    ) : null}
                    <EvidenceComparisonView
                      onSelectObject={(objectId) => selectObject(objectId)}
                      seed={seed}
                      selectedObjectId={selectedObjectId}
                    />
                  </>
                ) : (
                  <ValidationIssuePanel
                    editing={manualEditing}
                    issueId={visibleSelectedIssueId}
                    issueStatuses={visibleIssueStatuses}
                    manualValue={manualValue}
                    onManualValueChange={setManualValue}
                    onRejectPatch={rejectPatch}
                    onRequestPatch={requestPatch}
                    onResolveIssue={resolveIssue}
                    onSaveManual={() => resolveIssue("manual")}
                    onSelectIssue={openIssue}
                    onSelectObject={(objectId) => selectObject(objectId)}
                    onSendToAgent={sendIssueToAgent}
                    onRerunVerification={() => void rerunCurrentVerification()}
                    onStartEditing={() => { setManualEditing(true); announce("人工修订编辑器已打开。"); }}
                    realData={realData}
                    seed={seed}
                    status={selectedStatus}
                  />
                )}
              </div>
            ) : null}
            </div>
          </div>
          <section
            aria-label="CaseFile Agent 聊天框"
            className={styles.agentDock}
            data-surface={agentSurface}
          >
            <Image
              alt=""
              aria-hidden="true"
              className={styles.agentMascot}
              data-testid="agent-mascot"
              height={72}
              src="/casefile-agent-mascot-3d.png"
              width={72}
            />
            {agentSurfaceContent}
          </section>
        </main>

        <aside aria-label="对象上下文" className={styles.inspector}>
          <header className={styles.inspectorHeader}>
            <div>
              <span>{workspaceMode === "workbench" ? "工作台状态" : "对象上下文"}</span>
              <strong>{workspaceMode === "workbench" ? seed.caseMeta.title : getObject(seed, selectedObjectId)?.label ?? selectedEvent?.label ?? "尚未选择对象"}</strong>
            </div>
            <div className={styles.inspectorHeaderActions}>
              {workspaceMode !== "workbench" ? <div aria-label="对象上下文导航历史" className={styles.historyControls} role="group">
                <button
                  aria-label="后退到上一个对象"
                  className={styles.historyButton}
                  data-direction="back"
                  disabled={!objectHistoryBackFrame}
                  onClick={() => navigateObjectHistory("back")}
                  title={objectHistoryBackFrame ? `后退：${objectFocusLabel(objectHistoryBackFrame)}` : undefined}
                  type="button"
                >
                  <WorkbenchIcon name="chevron" />
                </button>
                <button
                  aria-label="前进到下一个对象"
                  className={styles.historyButton}
                  data-direction="forward"
                  disabled={!objectHistoryForwardFrame}
                  onClick={() => navigateObjectHistory("forward")}
                  title={objectHistoryForwardFrame ? `前进：${objectFocusLabel(objectHistoryForwardFrame)}` : undefined}
                  type="button"
                >
                  <WorkbenchIcon name="chevron" />
                </button>
              </div> : null}
              <button
                aria-label="收起对象上下文"
                aria-expanded={inspectorOpen}
                className={styles.inspectorToggle}
                onClick={() => {
                  setInspectorOpen(false);
                  announce("对象上下文已收起。主画布已扩展。");
                }}
                type="button"
              >
                <WorkbenchIcon name="chevron" />
              </button>
            </div>
          </header>
          <div className={styles.inspectorContent}>
            {workspaceMode === "workbench" ? (
              <section aria-label="工作台状态" className={styles.workbenchCaseSummary}>
                <header>
                  <span>CASE</span>
                  <strong>{seed.caseMeta.revision}</strong>
                  <small>{seed.origin === "contract" ? "当前工作稿" : "本地预览"}</small>
                </header>
                <dl className={styles.caseStatusCounts}>
                  <div><dt>对象</dt><dd>{seed.caseObjects.length}</dd></div>
                  <div><dt>事件</dt><dd>{seed.timelineEvents.length}</dd></div>
                  <div><dt>人物</dt><dd>{workbenchPersonCount}</dd></div>
                  <div><dt>地点</dt><dd>{seed.caseObjects.filter((object) => object.kind === "location").length}</dd></div>
                </dl>
                <section className={styles.caseStatusFocus}>
                  <span>当前关注</span>
                  <ul>
                    <li><i aria-hidden="true" /><div><strong>{seed.caseMeta.title}</strong><small>当前卷宗</small></div></li>
                    {selectedObject ? <li><i aria-hidden="true" /><div><strong>{selectedObject.label}</strong><small>焦点对象</small></div></li> : null}
                    {selectedEvent && selectedEvent.id !== selectedObject?.id ? <li><i aria-hidden="true" /><div><strong>{selectedEvent.label}</strong><small>关联事件</small></div></li> : null}
                  </ul>
                </section>
              </section>
            ) : (
              <WorkbenchContextInspector
                auditEntries={auditEntries}
                contextState={contextState}
                document={realDocument}
                navigationNotice={objectEditorNavigationNotice}
                onDirtyChange={updateObjectEditorDirty}
                onReloadContext={onReloadContext}
                onSave={onSaveObject}
                onSelectObject={selectObject}
                onSelectRelatedEvent={selectEvent}
                readOnly={writeLocked || !onSaveObject || spatialEditActive}
                readOnlyReason={
                  spatialEditActive
                    ? "先保存或取消地图位置预览，再编辑对象字段。"
                    : undefined
                }
                relatedEvents={selectedRelatedEvents}
                revision={draftRevision ?? 0}
                revisionLabel={
                  writeLocked
                    ? `候选任务 #${previewCandidate?.task_run_id ?? "—"}`
                    : undefined
                }
                saving={savingObject}
                selectedObject={selectedObject ?? null}
                selectedObjectId={selectedObjectId}
                writeLocked={writeLocked}
              />
            )}
            <div ref={setAgentInspectorHost} />
          </div>
        </aside>
      </div>

      <div aria-atomic="true" aria-live="polite" className={styles.liveStatus} role="status"><span>STATUS</span>{liveMessage}</div>

      <FocusTrapDialog inputRef={paletteInputRef} modalRef={modalRef} onClose={() => setPaletteOpen(false)} onQueryChange={setPaletteQuery} open={paletteOpen} query={paletteQuery}>
        <section><header><span>命令</span><small>{matchingPaletteEntries.length}</small></header>{matchingPaletteEntries.map((item) => <button key={item.id} onClick={() => runPaletteAction(item.action)} type="button"><span className={styles.paletteCommandMark}>⌘</span><span><strong>{item.label}</strong><small>{item.meta}</small></span><i>打开</i></button>)}{matchingPaletteEntries.length === 0 ? <p>没有匹配命令。</p> : null}</section>
        <section><header><span>卷宗对象</span><small>{matchingPaletteObjects.length}</small></header>{matchingPaletteObjects.map((object) => <button key={object.id} onClick={() => runPaletteAction(() => selectObject(object.id, true))} type="button"><span className={styles.paletteObjectMark}>{objectKindLabels[object.kind].slice(0, 1)}</span><span><strong>{object.label}</strong><small>{object.code}</small></span><i>{object.id}</i></button>)}{matchingPaletteObjects.length === 0 ? <p>没有匹配对象。</p> : null}</section>
      </FocusTrapDialog>

    </div>
  );
}
