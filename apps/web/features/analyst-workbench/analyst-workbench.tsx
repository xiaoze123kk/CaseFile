"use client";

import type { CaseFile } from "@casefile/contracts";
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

import { ApiError, type DraftView } from "@/lib/api-client";

import {
  defaultWorkbenchSeed,
  getEvent,
  getObject,
  type InspectorTab,
  type IssueStatus,
  objectKindLabels,
  type WorkbenchCandidate,
  type WorkbenchSeed,
  viewOptions,
  type WorkbenchView,
} from "./analyst-fixture";
import {
  type WorkbenchCandidateStatus,
  useCaseSession,
} from "@/features/case-session/case-session-provider";
import {
  fetchCaseDraft,
  patchCaseDraftObject,
} from "@/features/case-session/case-session-api";
import settingsStyles from "@/components/settings-entry.module.css";
import styles from "./analyst-workbench.module.css";
import relayStyles from "./candidate-relay.module.css";
import { AgentPanel } from "./workbench-agent-panel";
import { clamp } from "./workbench-geometry";
import { WorkbenchIcon } from "./workbench-icon";
import {
  type DirectoryObjectKind,
  directoryObjectKind,
  fixtureObjectKinds,
  productionObjectKinds,
  WorkbenchObjectDirectory,
} from "./workbench-object-directory";
import { WorkbenchObjectEditor } from "./workbench-object-editor";
import { mapCaseFileToWorkbenchModel } from "./workbench-real-data";
import { ReasoningGraphView } from "./workbench-reasoning-graph";
import { RelationshipGraph } from "./workbench-relationship-graph";
import {
  CompileCenterView,
  DossierView,
  ExportView,
  MapView,
  TimelineOverview,
} from "./workbench-secondary-views";

type MobileRegion = "objects" | "canvas" | "inspector" | "sources";
type DrawerTab = "audio" | "transcript" | "logs" | "retrieval";
type ValidationPhase = "idle" | "recomputing" | "running";

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

const inspectorTabs: Array<{ id: InspectorTab; label: string }> = [
  { id: "object", label: "对象详情" },
  { id: "issues", label: "验证问题" },
  { id: "sources", label: "引用来源" },
  { id: "patch", label: "补丁审阅" },
  { id: "audit", label: "审计记录" },
];

const mobileRegions: Array<{ id: MobileRegion; label: string }> = [
  { id: "objects", label: "对象" },
  { id: "canvas", label: "主画布" },
  { id: "inspector", label: "检查器" },
  { id: "sources", label: "来源" },
];

const drawerTabs: Array<{ id: DrawerTab; label: string; count?: number }> = [
  { id: "audio", label: "证词录音", count: 1 },
  { id: "transcript", label: "转写文本", count: 3 },
  { id: "logs", label: "模型日志摘要", count: 4 },
  { id: "retrieval", label: "检索命中", count: 3 },
];

function statusLabel(status: IssueStatus) {
  if (status === "patch-ready") return "补丁待审批";
  if (status === "resolved") return "已解决";
  if (status === "exception") return "已知例外";
  return "待处理";
}

function currentClock() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
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

function EvidenceComparison({
  seed,
  issueId,
  status,
  manualValue,
  editing,
  onManualValueChange,
  onStartEditing,
  onSaveManual,
}: {
  seed: WorkbenchSeed;
  issueId: string | null;
  status: IssueStatus;
  manualValue: string;
  editing: boolean;
  onManualValueChange: (value: string) => void;
  onStartEditing: () => void;
  onSaveManual: () => void;
}) {
  const issue =
    seed.validationIssues.find((item) => item.id === issueId) ??
    seed.validationIssues[0];
  if (!issue) {
    return (
      <section className={styles.realEmptyState} aria-label="证据对照尚未接入">
        <strong>暂无可对照的验证问题</strong>
        <p>真实验证、补丁与来源将在后续接入；当前不会展示本地样例。</p>
      </section>
    );
  }
  return (
    <section className={styles.evidenceCompare} aria-labelledby="evidence-heading">
      <header className={styles.sectionHeader}>
        <div><span>证据 × 知识状态</span><h2 id="evidence-heading">{issue.title}</h2></div>
        <small>{issue.severity} · {statusLabel(status)}</small>
      </header>
      <div className={styles.knowledgeSequence}>
        <article>
          <span>事件前已知</span>
          <strong>22:31 前</strong>
          <p>{issue.beforeKnowledge}</p>
        </article>
        <i aria-hidden="true" />
        <article data-conflict="true">
          <span>事件声称</span>
          <strong>{getEvent(seed, issue.eventId)?.time}</strong>
          <p>{issue.eventClaim}</p>
        </article>
        <i aria-hidden="true" />
        <article>
          <span>证据实际进入</span>
          <strong>22:40</strong>
          <p>{issue.afterKnowledge}</p>
        </article>
      </div>
      <div className={styles.diffPanel}>
        <header><span>建议修订</span><b>人工批准前不会写入 Canon</b></header>
        <div className={styles.diffLine} data-kind="remove"><b>−</b><p>{issue.patchBefore}</p></div>
        <div className={styles.diffLine} data-kind="add"><b>+</b><p>{issue.patchAfter}</p></div>
        {editing ? (
          <label className={styles.manualEditor}>
            <span>人工修订文本</span>
            <textarea autoFocus onChange={(event) => onManualValueChange(event.target.value)} rows={4} value={manualValue} />
            <button onClick={onSaveManual} type="button">保存并局部重算</button>
          </label>
        ) : (
          <button className={styles.textAction} onClick={onStartEditing} type="button">改为人工修正</button>
        )}
      </div>
    </section>
  );
}

