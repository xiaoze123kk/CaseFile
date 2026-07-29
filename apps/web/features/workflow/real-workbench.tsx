"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

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

import styles from "./real-workbench.module.css";

const collections = [
  ["resolution_specs", "结论规格", "结论"],
  ["entities", "实体 / 人物", "实体"],
  ["relationships", "关系", "关系"],
  ["locations", "地点", "地点"],
  ["events", "事件", "事件"],
  ["information_units", "信息单元", "信息"],
  ["claims", "主张", "主张"],
  ["hypotheses", "假设", "假设"],
  ["reasoning_paths", "推理路径", "路径"],
  ["constraints", "约束", "约束"],
  ["structure_locks", "结构锁", "结构"],
] as const;

type EditableKind = "entities" | "locations" | "events";

interface EditState {
  kind: EditableKind;
  object: CaseFileObject;
  headline: string;
  description: string;
}

function objectHeadline(object: CaseFileObject) {
  return String(object.name ?? object.title ?? object.id);
}

function isEditableKind(kind: string): kind is EditableKind {
  return kind === "entities" || kind === "locations" || kind === "events";
}

function editableKindLabel(kind: EditableKind) {
  return {
    entities: "实体",
    locations: "地点",
    events: "事件",
  }[kind];
}

function objectStatus(object: CaseFileObject) {
  const status = String(object.confirmation_status ?? "contract");
  const labels: Record<string, string> = {
    user_confirmed: "作者已确认",
    ai_inferred: "Agent 推断",
    contract: "契约对象",
  };
  return labels[status] ?? "待确认";
}

function compactDescription(object: CaseFileObject) {
  return String(object.description ?? "该对象暂无描述；完整字段仍保留在规范化草稿投影中。");
}

