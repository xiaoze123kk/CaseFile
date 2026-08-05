"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import {
  DocumentHeader,
  PanelHeader,
  StatusBadge,
} from "@/components/archive-ui";
import {
  apiRequest,
  errorMessage,
  type CaseFileObject,
  type DraftView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import { AgentWorkspace } from "./agent-workspace";
import { FactTimeline } from "./fact-timeline";
import { ObjectEditor } from "./object-editor";
import { ObjectTree } from "./object-tree";
import styles from "./real-workbench.module.css";
import {
  allWorkbenchObjects,
  collectionLabel,
  collectionObjects,
  firstWorkbenchSelection,
  selectedWorkbenchObject,
  WORKBENCH_COLLECTIONS,
  type WorkbenchObject,
  type WorkbenchSelection,
} from "./workbench-model";

const collections = WORKBENCH_COLLECTIONS;
type WorkspaceMode = "agent" | "timeline";
type WorkspaceDivider = "index" | "inspector";

export interface WorkbenchPanelWidths {
  index: number;
  inspector: number;
}

const PANEL_WIDTH_STORAGE_KEY = "casefile:workbench:panel-widths:v1";
const PANEL_HANDLE_TOTAL_WIDTH = 22;
const MIN_INDEX_PANEL_WIDTH = 180;
const MIN_CENTER_PANEL_WIDTH = 360;
const MIN_INSPECTOR_PANEL_WIDTH = 260;
const DEFAULT_INDEX_PANEL_RATIO = 0.19;
const DEFAULT_INSPECTOR_PANEL_RATIO = 0.29;

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

export function constrainWorkbenchPanelWidths(
  widths: WorkbenchPanelWidths,
  containerWidth: number,
): WorkbenchPanelWidths {
  const availableWidth = Math.max(
    containerWidth - PANEL_HANDLE_TOTAL_WIDTH,
    MIN_INDEX_PANEL_WIDTH + MIN_CENTER_PANEL_WIDTH + MIN_INSPECTOR_PANEL_WIDTH,
  );
  const index = clamp(
    widths.index,
    MIN_INDEX_PANEL_WIDTH,
    availableWidth - MIN_CENTER_PANEL_WIDTH - MIN_INSPECTOR_PANEL_WIDTH,
  );
  const inspector = clamp(
    widths.inspector,
    MIN_INSPECTOR_PANEL_WIDTH,
    availableWidth - MIN_CENTER_PANEL_WIDTH - index,
  );
  return { index, inspector };
}

export function resizeWorkbenchPanels(
  widths: WorkbenchPanelWidths,
  divider: WorkspaceDivider,
  deltaX: number,
  containerWidth: number,
): WorkbenchPanelWidths {
  const constrained = constrainWorkbenchPanelWidths(widths, containerWidth);
  const availableWidth = Math.max(
    containerWidth - PANEL_HANDLE_TOTAL_WIDTH,
    MIN_INDEX_PANEL_WIDTH + MIN_CENTER_PANEL_WIDTH + MIN_INSPECTOR_PANEL_WIDTH,
  );

  if (divider === "index") {
    return {
      ...constrained,
      index: clamp(
        constrained.index + deltaX,
        MIN_INDEX_PANEL_WIDTH,
        availableWidth - MIN_CENTER_PANEL_WIDTH - constrained.inspector,
      ),
    };
  }

  return {
    ...constrained,
    inspector: clamp(
      constrained.inspector - deltaX,
      MIN_INSPECTOR_PANEL_WIDTH,
      availableWidth - MIN_CENTER_PANEL_WIDTH - constrained.index,
    ),
  };
}

function defaultPanelWidths(containerWidth: number) {
  return constrainWorkbenchPanelWidths(
    {
      index: containerWidth * DEFAULT_INDEX_PANEL_RATIO,
      inspector: containerWidth * DEFAULT_INSPECTOR_PANEL_RATIO,
    },
    containerWidth,
  );
}

export function RealWorkbench() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<WorkbenchSelection | null>(null);
  const [pendingSelection, setPendingSelection] =
    useState<WorkbenchSelection | null>(null);
  const [workspaceMode, setWorkspaceMode] =
    useState<WorkspaceMode>("agent");
  const [agentThreadRailOpen, setAgentThreadRailOpen] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const [focusEvent, setFocusEvent] = useState<WorkbenchObject | null>(null);
  const [panelWidths, setPanelWidths] = useState<WorkbenchPanelWidths>(() =>
    defaultPanelWidths(1200),
  );
  const [activeDivider, setActiveDivider] =
    useState<WorkspaceDivider | null>(null);
  const workspaceRef = useRef<HTMLElement | null>(null);
  const workspaceWidthRef = useRef(1200);
  const panelWidthsRef = useRef(panelWidths);
  const dragRef = useRef<{
    divider: WorkspaceDivider;
    startX: number;
    startWidths: WorkbenchPanelWidths;
  } | null>(null);
  const restoredPanelWidthsRef = useRef(false);

  const updatePanelWidths = useCallback((next: WorkbenchPanelWidths) => {
    panelWidthsRef.current = next;
    setPanelWidths(next);
  }, []);

  const persistPanelWidths = useCallback((next: WorkbenchPanelWidths) => {
    const containerWidth = workspaceWidthRef.current;
    if (containerWidth <= 0) return;
    window.localStorage.setItem(
      PANEL_WIDTH_STORAGE_KEY,
      JSON.stringify({
        index: next.index / containerWidth,
        inspector: next.inspector / containerWidth,
      }),
    );
  }, []);

  useEffect(() => {
    if (!activeDivider) return;

    function move(event: PointerEvent) {
      const drag = dragRef.current;
      if (!drag) return;
      updatePanelWidths(
        resizeWorkbenchPanels(
          drag.startWidths,
          drag.divider,
          event.clientX - drag.startX,
          workspaceWidthRef.current,
        ),
      );
    }

    function finish() {
      if (!dragRef.current) return;
      dragRef.current = null;
      setActiveDivider(null);
      persistPanelWidths(panelWidthsRef.current);
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
  }, [activeDivider, persistPanelWidths, updatePanelWidths]);

  function startPanelResize(
    divider: WorkspaceDivider,
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    dragRef.current = {
      divider,
      startX: event.clientX,
      startWidths: panelWidthsRef.current,
    };
    setActiveDivider(divider);
  }

  function nudgePanelDivider(
    divider: WorkspaceDivider,
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 64 : 20;
    const next = resizeWorkbenchPanels(
      panelWidthsRef.current,
      divider,
      event.key === "ArrowRight" ? step : -step,
      workspaceWidthRef.current,
    );
    updatePanelWidths(next);
    persistPanelWidths(next);
  }

  function resetPanelWidths() {
    const next = defaultPanelWidths(workspaceWidthRef.current);
    updatePanelWidths(next);
    persistPanelWidths(next);
  }

  const draftQuery = useQuery({
    queryKey: ["draft", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<DraftView>(`/projects/${workflow.projectId}/draft`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });

  const patchMutation = useMutation({
    mutationFn: ({
      objectId,
      expectedRevision,
      changes,
    }: {
      objectId: string;
      expectedRevision: number;
      changes: Record<string, unknown>;
    }) =>
      apiRequest<CaseFileObject>(
        `/projects/${workflow.projectId}/draft/objects/${objectId}`,
        {
          actorId: workflow.actorId,
          method: "PATCH",
          body: {
            expected_revision: expectedRevision,
            changes,
          },
        },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["draft", workflow.actorId, workflow.projectId],
      });
    },
  });

  const draft = draftQuery.data;
  const document = draft?.content;
  const effectiveSelection = useMemo(() => {
    if (!document) return null;
    if (selection && selectedWorkbenchObject(document, selection)) {
      return selection;
    }
    return firstWorkbenchSelection(document);
  }, [document, selection]);
  const selectedObject = useMemo(
    () =>
      document
        ? selectedWorkbenchObject(document, effectiveSelection)
        : null,
    [document, effectiveSelection],
  );
  const totalObjects = document
    ? allWorkbenchObjects(document).length
    : 0;
  const eventCount = document
    ? collectionObjects(document, "events").length
    : 0;
  const workspaceStyle = {
    "--index-panel-width": `${panelWidths.index}px`,
    "--inspector-panel-width": `${panelWidths.inspector}px`,
  } as CSSProperties;

  useEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace || !document) return;

    const observer = new ResizeObserver(([entry]) => {
      const containerWidth = entry?.contentRect.width ?? 0;
      if (!containerWidth) return;
      workspaceWidthRef.current = containerWidth;

      if (!restoredPanelWidthsRef.current) {
        restoredPanelWidthsRef.current = true;
        let restored = defaultPanelWidths(containerWidth);
        try {
          const raw = window.localStorage.getItem(PANEL_WIDTH_STORAGE_KEY);
          if (raw) {
            const parsed = JSON.parse(raw) as Partial<WorkbenchPanelWidths>;
            if (
              typeof parsed.index === "number" &&
              Number.isFinite(parsed.index) &&
              typeof parsed.inspector === "number" &&
              Number.isFinite(parsed.inspector)
            ) {
              restored = constrainWorkbenchPanelWidths(
                {
                  index: parsed.index * containerWidth,
                  inspector: parsed.inspector * containerWidth,
                },
                containerWidth,
              );
            }
          }
        } catch {
          window.localStorage.removeItem(PANEL_WIDTH_STORAGE_KEY);
        }
        updatePanelWidths(restored);
        return;
      }

      updatePanelWidths(
        constrainWorkbenchPanelWidths(panelWidthsRef.current, containerWidth),
      );
    });

    observer.observe(workspace);
    return () => observer.disconnect();
  }, [document, updatePanelWidths]);

  const refreshDraft = useCallback(() => {
    void queryClient.invalidateQueries({
      queryKey: ["draft", workflow.actorId, workflow.projectId],
    });
  }, [
    queryClient,
    workflow.actorId,
    workflow.projectId,
  ]);

  function requestSelection(next: WorkbenchSelection) {
    if (
      effectiveSelection?.collection === next.collection &&
      effectiveSelection.objectId === next.objectId
    ) {
      return;
    }
    if (editorDirty) {
      setPendingSelection(next);
      return;
    }
    setSelection(next);
  }

  function discardAndContinue() {
    if (pendingSelection) setSelection(pendingSelection);
    setPendingSelection(null);
    setEditorDirty(false);
  }

  if (!workflow.ready || draftQuery.isLoading) {
    return (
      <main aria-busy="true" className={styles.centerState}>
        <span className={styles.loadingMark} aria-hidden="true" />
        <small>CASEFILE / 工作台</small>
        <strong>正在展开卷宗内容…</strong>
      </main>
    );
  }

  if (draftQuery.isError) {
    return (
      <main className={styles.centerState}>
        <small>CASEFILE / 读取失败</small>
        <strong role="alert">{errorMessage(draftQuery.error)}</strong>
        <button onClick={() => draftQuery.refetch()} type="button">
          重新读取
        </button>
      </main>
    );
  }

  if (workflow.projectId === null || !draft || !document) {
    return (
      <main className={styles.centerState}>
        <small>CASEFILE / 暂无草稿</small>
        <strong>当前还没有可以编辑的卷宗。</strong>
        <button onClick={() => router.push("/brief")} type="button">
          返回创作简报与生成
        </button>
      </main>
    );
  }

  return (
    <main className={`document ${styles.workbenchDocument}`}>
      <DocumentHeader
        action={
          <button
            aria-label="刷新当前卷宗"
            className={styles.refreshButton}
            disabled={draftQuery.isFetching}
            onClick={() => draftQuery.refetch()}
            type="button"
          >
            <span aria-hidden="true">↻</span>
            {draftQuery.isFetching ? "刷新中" : "刷新内容"}
          </button>
        }
        eyebrow="CaseFile · 卷宗编辑部"
        meta={[
          { label: "对象", value: `${totalObjects} 项` },
          { label: "事件", value: `${eventCount} 项` },
          { label: "状态", value: "持续编辑" },
        ]}
        title={document.title}
      />

      <section
        aria-label="CaseFile 卷宗编辑工作台"
        className={`${styles.workspace} ${
          activeDivider ? styles.workspaceResizing : ""
        }`}
        ref={workspaceRef}
        style={workspaceStyle}
      >
        <aside className={`paper-panel ${styles.indexPanel}`}>
          <PanelHeader
            code={`${collections.length} 组集合 · ${totalObjects} 个对象`}
            title="对象索引"
          />
          <ObjectTree
            document={document}
            onSelect={requestSelection}
            selected={effectiveSelection}
          />
          <footer className={styles.indexHint}>
            <span aria-hidden="true">↳</span>
            展开分类，直接选择具体对象
          </footer>
        </aside>

        <div
          aria-label="调整对象索引和协作工作区宽度"
          aria-orientation="vertical"
          aria-valuenow={Math.round(panelWidths.index)}
          aria-valuetext={`对象索引宽 ${Math.round(panelWidths.index)} 像素`}
          className={styles.panelResizeHandle}
          data-active={activeDivider === "index" || undefined}
          onDoubleClick={resetPanelWidths}
          onKeyDown={(event) => nudgePanelDivider("index", event)}
          onPointerDown={(event) => startPanelResize("index", event)}
          role="separator"
          tabIndex={0}
          title="拖动调整宽度，双击恢复默认"
        />

        <section className={`paper-panel ${styles.centerPanel}`}>
          <PanelHeader
            code="对话与事实编排"
            leading={
              workspaceMode === "agent" ? (
                <button
                  aria-controls="agent-workspace-panel"
                  aria-expanded={agentThreadRailOpen}
                  className={styles.panelThreadToggle}
                  onClick={() => setAgentThreadRailOpen((value) => !value)}
                  type="button"
                >
                  <span aria-hidden="true">☷</span>
                  线程
                </button>
              ) : undefined
            }
            title="协作工作区"
            trailing={
              <div
                aria-label="工作区视图"
                className={styles.workspaceTabs}
                role="tablist"
              >
                <button
                  aria-controls="agent-workspace-panel"
                  aria-selected={workspaceMode === "agent"}
                  className={
                    workspaceMode === "agent"
                      ? styles.activeWorkspaceTab
                      : undefined
                  }
                  id="agent-workspace-tab"
                  onClick={() => setWorkspaceMode("agent")}
                  role="tab"
                  type="button"
                >
                  Agent 协作
                </button>
                <button
                  aria-controls="timeline-workspace-panel"
                  aria-selected={workspaceMode === "timeline"}
                  className={
                    workspaceMode === "timeline"
                      ? styles.activeWorkspaceTab
                      : undefined
                  }
                  id="timeline-workspace-tab"
                  onClick={() => setWorkspaceMode("timeline")}
                  role="tab"
                  type="button"
                >
                  事实时间线
                </button>
              </div>
            }
          />

          <div
            aria-labelledby="agent-workspace-tab"
            className={styles.workspacePanel}
            hidden={workspaceMode !== "agent"}
            id="agent-workspace-panel"
            role="tabpanel"
          >
            <AgentWorkspace
              actorId={workflow.actorId}
              currentRevision={draft.revision}
              document={document}
              focusEvent={focusEvent}
              railOpen={agentThreadRailOpen}
              onClearFocus={() => setFocusEvent(null)}
              onDraftChanged={refreshDraft}
              onRailOpenChange={setAgentThreadRailOpen}
              onOpenSelection={(next, preferTimeline) => {
                requestSelection(next);
                if (preferTimeline) setWorkspaceMode("timeline");
              }}
              projectId={workflow.projectId}
              provider={workflow.provider}
            />
          </div>

          <div
            aria-labelledby="timeline-workspace-tab"
            className={styles.workspacePanel}
            hidden={workspaceMode !== "timeline"}
            id="timeline-workspace-panel"
            role="tabpanel"
          >
            <FactTimeline
              events={collectionObjects(document, "events")}
              onDiscuss={(event) => {
                requestSelection({
                  collection: "events",
                  objectId: event.id,
                });
                setFocusEvent(event);
                setWorkspaceMode("agent");
              }}
              onSelect={requestSelection}
              selectedObjectId={
                effectiveSelection?.collection === "events"
                  ? effectiveSelection.objectId
                  : null
              }
            />
          </div>
        </section>

        <div
          aria-label="调整协作工作区和对象编辑器宽度"
          aria-orientation="vertical"
          aria-valuenow={Math.round(panelWidths.inspector)}
          aria-valuetext={`对象编辑器宽 ${Math.round(panelWidths.inspector)} 像素`}
          className={styles.panelResizeHandle}
          data-active={activeDivider === "inspector" || undefined}
          onDoubleClick={resetPanelWidths}
          onKeyDown={(event) => nudgePanelDivider("inspector", event)}
          onPointerDown={(event) => startPanelResize("inspector", event)}
          role="separator"
          tabIndex={0}
          title="拖动调整宽度，双击恢复默认"
        />

        <aside className={`paper-panel ${styles.inspectorPanel}`}>
          <PanelHeader
            code={
              effectiveSelection
                ? collectionLabel(effectiveSelection.collection)
                : "等待选择"
            }
            title="对象编辑器"
            trailing={
              <StatusBadge tone={editorDirty ? "warning" : "neutral"}>
                {editorDirty ? "尚未保存" : "已同步"}
              </StatusBadge>
            }
          />
          {selectedObject && effectiveSelection ? (
            <ObjectEditor
              collection={effectiveSelection.collection}
              document={document}
              draftRevision={draft.revision}
              key={`${effectiveSelection.collection}:${selectedObject.id}`}
              object={selectedObject}
              onDirtyChange={setEditorDirty}
              onSave={async (changes, expectedRevision) => {
                await patchMutation.mutateAsync({
                  changes,
                  expectedRevision,
                  objectId: selectedObject.id,
                });
              }}
            />
          ) : (
            <div className={styles.emptyInspector}>
              <span aria-hidden="true">⌖</span>
              <strong>从左侧选择一个对象</strong>
              <p>这里会显示对应类型的说明和可修改字段。</p>
            </div>
          )}
        </aside>
      </section>

      {pendingSelection ? (
        <div className={styles.confirmBackdrop} role="presentation">
          <section
            aria-labelledby="discard-object-edit-title"
            aria-modal="true"
            className={styles.confirmDialog}
            role="dialog"
          >
            <header>
              <span>未保存修改</span>
              <h2 id="discard-object-edit-title">要离开当前对象吗？</h2>
            </header>
            <p>
              当前对象还有尚未保存的内容。继续切换会放弃这些修改。
            </p>
            <footer>
              <button
                onClick={() => setPendingSelection(null)}
                type="button"
              >
                留在这里
              </button>
              <button
                className={styles.confirmPrimary}
                onClick={discardAndContinue}
                type="button"
              >
                放弃并切换
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </main>
  );
}