const DEFAULT_RAIL_WIDTH = 254;
const DEFAULT_INSPECTOR_WIDTH = 350;

export function AnalystWorkbench({
  requestedProjectId,
  invalidProjectId = false,
}: {
  /** `undefined` is reserved for the isolated fixture harness used by component tests. */
  requestedProjectId?: number | null;
  invalidProjectId?: boolean;
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
  const [draftLoad, setDraftLoad] = useState<{
    projectId: number;
    draft: DraftView | null;
    error: string | null;
  } | null>(null);
  const [reloadDraft, setReloadDraft] = useState(0);
  const [savingObject, setSavingObject] = useState(false);
  const saveInFlightRef = useRef(false);

  useEffect(() => {
    if (fixtureMode || invalidProjectId || projectId === null) return;
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
          error:
            caught instanceof Error ? caught.message : "当前工作稿加载失败。",
        });
      });
    return () => {
      active = false;
    };
  }, [
    activeProjectId,
    fixtureMode,
    invalidProjectId,
    loadProject,
    projectId,
    reloadDraft,
  ]);

  const fixtureSeed = activeCandidate?.workbenchSeed ?? defaultWorkbenchSeed;
  const activeCandidateStatus = activeCandidate
    ? candidateStatus(activeCandidate)
    : null;
  const currentDraftLoad =
    projectId !== null && draftLoad?.projectId === projectId ? draftLoad : null;
  const draft = currentDraftLoad?.draft ?? null;
  const draftError = currentDraftLoad?.error ?? null;

  if (fixtureMode) {
    return (
      <AnalystWorkbenchSurface
        activeCandidate={activeCandidate}
        activeCandidateStatus={activeCandidateStatus}
        adoptCandidate={adoptCandidate}
        key={fixtureSeed.id}
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
  if (currentDraftLoad === null) {
    return <WorkbenchGate title="正在读取当前工作稿" detail={`项目 ${projectId} · 连接服务端 Draft`} loading />;
  }
  if (draftError) {
    return (
      <WorkbenchGate
        detail={draftError}
        onRetry={() => {
          setDraftLoad(null);
          setReloadDraft((value) => value + 1);
        }}
        title="当前工作稿加载失败"
      />
    );
  }
  if (!draft?.content) {
    return <WorkbenchGate title="这个项目尚无当前工作稿" detail="请先返回建案中心生成三份候选，并采用其中一份。" />;
  }

  const loadedProjectId = projectId;
  const seed = mapCaseFileToWorkbenchModel(draft.content, draft.revision);

  async function saveObject(
    objectId: string,
    changes: Record<string, unknown>,
  ): Promise<"saved" | "conflict" | "error"> {
    if (!draft || saveInFlightRef.current) return "error";
    saveInFlightRef.current = true;
    setSavingObject(true);
    try {
      await patchCaseDraftObject(loadedProjectId, objectId, draft.revision, changes);
      const latest = await fetchCaseDraft(loadedProjectId);
      setDraftLoad({ projectId: loadedProjectId, draft: latest, error: null });
      return "saved";
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.status === 409 || caught.body.code === "draft_revision_conflict")
      ) {
        try {
          const latest = await fetchCaseDraft(loadedProjectId);
          setDraftLoad({ projectId: loadedProjectId, draft: latest, error: null });
          return "conflict";
        } catch {
          return "error";
        }
      }
      return "error";
    } finally {
      saveInFlightRef.current = false;
      setSavingObject(false);
    }
  }

  return (
    <AnalystWorkbenchSurface
      activeCandidate={activeCandidate}
      activeCandidateStatus={activeCandidateStatus}
      adoptCandidate={adoptCandidate}
      draftRevision={draft.revision}
      key={`project-${projectId}`}
      onSaveObject={saveObject}
      realDocument={draft.content}
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
}: {
  title: string;
  detail: string;
  loading?: boolean;
  onRetry?: () => void;
}) {
  return (
    <main className={styles.workbenchGate}>
      <article aria-busy={loading}>
        <span>{loading ? "LOADING DRAFT" : "CURRENT DRAFT"}</span>
        <h1>{title}</h1>
        <p>{detail}</p>
        {onRetry ? <button onClick={onRetry} type="button">重新读取</button> : null}
        <Link href="/">返回建案中心</Link>
      </article>
    </main>
  );
}