export function RealWorkbench() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [selectedCollection, setSelectedCollection] = useState<string>("resolution_specs");
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [edit, setEdit] = useState<EditState | null>(null);
  const draftQuery = useQuery({
    queryKey: ["draft", workflow.actorId, workflow.projectId],
    queryFn: () =>
      apiRequest<DraftView>(`/projects/${workflow.projectId}/draft`, {
        actorId: workflow.actorId,
      }),
    enabled: workflow.ready && workflow.projectId !== null,
  });

  const patchMutation = useMutation({
    mutationFn: () => {
      if (!edit || !draftQuery.data) throw new Error("编辑上下文已经失效，请重新打开对象。");
      const titleField = edit.kind === "events" ? "title" : "name";
      return apiRequest<CaseFileObject>(
        `/projects/${workflow.projectId}/draft/objects/${edit.object.id}`,
        {
          actorId: workflow.actorId,
          method: "PATCH",
          body: {
            expected_revision: draftQuery.data.revision,
            changes: {
              [titleField]: edit.headline,
              description: edit.description,
            },
          },
        },
      );
    },
    onSuccess: async () => {
      setEdit(null);
      await queryClient.invalidateQueries({
        queryKey: ["draft", workflow.actorId, workflow.projectId],
      });
    },
  });

  const draft = draftQuery.data;
  const document = draft?.content;
  const selected = useMemo(
    () => collections.find(([key]) => key === selectedCollection) ?? collections[0],
    [selectedCollection],
  );
  const values = document ? ((document[selected[0]] ?? []) as CaseFileObject[]) : [];
  const selectedObject =
    values.find((object) => object.id === selectedObjectId) ?? values[0] ?? null;
  const editableKind = isEditableKind(selected[0]) ? selected[0] : null;
  const totalObjects = document
    ? collections.reduce(
        (total, [key]) => total + ((document[key] ?? []) as unknown[]).length,
        0,
      )
    : 0;

  function chooseCollection(key: string) {
    setSelectedCollection(key);
    setSelectedObjectId(null);
  }

  function beginEdit(object: CaseFileObject) {
    if (!editableKind) return;
    patchMutation.reset();
    setEdit({
      kind: editableKind,
      object,
      headline: objectHeadline(object),
      description: String(object.description ?? ""),
    });
  }

  if (!workflow.ready || draftQuery.isLoading) {
    return (
      <main aria-busy="true" className={styles.centerState}>
        <span className={styles.loadingMark} aria-hidden="true" />
        <small>数据库 / 草稿投影</small>
        <strong>正在读取规范化草稿投影…</strong>
      </main>
    );
  }

  if (draftQuery.isError) {
    return (
      <main className={styles.centerState}>
        <small>读取失败 / 草稿投影</small>
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
        <small>CaseFile / 暂无草稿</small>
        <strong>当前没有已生成的 CaseFile。</strong>
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
            aria-label="刷新数据库中的草稿投影"
            className={styles.refreshButton}
            disabled={draftQuery.isFetching}
            onClick={() => draftQuery.refetch()}
            type="button"
          >
            <span aria-hidden="true">↻</span>
            {draftQuery.isFetching ? "刷新中" : "刷新投影"}
          </button>
        }
        eyebrow="CaseFile 工作台 · 实时草稿"
        meta={[
          { label: "卷宗编号", value: document.casefile_id },
          { label: "对象总数", value: String(totalObjects).padStart(2, "0") },
          { label: "草稿版本", value: `版本 ${draft.revision}` },
        ]}
        title={document.title}
      />

      <CaseSpine current="draft" />

      <div className={styles.workspace}>
        <aside className={`paper-panel ${styles.indexPanel}`}>
          <PanelHeader
            code={`${collections.length} 组集合`}
            title="对象索引"
            trailing={<StatusBadge tone="dark">{totalObjects} 个对象</StatusBadge>}
          />
          <nav className={styles.collectionList} aria-label="CaseFile 对象集合">
            {collections.map(([key, label, code], index) => {
              const count = ((document[key] ?? []) as unknown[]).length;
              const active = selected[0] === key;
              return (
                <button
                  aria-current={active ? "page" : undefined}
                  className={active ? styles.activeCollection : undefined}
                  key={key}
                  onClick={() => chooseCollection(key)}
                  type="button"
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <b>{label}</b>
                  <small>{code} · {count}</small>
                </button>
              );
            })}
          </nav>
          <footer className={styles.indexFooter}>
            <span>PostgreSQL / 当前状态</span>
            <b>实时</b>
          </footer>
        </aside>

        <section className={`paper-panel ${styles.archivePanel}`}>
          <PanelHeader
            code={`${selected[2]} / ${String(values.length).padStart(2, "0")}`}
            title={`${selected[1]}档案`}
            trailing={
              <StatusBadge tone={editableKind ? "red" : "neutral"}>
                {editableKind ? "有限字段可编辑" : "契约只读"}
              </StatusBadge>
            }
          />
          <div className={styles.archiveToolbar}>
            <div>
              <i aria-hidden="true" />
              <span>数据库实时投影</span>
              <small>所有结构均来自当前草稿，不使用前端样例数据</small>
            </div>
            <span>契约版本 {document.schema_version}</span>
          </div>

          {values.length ? (
            <div className={styles.objectGrid}>
              {values.map((object, index) => {
                const active = selectedObject?.id === object.id;
                return (
                  <article
                    className={`${styles.objectCard} ${active ? styles.activeObject : ""}`}
                    key={object.id}
                  >
                    <button
                      aria-pressed={active}
                      className={styles.objectSelect}
                      onClick={() => setSelectedObjectId(object.id)}
                      type="button"
                    >
                      <span className={styles.cardSequence}>
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <span className={styles.cardIdentity}>
                        <small>{object.id}</small>
                        <strong>{objectHeadline(object)}</strong>
                      </span>
                      <StatusBadge tone={active ? "red" : "neutral"}>
                        {objectStatus(object)}
                      </StatusBadge>
                      <p>{compactDescription(object)}</p>
                    </button>
                    <footer>
                      <details>
                        <summary>查看完整 JSON</summary>
                        <pre>{JSON.stringify(object, null, 2)}</pre>
                      </details>
                      {editableKind ? (
                        <button onClick={() => beginEdit(object)} type="button">
                          编辑允许字段 ↗
                        </button>
                      ) : (
                        <small>校验器 / Agent 维护</small>
                      )}
                    </footer>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className={styles.emptyCollection}>
              <span aria-hidden="true">＋</span>
              <strong>当前集合尚无对象</strong>
              <p>该集合仍属于正式 CaseFile v1 投影，生成或后续审批后会在这里出现。</p>
            </div>
          )}
        </section>

        <aside className={`paper-panel ${styles.inspectorPanel}`}>
          <PanelHeader
            code="对象 / 检查器"
            title="对象检查器"
            trailing={
              <StatusBadge tone={selectedObject ? "dark" : "neutral"}>
                {selectedObject ? "已定位" : "待选择"}
              </StatusBadge>
            }
          />
          {selectedObject ? (
            <div className={styles.inspectorBody}>
              <header>
                <span>{selectedObject.id}</span>
                <small>{selected[2]} / 当前版本</small>
                <h2>{objectHeadline(selectedObject)}</h2>
                <StatusBadge tone="neutral">{objectStatus(selectedObject)}</StatusBadge>
              </header>
              <section>
                <span className={styles.fieldLabel}>对象说明</span>
                <p>{compactDescription(selectedObject)}</p>
              </section>
              <section className={styles.guardRail}>
                <span aria-hidden="true">◎</span>
                <div>
                  <b>{editableKind ? "允许修改名称与描述" : "当前对象保持只读"}</b>
                  <small>
                    {editableKind
                      ? `保存采用草稿版本 ${draft.revision} 乐观锁`
                      : "引用、ID 与契约字段由校验器和 Agent 流程维护"}
                  </small>
                </div>
              </section>
              <details className={styles.inspectorJson}>
                <summary>完整对象结构</summary>
                <pre>{JSON.stringify(selectedObject, null, 2)}</pre>
              </details>
              {editableKind ? (
                <button
                  className={styles.inspectorEdit}
                  onClick={() => beginEdit(selectedObject)}
                  type="button"
                >
                  <span>打开有限编辑</span>
                  <b>打开编辑 →</b>
                </button>
              ) : (
                <div className={styles.readOnlyStamp}>
                  <span>只读</span>
                  <small>本期不允许直接变更</small>
                </div>
              )}
            </div>
          ) : (
            <div className={styles.emptyInspector}>
              <span aria-hidden="true">⌖</span>
              <strong>尚未定位对象</strong>
              <p>从中间档案区选择对象，即可查看完整结构与编辑权限。</p>
            </div>
          )}
        </aside>
      </div>

      <footer className={styles.documentFooter}>
        <span><b>真实状态：</b>规范化草稿已读取</span>
        <span>数据来源：PostgreSQL</span>
        <span>预期版本 / {draft.revision}</span>
        <span>CaseFile / 真实数据 V1</span>
      </footer>

      {edit ? (
        <div className={styles.modalBackdrop} onMouseDown={() => setEdit(null)} role="presentation">
          <form
            aria-labelledby="real-object-edit-title"
            aria-modal="true"
            className={styles.editDialog}
            onMouseDown={(event) => event.stopPropagation()}
            onSubmit={(event) => {
              event.preventDefault();
              patchMutation.mutate();
            }}
            role="dialog"
          >
            <header className={styles.dialogHeader}>
              <div>
                <small>{editableKindLabel(edit.kind)} / {edit.object.id}</small>
                <h2 id="real-object-edit-title">有限字段编辑</h2>
              </div>
              <button aria-label="关闭编辑对话框" onClick={() => setEdit(null)} type="button">
                ×
              </button>
            </header>
            <div className={styles.revisionNotice}>
              <span>版本保护</span>
              <b>预期版本 {draft.revision}</b>
              <p>ID、引用和未列出的契约字段保持只读；保存成功后版本号自动 +1。</p>
            </div>
            <label>
              <span>{edit.kind === "events" ? "标题" : "名称"}</span>
              <input
                autoFocus
                onChange={(event) =>
                  setEdit((current) => current && { ...current, headline: event.target.value })
                }
                required
                value={edit.headline}
              />
            </label>
            <label>
              <span>描述</span>
              <textarea
                onChange={(event) =>
                  setEdit((current) => current && { ...current, description: event.target.value })
                }
                rows={7}
                value={edit.description}
              />
            </label>
            {patchMutation.isError ? (
              <p className={styles.formError} role="alert">{errorMessage(patchMutation.error)}</p>
            ) : null}
            <div className={styles.dialogActions}>
              <button
                className={styles.secondaryButton}
                onClick={() => setEdit(null)}
                type="button"
              >
                取消
              </button>
              <button
                className={styles.primaryButton}
                disabled={patchMutation.isPending}
                type="submit"
              >
                {patchMutation.isPending ? "保存中…" : "保存并校验"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}
