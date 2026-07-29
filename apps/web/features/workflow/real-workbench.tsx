"use client";

import {
  useCallback,
  useMemo,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import {
  CaseSpine,
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

export function RealWorkbench() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<WorkbenchSelection | null>(null);
  const [pendingSelection, setPendingSelection] =
    useState<WorkbenchSelection | null>(null);
  const [workspaceMode, setWorkspaceMode] =
    useState<WorkspaceMode>("agent");
  const [editorDirty, setEditorDirty] = useState(false);
  const [focusEvent, setFocusEvent] = useState<WorkbenchObject | null>(null);

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

      <CaseSpine current="draft" />

      <section
        aria-label="CaseFile 卷宗编辑工作台"
        className={styles.workspace}
      >
        <aside className={`paper-panel ${styles.indexPanel}`}>
          <PanelHeader
            code={`${collections.length} 组集合`}
            title="对象索引"
            trailing={<StatusBadge tone="dark">{totalObjects} 个对象</StatusBadge>}
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

        <section className={`paper-panel ${styles.centerPanel}`}>
          <PanelHeader
            code="对话与事实编排"
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
              onClearFocus={() => setFocusEvent(null)}
              onDraftChanged={refreshDraft}
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
