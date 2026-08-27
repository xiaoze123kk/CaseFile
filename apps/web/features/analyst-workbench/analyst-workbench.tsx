"use client";

import dynamic from "next/dynamic";
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
import settingsStyles from "@/components/settings-entry.module.css";
import styles from "./analyst-workbench.module.css";
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
import {
  CandidatePreviewFactBoundary,
  WorkbenchContextInspector,
} from "./workbench-context-inspector";
import {
  type WorkbenchContextState,
  WorkbenchSourcesPanel,
} from "./workbench-context-panels";
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

type MobileRegion = "objects" | "canvas" | "inspector" | "sources";
type DrawerTab = "audio" | "transcript" | "logs" | "retrieval";

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
  { id: "sources", label: "来源" },
];

const drawerTabs: Array<{ id: DrawerTab; label: string; count?: number }> = [
  { id: "audio", label: "证词录音", count: 1 },
  { id: "transcript", label: "转写文本", count: 3 },
  { id: "logs", label: "模型日志摘要", count: 4 },
  { id: "retrieval", label: "检索命中", count: 3 },
];

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

const DEFAULT_RAIL_WIDTH = 254;
const DEFAULT_INSPECTOR_WIDTH = 400;

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
    if (activeProjectId !== projectId) {
      void loadProject(projectId).catch(() => undefined);
    }
    void fetchCaseDraft(projectId)
      .then((nextDraft) => {
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
    void fetchDraftCandidatePreview(projectId, previewTaskRunId)
      .then((preview) => {
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
        detail={`项目 ${projectId} · 连接服务端 Draft`}
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
    <main className={styles.workbenchGate}>
      <article aria-busy={loading}>
        <span>{loading ? "LOADING DRAFT" : "CURRENT DRAFT"}</span>
        <h1>{title}</h1>
        <p>{detail}</p>
        {onRetry ? <button onClick={onRetry} type="button">重新读取</button> : null}
        <Link href={actionHref}>{actionLabel}</Link>
      </article>
    </main>
  );
  if (projectId === null) return gate;

  return (
    <div className={`${styles.workbench} ${styles.gatedWorkbench}`}>
      <header className={styles.topbar}>
        <div className={styles.brandBlock}>
          <span className={styles.brandMark} aria-hidden="true" />
          <div><strong>CaseFile</strong><small>推理卷宗</small></div>
        </div>
        <ProjectSwitcher
          currentProjectId={projectId}
          fallbackTitle={projectTitle}
        />
        <div className={styles.topStatus} aria-hidden="true" />
        <div className={styles.globalSearch} aria-hidden="true" />
        <div className={styles.topActions}>
          <Link href={`/?project=${projectId}`}>建案中心</Link>
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("audio");
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(58);
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
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const inspectorResizeRef = useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);
  const [agentSurface, setAgentSurface] = useState<AgentSurface>("closed");
  const [agentFocusRequest, setAgentFocusRequest] = useState(0);
  const [agentKickoff, setAgentKickoff] = useState<{
    id: number;
    prompt: string;
    routingHint?: AgentChatRoutingHint;
  } | null>(null);
  const [agentInspectorHost, setAgentInspectorHost] = useState<HTMLElement | null>(null);
  const [agentFocusPatchSetId, setAgentFocusPatchSetId] = useState<number | null>(null);
  const [agentFocusFindingId, setAgentFocusFindingId] = useState<string | null>(null);
  const modalRef = useRef<HTMLElement>(null);
  const paletteInputRef = useRef<HTMLInputElement>(null);
  const commandTriggerRef = useRef<HTMLButtonElement>(null);
  const agentTriggerRef = useRef<HTMLButtonElement>(null);
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
    const width = clamp(resize.startWidth + (event.clientX - resize.startX), 170, 460);
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
      340,
      520,
    );
    setInspectorWidth(width);
  }

  function endInspectorResize() {
    inspectorResizeRef.current = null;
  }

  const selectedEvent = getEvent(seed, selectedEventId);
  const selectedObject = getObject(seed, selectedObjectId);
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
        if (!writeLocked) openQuickAsk();
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

  function switchWorkbenchView(nextView: WorkbenchView, label: string) {
    if (nextView !== view && blockDirtyObjectNavigation()) return;
    setView(nextView);
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
    if (revealInDirectory) {
      setObjectQuery("");
      setKindFilter(directoryObjectKind(object.kind));
      setSubtypeFilter("all");
    }
    if (realData) {
      if (!preserveCanvas) setMobileRegion("inspector");
    }
    commitObjectFocus({ objectId: object.id, view });
    announce(`已选择${objectKindLabels[object.kind]}“${object.label}”，相关事件已高亮。`);
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
    setDrawerOpen(false);
    setDrawerTab("audio");
    setPlaying(false);
    setPlayhead(58);
    setManualEditing(false);
    setManualValue(seed.validationIssues[0]?.patchAfter ?? "");
    setAuditEntries([...seed.initialAuditEntries]);
    setAgentSurface("closed");
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
    }, {
      id: "open-audio",
      label: `打开${seed.drawer.audioTitle}`,
      meta: "来源",
      action: () => { setDrawerOpen(true); setDrawerTab("audio"); setMobileRegion("sources"); announce(`已打开${seed.drawer.audioTitle}。`); },
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
    agentTriggerRef.current?.focus();
    setAgentSurface("closed");
    setAgentKickoff(null);
  }

  function openQuickAsk() {
    setAgentSurface("quick");
    setAgentFocusRequest((version) => version + 1);
  }

  const agentLiveProps =
    realData && projectId !== null && currentDraft
      ? {
          draftId: currentDraft.draft_id,
          draftRevision: draftRevision ?? currentDraft.revision,
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
          onLocateObject: (objectId: string) => selectObject(objectId, true),
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
          onDraftChanged: onCurrentDraftChanged ?? (async () => {}),
          projectId,
          referenceLabels: {
            objects: Object.fromEntries(seed.caseObjects.map((object) => [object.id, object.label])),
            events: Object.fromEntries(seed.timelineEvents.map((event) => [event.id, event.label])),
            issues: Object.fromEntries(seed.validationIssues.map((issue) => [issue.id, issue.title])),
          },
        }
      : null;

  const agentSurfaceContent =
    agentSurface === "closed" ? null : (
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
          <div><strong>CaseFile</strong><small>推理卷宗</small></div>
          <button
            aria-label="打开模型服务设置"
            className={settingsStyles.settingsEntry}
            data-casefile-surface="workbench"
            onClick={() => window.dispatchEvent(new Event("casefile:open-settings"))}
            title="模型服务设置"
            type="button"
          >
            <span aria-hidden="true" className={settingsStyles.settingsDot} />
            <span className={settingsStyles.settingsLabel}>模型</span>
          </button>
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
          <button disabled={writeLocked} data-tone="muted" onClick={() => switchWorkbenchView("export", "导出预览")} type="button">
            <WorkbenchIcon name="export" />
            <span><small>导出</small><strong>{writeLocked ? "预览锁定" : realData ? "开发预览" : unresolvedCount > 0 ? "门禁阻断" : "可以导出"}</strong></span>
          </button>
        </div>
        <button className={styles.globalSearch} onClick={() => setPaletteOpen(true)} ref={commandTriggerRef} type="button">
          <WorkbenchIcon name="search" />
          <span>搜索对象或命令</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className={styles.topActions}>
          <button aria-label="打开命令面板" onClick={() => setPaletteOpen(true)} type="button"><WorkbenchIcon name="command" /></button>
          <button
            aria-expanded={agentSurface !== "closed"}
            aria-label={writeLocked ? "候选预览不可使用 Agent" : "打开卷宗统筹 Agent 对话"}
            disabled={writeLocked}
            onClick={openQuickAsk}
            ref={agentTriggerRef}
            type="button"
          >
            <WorkbenchIcon name="chat" />
          </button>
          <button aria-label={writeLocked ? "候选预览不可重置" : "重置工作台数据"} disabled={writeLocked} onClick={resetWorkbench} type="button"><WorkbenchIcon name="reset" /></button>
          <Link href="/">建案中心</Link>
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
        style={
          {
            "--rail-width": `${railWidth ?? DEFAULT_RAIL_WIDTH}px`,
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
        <aside aria-label="卷宗对象导航" className={styles.objectRail}>
          <section className={styles.projectTree}>
            <div className={styles.railEyebrow}>
              <span>卷宗对象导航</span>
              <b>{seed.caseObjects.length}</b>
            </div>
            <div className={styles.objectNavTitle}>
              <span className={styles.projectMonogram}>{seed.caseMeta.monogram}</span>
              <span>
                <strong>{seed.caseMeta.branchLabel}</strong>
                <small>
                  {currentDraft
                    ? `工作稿 #${currentDraft.draft_id} · R${currentDraft.revision}`
                    : seed.caseMeta.revision}
                </small>
              </span>
            </div>
            <div className={styles.treeBranches}>
              <button data-active="true" type="button"><i />{seed.caseMeta.branchLabel} <b>{seed.timelineEvents.length}</b></button>
              {realData ? (
                <button type="button"><i />{writeLocked ? "候选任务" : "服务端修订"} <b>{writeLocked ? `#${previewCandidate?.task_run_id ?? "—"}` : `R${draftRevision ?? "—"}`}</b></button>
              ) : (
                <>
                  <button type="button"><i />未采用候选 <b>03</b></button>
                  <button type="button"><i />导出模板 <b>02</b></button>
                </>
              )}
            </div>
          </section>

          <WorkbenchObjectDirectory
            kindFilter={kindFilter}
            kinds={realData ? productionObjectKinds : fixtureObjectKinds}
            objects={seed.caseObjects}
            onKindFilterChange={setKindFilter}
            onQueryChange={setObjectQuery}
            onSelectObject={(objectId) => selectObject(objectId)}
            onSubtypeFilterChange={setSubtypeFilter}
            query={objectQuery}
            relatedObjectIds={eventRelatedObjectIds}
            selectedObjectId={selectedObjectId}
            subtypeFilter={subtypeFilter}
          />
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
          {agentSurface === "desk" ? agentSurfaceContent : null}
          {agentSurface !== "desk" ? (
            <>
          <header className={styles.canvasToolbar}>
            {projectId !== null && currentDraft && onDraftActivated && !writeLocked ? (
              <DraftSwitcher
                currentDraft={currentDraft}
                onActivated={onDraftActivated}
                onBeforeSwitch={() => !blockDirtyObjectNavigation()}
                onCurrentDraftChanged={onCurrentDraftChanged}
                projectId={projectId}
              />
            ) : null}
            <div className={styles.viewTabs} aria-label="主画布视图" role="tablist">
              {workbenchViewOptions.map((option) => (
                <button aria-selected={view === option.id} disabled={writeLocked && (option.id === "export" || option.id === "compile")} key={option.id} onClick={() => switchWorkbenchView(option.id, option.label)} role="tab" type="button">
                  <span>{option.shortLabel}</span>{option.label}
                </button>
              ))}
            </div>
          </header>
          <div className={styles.canvasContent} data-view={view}>
            {view === "timeline" ? (
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
            {view === "relations" ? (
              <RelationshipGraph layoutScope={canvasLayoutScope} onSelectObject={(objectId) => selectObject(objectId, true)} seed={seed} selectedObjectId={selectedObjectId} />
            ) : null}
            {view === "reasoning" ? (
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
            {view === "map" ? (
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
            {view === "export" ? <ExportView seed={seed} unresolvedCount={unresolvedCount} /> : null}
            {view === "compile" ? (
              <CompileCenterView seed={seed} unresolvedCount={unresolvedCount} />
            ) : null}
            {view === "evidence" ? (
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
            {agentSurface === "quick" ? agentSurfaceContent : null}
          </div>
            </>
          ) : null}
        </main>

        <aside aria-label="对象上下文" className={styles.inspector}>
          <header className={styles.inspectorHeader}>
            <div><span>对象上下文</span><strong>{getObject(seed, selectedObjectId)?.label ?? selectedEvent?.label ?? "尚未选择对象"}</strong></div>
            <div className={styles.inspectorHeaderActions}>
              <div aria-label="对象上下文导航历史" className={styles.historyControls} role="group">
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
              </div>
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
            <WorkbenchContextInspector
              auditEntries={auditEntries}
              contextState={contextState}
              document={realDocument}
              navigationNotice={objectEditorNavigationNotice}
              onDirtyChange={updateObjectEditorDirty}
              onOpenSources={() => {
                setDrawerOpen(true);
                setMobileRegion("sources");
              }}
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
            <div ref={setAgentInspectorHost} />
          </div>
        </aside>
        {!inspectorOpen ? (
          <button
            aria-label="展开对象上下文"
            aria-expanded={inspectorOpen}
            className={styles.inspectorRestore}
            onClick={() => {
              setInspectorOpen(true);
              announce("对象上下文已展开。");
            }}
            type="button"
          >
            <WorkbenchIcon name="chevron" />
            <span>对象上下文</span>
          </button>
        ) : null}
      </div>

      <section aria-label="来源与运行记录抽屉" className={styles.bottomDrawer} data-open={drawerOpen}>
        <header className={styles.drawerHeader}>
          <button aria-expanded={drawerOpen} className={styles.drawerToggle} onClick={() => setDrawerOpen((open) => !open)} type="button"><WorkbenchIcon name="chevron" /><span>来源抽屉</span><small>录音、转写与检索依据</small></button>
          <div className={styles.drawerTabs} role="tablist">
            {drawerTabs.map((tab) => <button aria-selected={drawerTab === tab.id} key={tab.id} onClick={() => { setDrawerTab(tab.id); setDrawerOpen(true); }} role="tab" type="button">{tab.label}{!realData && tab.count ? <b>{tab.count}</b> : null}</button>)}
          </div>
          <div className={styles.drawerObject}><span>绑定对象</span><strong>{selectedEvent?.id ?? "—"}</strong></div>
        </header>
        {drawerOpen ? (
          <div className={styles.drawerContent}>
            {writeLocked ? (
              <CandidatePreviewFactBoundary area="sources" />
            ) : realData ? (
              <WorkbenchSourcesPanel
                onRetry={onReloadContext ?? (() => undefined)}
                state={contextState}
              />
            ) : drawerTab === "audio" ? (
              <div className={styles.audioPlayer}>
                <button aria-label={playing ? "暂停录音" : "播放录音"} className={styles.playButton} onClick={() => { setPlaying((value) => !value); announce(playing ? "录音已暂停。" : `正在播放${seed.drawer.audioTitle}。`); }} type="button"><WorkbenchIcon name={playing ? "pause" : "play"} /></button>
                <div className={styles.waveform} aria-label={`录音播放进度 ${playhead}%`} role="progressbar" aria-valuemax={100} aria-valuemin={0} aria-valuenow={playhead}>{Array.from({ length: 42 }, (_, index) => <i data-played={index / 41 * 100 <= playhead} key={index} style={{ height: `${22 + ((index * 17) % 64)}%` }} />)}</div>
                <div className={styles.audioMeta}><span>{seed.drawer.audioProgress}</span><strong>{seed.drawer.audioTitle}</strong><small>关键短句将在 {seed.drawer.keyTime} 出现 · 共 {seed.drawer.audioDuration}</small></div>
                <button className={styles.jumpButton} onClick={() => { setPlayhead(62); announce(`播放位置已跳转到 ${seed.drawer.keyTime} 的关键证词。`); }} type="button">跳到 {seed.drawer.keyTime}</button>
              </div>
            ) : null}
            {!realData && drawerTab === "transcript" ? <div className={styles.transcriptPanel}><time>{seed.drawer.keyTime}</time><p><mark>“{seed.drawer.keyExcerpt}”</mark> {seed.drawer.transcript}</p><button disabled={!seed.defaultIssueId} onClick={() => { if (seed.defaultIssueId) openIssue(seed.defaultIssueId); }} type="button">对照验证问题</button></div> : null}
            {!realData && drawerTab === "logs" ? <div className={styles.logPanel}><ul>{seed.drawer.logs.map((entry) => <li key={`${entry.time}-${entry.actor}`}><span>{entry.time}</span><strong>{entry.actor}</strong><p>{entry.detail}</p></li>)}</ul></div> : null}
            {!realData && drawerTab === "retrieval" ? <div className={styles.retrievalPanel}>{seed.sourceItems.filter((source) => source.kind === "retrieval" || source.kind === "record").map((source) => <article key={source.id}><span>{source.kind}</span><div><strong>{source.label}</strong><p>{source.excerpt}</p></div><button onClick={() => selectEvent(source.eventId)} type="button">定位</button></article>)}</div> : null}
          </div>
        ) : null}
      </section>

      <div aria-atomic="true" aria-live="polite" className={styles.liveStatus} role="status"><span>STATUS</span>{liveMessage}</div>

      <FocusTrapDialog inputRef={paletteInputRef} modalRef={modalRef} onClose={() => setPaletteOpen(false)} onQueryChange={setPaletteQuery} open={paletteOpen} query={paletteQuery}>
        <section><header><span>命令</span><small>{matchingPaletteEntries.length}</small></header>{matchingPaletteEntries.map((item) => <button key={item.id} onClick={() => runPaletteAction(item.action)} type="button"><span className={styles.paletteCommandMark}>⌘</span><span><strong>{item.label}</strong><small>{item.meta}</small></span><i>打开</i></button>)}{matchingPaletteEntries.length === 0 ? <p>没有匹配命令。</p> : null}</section>
        <section><header><span>卷宗对象</span><small>{matchingPaletteObjects.length}</small></header>{matchingPaletteObjects.map((object) => <button key={object.id} onClick={() => runPaletteAction(() => selectObject(object.id, true))} type="button"><span className={styles.paletteObjectMark}>{objectKindLabels[object.kind].slice(0, 1)}</span><span><strong>{object.label}</strong><small>{object.code}</small></span><i>{object.id}</i></button>)}{matchingPaletteObjects.length === 0 ? <p>没有匹配对象。</p> : null}</section>
      </FocusTrapDialog>

    </div>
  );
}