function AnalystWorkbenchSurface({
  seed,
  activeCandidate,
  activeCandidateStatus,
  adoptCandidate,
  realDocument = null,
  draftRevision = null,
  savingObject = false,
  onSaveObject,
}: {
  seed: WorkbenchSeed;
  activeCandidate: WorkbenchCandidate | null;
  activeCandidateStatus: WorkbenchCandidateStatus | null;
  adoptCandidate: (candidateId: string) => Promise<boolean>;
  realDocument?: CaseFile | null;
  draftRevision?: number | null;
  savingObject?: boolean;
  onSaveObject?: (
    objectId: string,
    changes: Record<string, unknown>,
  ) => Promise<"saved" | "conflict" | "error">;
}) {
  const realData = realDocument !== null;
  const [view, setView] = useState<WorkbenchView>("timeline");
  const [selectedEventId, setSelectedEventId] = useState(seed.defaultEventId);
  const [selectedObjectId, setSelectedObjectId] = useState(seed.defaultObjectId);
  const [selectedIssueId, setSelectedIssueId] = useState(seed.defaultIssueId);
  const [issueStatuses, setIssueStatuses] = useState<Record<string, IssueStatus>>(
    () => createIssueStatuses(seed),
  );
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(
    seed.validationIssues.length ? "issues" : "object",
  );
  const [kindFilter, setKindFilter] = useState<DirectoryObjectKind | "all">(
    "all",
  );
  const [subtypeFilter, setSubtypeFilter] = useState<string | "all">("all");
  const [objectQuery, setObjectQuery] = useState("");
  const [objectEditorDirty, setObjectEditorDirty] = useState(false);
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
  const [validationPhase, setValidationPhase] = useState<ValidationPhase>("idle");
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
  const [agentOpen, setAgentOpen] = useState(false);
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
      250,
      520,
    );
    setInspectorWidth(width);
  }

  function endInspectorResize() {
    inspectorResizeRef.current = null;
  }

  const selectedEvent = getEvent(seed, selectedEventId);
  const selectedObject = getObject(seed, selectedObjectId);
  const selectedIssue =
    seed.validationIssues.find((item) => item.id === selectedIssueId) ??
    seed.validationIssues[0];
  const selectedStatus = selectedIssue
    ? issueStatuses[selectedIssue.id] ?? "open"
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
  const unresolvedCount = seed.validationIssues.filter((issue) => {
    const status = issueStatuses[issue.id];
    return status === "open" || status === "patch-ready";
  }).length;

  function schedule(callback: () => void, delay: number) {
    const timer = window.setTimeout(callback, delay);
    timersRef.current.push(timer);
  }

  useEffect(() => () => timersRef.current.forEach((timer) => window.clearTimeout(timer)), []);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

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

  function blockDirtyObjectNavigation(nextObjectId?: string) {
    if (
      !realData ||
      !objectEditorDirty ||
      (nextObjectId !== undefined && nextObjectId === selectedObjectId)
    ) {
      return false;
    }
    const message = "有未保存修改。请先保存或取消修改，再切换对象。";
    setInspectorTab("object");
    setMobileRegion("inspector");
    setObjectEditorNavigationNotice(message);
    announce(message);
    return true;
  }

  function updateObjectEditorDirty(dirty: boolean) {
    setObjectEditorDirty(dirty);
    if (!dirty) setObjectEditorNavigationNotice(null);
  }

  function showInspectorTab(tab: InspectorTab) {
    if (tab !== "object" && blockDirtyObjectNavigation()) return;
    setInspectorTab(tab);
  }

  function appendAudit(actor: string, action: string, detail: string) {
    setAuditEntries((entries) => [
      { id: `AUD-${Date.now()}`, time: currentClock(), actor, action, detail },
      ...entries,
    ]);
  }

  function selectEvent(eventId: string) {
    const event = getEvent(seed, eventId);
    if (!event || blockDirtyObjectNavigation(event.id)) return;
    setSelectedEventId(event.id);
    setSelectedObjectId(event.id);
    setObjectEditorNavigationNotice(null);
    const issueId = event.issueIds[0];
    if (issueId) setSelectedIssueId(issueId);
    setObjectQuery("");
    setKindFilter("event");
    setSubtypeFilter("all");
    setView("timeline");
    if (realData) setInspectorTab("object");
    setMobileRegion("canvas");
    announce(`已选择事件“${event.label}”，关系图和检查器已同步定位。`);
  }

  function selectObject(objectId: string, revealInDirectory = false) {
    const object = getObject(seed, objectId);
    if (!object || blockDirtyObjectNavigation(object.id)) return;
    setSelectedObjectId(object.id);
    const eventId = object.kind === "event" ? object.id : object.relatedEventIds[0];
    setSelectedEventId(eventId ?? null);
    setObjectEditorNavigationNotice(null);
    if (revealInDirectory) {
      setObjectQuery("");
      setKindFilter(directoryObjectKind(object.kind));
      setSubtypeFilter("all");
    }
    if (realData) {
      setInspectorTab("object");
      setMobileRegion("inspector");
    }
    announce(`已选择${objectKindLabels[object.kind]}“${object.label}”，相关事件已高亮。`);
  }

  function openIssue(issueId: string) {
    const issue = seed.validationIssues.find((item) => item.id === issueId);
    if (!issue || blockDirtyObjectNavigation(issue.eventId)) return;
    setSelectedIssueId(issue.id);
    setSelectedEventId(issue.eventId);
    setSelectedObjectId(issue.eventId);
    setObjectQuery("");
    setKindFilter("event");
    setSubtypeFilter("all");
    setView("evidence");
    setInspectorTab("issues");
    setMobileRegion("canvas");
    setManualEditing(false);
    setManualValue(issue.patchAfter);
    announce(`已打开${issue.severity}问题“${issue.title}”，主画布切换到证据与知识状态对照。`);
  }

  function requestPatch() {
    if (!selectedIssue) return;
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: "patch-ready" }));
    setInspectorTab("patch");
    setView("evidence");
    appendAudit("Agent", "生成建议补丁", `${selectedIssue.id} · 等待人工批准`);
    announce("Agent 补丁已生成，仅作为建议展示，等待人工批准。");
  }

  function resolveIssue(action: "approve" | "manual" | "exception") {
    if (!selectedIssue) return;
    const nextStatus: IssueStatus = action === "exception" ? "exception" : "resolved";
    setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: nextStatus }));
    setValidationPhase("recomputing");
    setInspectorTab("audit");
    setManualEditing(false);
    const actionLabel = action === "approve" ? "批准 Agent 补丁" : action === "manual" ? "保存人工修正" : "标记已知例外";
    appendAudit(seed.caseMeta.protagonist, actionLabel, `${selectedIssue.id} · 局部重算`);
    announce(`${actionLabel}已记录，正在执行局部重算。`);
    schedule(() => {
      setValidationPhase("idle");
      setLiveMessage(`${actionLabel}已完成。当前仍有 ${Math.max(0, unresolvedCount - 1)} 个待处理问题。`);
    }, 760);
  }

  function revalidateAll() {
    if (blockDirtyObjectNavigation()) return;
    if (realData) {
      setInspectorTab("issues");
      announce("真实验证接口尚未接入；当前不会运行样例验证器。");
      return;
    }
    setValidationPhase("running");
    appendAudit(
      "Validator",
      "启动全量重新验证",
      `${seed.caseMeta.revision} · ${unresolvedCount} 个待处理问题`,
    );
    announce("全量重新验证已启动。页面保持可浏览，结果将通过状态消息更新。");
    schedule(() => {
      setValidationPhase("idle");
      setLiveMessage(`全量验证完成：${unresolvedCount} 个问题仍需人工决定。`);
    }, 980);
  }

  function resetWorkbench() {
    if (blockDirtyObjectNavigation()) return;
    setView("timeline");
    setSelectedEventId(seed.defaultEventId);
    setSelectedObjectId(seed.defaultObjectId);
    setSelectedIssueId(seed.defaultIssueId);
    setIssueStatuses(createIssueStatuses(seed));
    setInspectorTab(seed.validationIssues.length ? "issues" : "object");
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
    setAgentOpen(false);
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
      action: () => { setView("timeline"); setMobileRegion("canvas"); announce("主画布已切换到事件时间线。"); },
    },
    {
      id: "view-relations",
      label: "打开人物与证据关系图",
      meta: "视图",
      action: () => { setView("relations"); setMobileRegion("canvas"); announce("主画布已切换到关系图。"); },
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
    if (!seed.timelineEvents.length) return;
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const currentIndex = seed.timelineEvents.findIndex((item) => item.id === selectedEventId);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const nextIndex = Math.min(seed.timelineEvents.length - 1, Math.max(0, currentIndex + delta));
    const nextEvent = seed.timelineEvents[nextIndex];
    if (nextEvent) selectEvent(nextEvent.id);
  }

  return (
    <div
      className={styles.workbench}
      data-mobile-region={mobileRegion}
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
        <div className={styles.caseIdentity}>
          <span>当前卷宗</span>
          <strong>{seed.caseMeta.title}</strong>
          <small>{seed.caseMeta.revision}</small>
        </div>
        <div className={styles.topStatus} aria-label="卷宗状态">
          <button data-tone={realData ? "muted" : unresolvedCount > 0 ? "danger" : "success"} onClick={() => { showInspectorTab("issues"); setMobileRegion("inspector"); }} type="button">
            <WorkbenchIcon name="validate" />
            <span><small>验证</small><strong>{realData ? "尚未接入" : unresolvedCount > 0 ? `${unresolvedCount} 个问题` : "已通过"}</strong></span>
          </button>
          <button data-tone="muted" onClick={() => { setView("export"); setMobileRegion("canvas"); }} type="button">
            <WorkbenchIcon name="export" />
            <span><small>导出</small><strong>{realData ? "开发预览" : unresolvedCount > 0 ? "门禁阻断" : "可以导出"}</strong></span>
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
            aria-expanded={agentOpen}
            aria-label={realData ? "卷宗统筹 Agent 尚未接入" : "打开卷宗统筹 Agent 对话"}
            disabled={realData}
            onClick={() => setAgentOpen(true)}
            type="button"
          >
            <WorkbenchIcon name="chat" />
          </button>
          <button aria-label="重置工作台数据" onClick={resetWorkbench} type="button"><WorkbenchIcon name="reset" /></button>
          <Link href="/">建案中心</Link>
        </div>
      </header>

      {activeCandidate ? (
        <section
          aria-label="工作稿接力状态"
          className={relayStyles.candidateRelay}
          data-status={activeCandidateStatus ?? "pending"}
        >
          <div>
            <span>
              {activeCandidateStatus === "current"
                ? "当前工作稿"
                : activeCandidateStatus === "stale"
                  ? "旧简报"
                  : "预览稿"}
            </span>
            <strong>{activeCandidate.title}</strong>
            <p>
              {activeCandidate.focusLabel} · 简报 V
              {String(activeCandidate.briefVersion).padStart(2, "0")} ·
              {realData ? "服务端当前工作稿" : "本地样例"}
            </p>
          </div>
          <div>
            <Link href="/">← 返回候选卷</Link>
            {!realData && activeCandidateStatus === "pending" ? (
              <button
                onClick={() => {
                  void adoptCandidate(activeCandidate.id)
                    .then((ok) => {
                      if (ok) announce("该候选已采用为当前工作稿。");
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
            {activeCandidateStatus === "stale" ? (
              <small>旧简报候选仅供预览，不可采用</small>
            ) : null}
            {activeCandidateStatus === "current" ? (
              <small>{realData ? "已采用 · 服务端持久化" : "已采用 · 客户端路由内保持"}</small>
            ) : null}
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
        <aside aria-label="项目与对象导航" className={styles.objectRail}>
          <section className={styles.projectTree}>
            <div className={styles.railEyebrow}><span>项目树</span><b>01 / 03</b></div>
            <button className={styles.projectSelector} type="button">
              <span className={styles.projectMonogram}>{seed.caseMeta.monogram}</span>
              <span><strong>{seed.caseMeta.title}</strong><small>主卷宗 · {seed.caseMeta.revision}</small></span>
              <WorkbenchIcon name="chevron" />
            </button>
            <div className={styles.treeBranches}>
              <button data-active="true" type="button"><i />{seed.caseMeta.branchLabel} <b>{seed.timelineEvents.length}</b></button>
              {realData ? (
                <button type="button"><i />服务端修订 <b>R{draftRevision ?? "—"}</b></button>
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

        <main className={styles.canvas} id="analyst-canvas" onKeyDown={handleTimelineKeys} tabIndex={-1}>
          <header className={styles.canvasToolbar}>
            <div className={styles.viewTabs} aria-label="主画布视图" role="tablist">
              {viewOptions.map((option) => (
                <button aria-selected={view === option.id} key={option.id} onClick={() => { setView(option.id); announce(`主画布已切换到${option.label}。`); }} role="tab" type="button">
                  <span>{option.shortLabel}</span>{option.label}
                </button>
              ))}
              {view === "evidence" ? <button aria-selected="true" role="tab" type="button"><span>证</span>证据对照</button> : null}
            </div>
            <div className={styles.canvasMeta}><span>同步定位</span><b>{selectedEvent ? `${selectedEvent.time} / ${selectedEvent.id}` : "尚无事件"}</b></div>
          </header>
          <div className={styles.canvasContent} data-view={view}>
            {view === "timeline" ? (
              seed.timelineEvents.length ? (
                selectedEvent ? (
                  <TimelineOverview issueStatuses={issueStatuses} onSelectEvent={selectEvent} onSelectObject={(objectId) => selectObject(objectId, true)} seed={seed} selectedEventId={selectedEventId} selectedObjectId={selectedObjectId} />
                ) : (
                  <section className={styles.realEmptyState}><strong>此对象没有关联事件</strong><p>检查器仍显示当前对象详情；可以从对象树选择其他对象继续核对。</p></section>
                )
              ) : (
                <section className={styles.realEmptyState}><strong>当前工作稿还没有事件</strong><p>事件由已采用候选决定；这里不会补入样例时间线。</p></section>
              )
            ) : null}
            {view === "relations" ? (
              <RelationshipGraph onSelectObject={(objectId) => selectObject(objectId, true)} relatedObjectIds={eventRelatedObjectIds} seed={seed} selectedObjectId={selectedObjectId} />
            ) : null}
            {view === "reasoning" ? (
              <ReasoningGraphView onSelectObject={(objectId) => selectObject(objectId, true)} seed={seed} />
            ) : null}
            {view === "map" ? <MapView onSelectEvent={selectEvent} seed={seed} selectedEventId={selectedEventId} /> : null}
            {view === "dossier" ? (
              selectedEvent ? <DossierView seed={seed} selectedEventId={selectedEventId} /> : <section className={styles.realEmptyState}><strong>没有可编辑的事件卷宗</strong><p>可以从右侧“对象详情”编辑实体、信息、地点或假设。</p></section>
            ) : null}
            {view === "export" ? <ExportView seed={seed} unresolvedCount={unresolvedCount} /> : null}
            {view === "compile" ? (
              <CompileCenterView seed={seed} unresolvedCount={unresolvedCount} />
            ) : null}
            {view === "evidence" ? (
              <EvidenceComparison
                editing={manualEditing}
                issueId={selectedIssueId}
                manualValue={manualValue}
                onManualValueChange={setManualValue}
                onSaveManual={() => resolveIssue("manual")}
                onStartEditing={() => { setManualEditing(true); announce("人工修订编辑器已打开。"); }}
                seed={seed}
                status={selectedStatus}
              />
            ) : null}
          </div>
        </main>

        <aside aria-label="上下文检查器" className={styles.inspector}>
          <header className={styles.inspectorHeader}>
            <div><span>上下文检查器</span><strong>{getObject(seed, selectedObjectId)?.label ?? selectedEvent?.label ?? "尚未选择对象"}</strong></div>
            <div className={styles.inspectorHeaderActions}>
              <small>{selectedObjectId ?? selectedEvent?.id ?? "—"}</small>
              <button
                aria-label="收起上下文检查器"
                aria-expanded={inspectorOpen}
                className={styles.inspectorToggle}
                onClick={() => {
                  setInspectorOpen(false);
                  announce("上下文检查器已收起。主画布已扩展。");
                }}
                type="button"
              >
                <WorkbenchIcon name="chevron" />
              </button>
            </div>
          </header>
          <div className={styles.inspectorTabs} aria-label="检查器内容" role="tablist">
            {inspectorTabs.map((tab) => {
              const count = tab.id === "issues" ? unresolvedCount : tab.id === "sources" ? selectedIssue?.evidenceIds.length ?? 0 : tab.id === "patch" && selectedStatus === "patch-ready" ? 1 : undefined;
              return (
                <button aria-selected={inspectorTab === tab.id} key={tab.id} onClick={() => showInspectorTab(tab.id)} role="tab" type="button">
                  {tab.label}{count !== undefined ? <b>{count}</b> : null}
                </button>
              );
            })}
          </div>
          <div className={styles.inspectorContent}>
            {inspectorTab === "object" ? (
              realDocument && draftRevision !== null && onSaveObject ? (
                <WorkbenchObjectEditor
                  document={realDocument}
                  key={selectedObjectId ?? "no-object"}
                  navigationNotice={objectEditorNavigationNotice}
                  onDirtyChange={updateObjectEditorDirty}
                  onSave={onSaveObject}
                  onSelectRelatedEvent={selectEvent}
                  relatedEvents={selectedRelatedEvents}
                  revision={draftRevision}
                  saving={savingObject}
                  selectedObjectId={selectedObjectId}
                />
              ) : (
                <div className={styles.realEmptyState}><strong>本地样例不提供持久化编辑</strong><p>采用真实候选后，可在这里修改当前工作稿对象。</p></div>
              )
            ) : null}
            {inspectorTab === "issues" ? (
              selectedIssue ? <div className={styles.issueInspector}>
                <div className={styles.issueList}>
                  {seed.validationIssues.map((issue) => {
                    const status = issueStatuses[issue.id] ?? "open";
                    return (
                      <button aria-pressed={issue.id === selectedIssueId} data-status={status} key={issue.id} onClick={() => openIssue(issue.id)} type="button">
                        <span data-severity={issue.severity}>{issue.severity}</span>
                        <span><strong>{issue.title}</strong><small>{statusLabel(status)}</small></span>
                      </button>
                    );
                  })}
                </div>
                <article className={styles.issueDetail}>
                  <header><span data-severity={selectedIssue.severity}>{selectedIssue.severity}</span><div><small>{selectedIssue.rule}</small><h2>{selectedIssue.title}</h2></div></header>
                  <p>{selectedIssue.summary}</p>
                  <dl>
                    <div><dt>定位事件</dt><dd>{getEvent(seed, selectedIssue.eventId)?.time} · {getEvent(seed, selectedIssue.eventId)?.label}</dd></div>
                    <div><dt>依据</dt><dd>{selectedIssue.evidenceIds.map((id) => getObject(seed, id)?.label).filter(Boolean).join("、")}</dd></div>
                    <div><dt>当前状态</dt><dd>{statusLabel(selectedStatus)}</dd></div>
                  </dl>
                  <button className={styles.inspectEvidence} onClick={() => openIssue(selectedIssue.id)} type="button">在主画布查看证据对照</button>
                  <div className={styles.issueActions}>
                    <button onClick={() => { setView("evidence"); setManualEditing(true); setMobileRegion("canvas"); }} type="button">手动修正</button>
                    <button disabled={selectedStatus === "resolved" || selectedStatus === "exception"} onClick={requestPatch} type="button">请求 Agent 补丁</button>
                    <button disabled={selectedStatus === "resolved" || selectedStatus === "exception"} onClick={() => resolveIssue("exception")} type="button">标记已知例外</button>
                  </div>
                </article>
              </div> : <div className={styles.realEmptyState}><strong>验证问题尚未接入</strong><p>当前工作台只展示真实 Draft，不会用本地问题填充检查器。</p></div>
            ) : null}

            {inspectorTab === "sources" ? (
              realData ? (
                <div className={styles.realEmptyState}><strong>引用来源尚未接入</strong><p>当前 CaseFile 只携带来源引用；来源正文与媒体接口将在后续接入。</p></div>
              ) : <div className={styles.sourceInspector}>
                <p>引用只说明“依据来自哪里”，不会自动把检索结果提升为卷宗事实。</p>
                {seed.sourceItems.filter((source) => source.eventId === selectedEventId || (source.evidenceObjectId ? selectedIssue?.evidenceIds.includes(source.evidenceObjectId) : false)).map((source) => (
                  <article key={source.id}>
                    <header><span>{source.kind}</span><small>{source.meta}</small></header>
                    <h2>{source.label}</h2><p>{source.excerpt}</p>
                    <div><button onClick={() => { setDrawerOpen(true); setDrawerTab(source.kind === "audio" ? "audio" : "transcript"); setMobileRegion("sources"); }} type="button">打开来源</button><button onClick={() => selectEvent(source.eventId)} type="button">定位事件</button></div>
                  </article>
                ))}
              </div>
            ) : null}

            {inspectorTab === "patch" ? (
              realData || !selectedIssue ? (
                <div className={styles.realEmptyState}><strong>补丁审阅尚未接入</strong><p>当前不会生成或批准样例补丁；对象编辑会直接写入真实 Draft。</p></div>
              ) : <div className={styles.patchInspector}>
                {selectedStatus === "patch-ready" || selectedStatus === "resolved" ? (
                  <>
                    <div className={styles.patchSummary} data-state={selectedStatus}><span>Agent 建议</span><b>{selectedStatus === "resolved" ? "已批准" : "等待批准"}</b></div>
                    <p>该补丁只调整事件措辞和知识进入时间，不新增人物、证据或关系。</p>
                    <div className={styles.compactDiff}><p data-kind="remove">− {selectedIssue.patchBefore}</p><p data-kind="add">+ {selectedIssue.patchAfter}</p></div>
                    <dl><div><dt>影响范围</dt><dd>1 个事件 · 1 个知识状态</dd></div><div><dt>引用变化</dt><dd>新增 A-13 时间锚点</dd></div></dl>
                    <div className={styles.patchActions}>
                      <button disabled={selectedStatus === "resolved"} onClick={() => { setIssueStatuses((statuses) => ({ ...statuses, [selectedIssue.id]: "open" })); appendAudit(seed.caseMeta.protagonist, "拒绝 Agent 补丁", selectedIssue.id); announce("补丁已拒绝，验证问题保持待处理。"); }} type="button">拒绝</button>
                      <button disabled={selectedStatus === "resolved"} onClick={() => resolveIssue("approve")} type="button">批准并局部重算</button>
                    </div>
                  </>
                ) : (
                  <div className={styles.inspectorEmpty}><span>PATCH</span><h2>还没有建议补丁</h2><p>先在验证问题中请求 Agent 补丁，系统会展示逐字差异与影响范围。</p><button onClick={requestPatch} type="button">为当前问题生成补丁</button></div>
                )}
              </div>
            ) : null}

            {inspectorTab === "audit" ? (
              realData ? (
                <div className={styles.realEmptyState}><strong>审计记录尚未接入</strong><p>这里不会展示本地构造的操作记录；服务端审计接口接入后再开放。</p></div>
              ) : <div className={styles.auditInspector}>
                <div className={styles.auditStatus}><span>当前修订</span><strong>{seed.caseMeta.revision}</strong><small>只追加记录</small></div>
                <ol>{auditEntries.map((entry) => <li key={entry.id}><time>{entry.time}</time><i aria-hidden="true" /><div><span>{entry.actor}</span><strong>{entry.action}</strong><small>{entry.detail}</small></div></li>)}</ol>
              </div>
            ) : null}
          </div>
          <footer className={styles.inspectorFooter}>
            <div><span>{realData ? "真实验证尚未接入" : validationPhase === "idle" ? "验证器空闲" : validationPhase === "recomputing" ? "局部重算中…" : "全量验证中…"}</span><small>{realData ? "不使用样例结果" : `${unresolvedCount} 个问题待决定`}</small></div>
            <button disabled={realData || validationPhase !== "idle"} onClick={revalidateAll} type="button">重新验证</button>
          </footer>
        </aside>
        {!inspectorOpen ? (
          <button
            aria-label="展开上下文检查器"
            aria-expanded={inspectorOpen}
            className={styles.inspectorRestore}
            onClick={() => {
              setInspectorOpen(true);
              announce("上下文检查器已展开。");
            }}
            type="button"
          >
            <WorkbenchIcon name="chevron" />
            <span>检查器</span>
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
            {realData ? (
              <div className={styles.realEmptyState}><strong>真实来源内容尚未接入</strong><p>当前工作稿仅保留 CaseFile 内的引用关系，不展示样例录音、转写、日志或检索命中。</p></div>
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

      {agentOpen && !realData ? (
        <AgentPanel
          onClose={() => setAgentOpen(false)}
          seed={seed}
          unresolvedCount={unresolvedCount}
        />
      ) : null}
    </div>
  );
}
