"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import {
  apiRequest,
  errorMessage,
  type CaseFileObject,
  type DraftView,
} from "@/lib/api-client";
import { useWorkflowSession } from "@/store/workflow-store";

import styles from "./workflow.module.css";

const collections = [
  ["resolution_specs", "结论规格"],
  ["entities", "实体 / 人物"],
  ["relationships", "关系"],
  ["locations", "地点"],
  ["events", "事件"],
  ["information_units", "信息单元"],
  ["claims", "主张"],
  ["hypotheses", "假设"],
  ["reasoning_paths", "推理路径"],
  ["phases", "叙事阶段"],
  ["constraints", "约束"],
  ["structure_locks", "结构锁"],
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

export function RealWorkbench() {
  const router = useRouter();
  const workflow = useWorkflowSession();
  const queryClient = useQueryClient();
  const [selectedCollection, setSelectedCollection] = useState<string>("resolution_specs");
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

  const document = draftQuery.data?.content;
  const selected = useMemo(
    () => collections.find(([key]) => key === selectedCollection) ?? collections[0],
    [selectedCollection],
  );
  const values = document ? ((document[selected[0]] ?? []) as CaseFileObject[]) : [];
  const editableKind = isEditableKind(selected[0]) ? selected[0] : null;

  if (!workflow.ready || draftQuery.isLoading) {
    return <main className={styles.centerState}>正在读取规范化 Draft 投影…</main>;
  }
  if (workflow.projectId === null || !document) {
    return (
      <main className={styles.centerState}>
        <p>当前没有已生成的 CaseFile。</p>
        <button className={styles.primaryButton} onClick={() => router.push("/brief")} type="button">
          返回 Brief 与生成
        </button>
      </main>
    );
  }

  return (
    <main className={styles.workbench}>
      <header className={styles.workbenchHeader}>
        <div>
          <small>CASEFILE V{document.schema_version} / DRAFT REV.{draftQuery.data?.revision}</small>
          <h1>{document.title}</h1>
          <p>{document.casefile_id}</p>
        </div>
        <div>
          <span className={styles.liveBadge}>数据库实时投影</span>
          <button onClick={() => draftQuery.refetch()} type="button">刷新</button>
        </div>
      </header>

      <div className={styles.workbenchBody}>
        <nav className={styles.collectionNav} aria-label="CaseFile 对象集合">
          <header>
            <span>对象索引</span>
            <small>12 COLLECTIONS</small>
          </header>
          {collections.map(([key, label], index) => {
            const count = ((document[key] ?? []) as unknown[]).length;
            return (
              <button
                className={selected[0] === key ? styles.activeCollection : undefined}
                key={key}
                onClick={() => setSelectedCollection(key)}
                type="button"
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <b>{label}</b>
                <small>{count}</small>
              </button>
            );
          })}
        </nav>

        <section className={styles.objectDesk}>
          <header>
            <div>
              <small>{selected[0].toUpperCase()}</small>
              <h2>{selected[1]}</h2>
            </div>
            <span>{editableKind ? "有限字段可编辑" : "只读契约对象"}</span>
          </header>
          {values.length ? (
            <div className={styles.objectGrid}>
              {values.map((object) => (
                <article className={styles.objectCard} key={object.id}>
                  <header>
                    <div>
                      <small>{object.id}</small>
                      <h3>{objectHeadline(object)}</h3>
                    </div>
                    <span>{String(object.confirmation_status ?? "contract")}</span>
                  </header>
                  {object.description ? <p>{String(object.description)}</p> : null}
                  <details>
                    <summary>查看完整结构</summary>
                    <pre>{JSON.stringify(object, null, 2)}</pre>
                  </details>
                  {editableKind ? (
                    <button
                      onClick={() =>
                        setEdit({
                          kind: editableKind,
                          object,
                          headline: objectHeadline(object),
                          description: String(object.description ?? ""),
                        })
                      }
                      type="button"
                    >
                      编辑允许字段
                    </button>
                  ) : (
                    <small className={styles.readOnlyNote}>本期只读 · 由 Validator 与后续 Agent 流程维护</small>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <div className={styles.emptyCollection}>此集合当前为空，但仍属于正式 CaseFile v1 投影。</div>
          )}
        </section>
      </div>

      {edit ? (
        <div className={styles.modalBackdrop} onMouseDown={() => setEdit(null)} role="presentation">
          <form
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
                <small>{edit.kind.toUpperCase()} / {edit.object.id}</small>
                <h2>编辑对象</h2>
              </div>
              <button onClick={() => setEdit(null)} type="button">关闭</button>
            </header>
            <label>
              <span>{edit.kind === "events" ? "标题" : "名称"}</span>
              <input
                onChange={(event) => setEdit((current) => current && { ...current, headline: event.target.value })}
                required
                value={edit.headline}
              />
            </label>
            <label>
              <span>描述</span>
              <textarea
                onChange={(event) => setEdit((current) => current && { ...current, description: event.target.value })}
                rows={6}
                value={edit.description}
              />
            </label>
            <p>采用 Draft revision 乐观锁；保存后 revision 自动 +1。ID、引用与其他字段保持只读。</p>
            {patchMutation.isError ? (
              <p className={styles.formError}>{errorMessage(patchMutation.error)}</p>
            ) : null}
            <div className={styles.dialogActions}>
              <button className={styles.secondaryButton} onClick={() => setEdit(null)} type="button">取消</button>
              <button className={styles.primaryButton} disabled={patchMutation.isPending} type="submit">
                {patchMutation.isPending ? "保存中…" : "保存并校验"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </main>
  );
}
