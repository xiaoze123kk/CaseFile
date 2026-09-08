"use client";
import { useEffect, useRef, useState } from "react";
import type {
  NovelEditorExchange,
  NovelEditorVersion,
} from "@casefile/contracts";
import type { useNovelEditor } from "./use-novel-editor";
import { novelTextDiff } from "./novel-diff";
import { novelEditorApi as api } from "./novel-editor-api";
import { Dialog } from "./novel-workspace-panels";
import { errorMessage } from "@/lib/api-client";
import styles from "./novel-collaboration.module.css";
type Editor = ReturnType<typeof useNovelEditor>;

export function NovelDiffReview({
  exchange,
  editor,
  focusEdit,
  onClose,
}: {
  exchange: NovelEditorExchange;
  editor: Editor;
  focusEdit?: number;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [undo, setUndo] = useState<{ before: number; after: number } | null>(
    null,
  );
  const lock = useRef(false);
  useEffect(() => {
    if (focusEdit)
      document
        .getElementById(`novel-edit-${focusEdit}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [focusEdit]);
  const pending = exchange.edits.filter((e) => e.status === "pending");
  async function decide(ids: number[], action: "accept" | "reject") {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const base = await editor.flush();
      await editor.decide(exchange.id, ids, action);
      if (action === "accept" && base)
        setUndo({ before: base.revision, after: base.revision + 1 });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <section className={styles.review} aria-label="修改审阅">
      <header>
        <div>
          <small>修改候选 · 不会自动写入正文</small>
          <h2>修改审阅</h2>
        </div>
        <button type="button" onClick={onClose}>
          返回编辑
        </button>
      </header>
      <div className={styles.actions}>
        <div role="group" aria-label="差异显示">
          <button
            type="button"
            aria-pressed={!preview}
            onClick={() => setPreview(false)}
          >
            差异
          </button>
          <button
            type="button"
            aria-pressed={preview}
            onClick={() => setPreview(true)}
          >
            修改后
          </button>
        </div>
        <small>删除 −　新增 ＋</small>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      {exchange.edits.map((edit, index) => (
        <article
          id={`novel-edit-${edit.id}`}
          className={styles.edit}
          key={edit.id}
        >
          <h3>
            修改 {index + 1}{" "}
            <small>
              {edit.status === "accepted"
                ? "已采纳"
                : edit.status === "rejected"
                  ? "已拒绝"
                  : "待审阅"}
            </small>
          </h3>
          <p className={styles.reasonText}>{edit.reason}</p>
          <div className={styles.diff}>
            {preview
              ? edit.after
              : novelTextDiff(edit.before, edit.after).map((part, i) =>
                  part.kind === "same" ? (
                    <span key={i}>{part.text}</span>
                  ) : part.kind === "delete" ? (
                    <del key={i}>
                      <span className={styles.diffMark}>−</span>
                      {part.text}
                    </del>
                  ) : (
                    <ins key={i}>
                      <span className={styles.diffMark}>＋</span>
                      {part.text}
                    </ins>
                  ),
                )}
          </div>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={busy || edit.status !== "pending"}
              onClick={() => void decide([edit.id], "accept")}
            >
              采纳这组
            </button>
            <button
              type="button"
              disabled={busy || edit.status !== "pending"}
              onClick={() => void decide([edit.id], "reject")}
            >
              拒绝这组
            </button>
          </div>
        </article>
      ))}
      <footer className={styles.actions}>
        <button
          type="button"
          disabled={busy || !pending.length}
          onClick={() =>
            void decide(
              pending.map((e) => e.id),
              "accept",
            )
          }
        >
          采纳全部待审阅修改
        </button>
        <button
          type="button"
          disabled={busy || !pending.length}
          onClick={() =>
            void decide(
              pending.map((e) => e.id),
              "reject",
            )
          }
        >
          放弃剩余修改
        </button>
        {undo ? (
          <button
            type="button"
            disabled={busy || editor.view?.revision !== undo.after}
            onClick={() => {
              setBusy(true);
              void editor
                .restore(undo.before)
                .then(() => setUndo(null))
                .catch((e) => setError(errorMessage(e)))
                .finally(() => setBusy(false));
            }}
          >
            撤销本次采纳
          </button>
        ) : null}
      </footer>
      <p>正文变化后，旧候选需重新生成。更早的修改可通过版本记录恢复。</p>
    </section>
  );
}

export function NovelServerHistory({
  project,
  draftId,
  editor,
  onClose,
}: {
  project: number;
  draftId: number;
  editor: Editor;
  onClose: () => void;
}) {
  const [versions, setVersions] = useState<NovelEditorVersion[]>([]),
    [books, setBooks] = useState<{ id: number; title: string }[]>([]),
    [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    const id = editor.view?.id;
    if (id)
      void Promise.all([api.history(project, id), api.list(project, draftId)])
        .then(([v, b]) => {
          if (!disposed) {
            setVersions(v);
            setBooks(b);
          }
        })
        .catch((e) => {
          if (!disposed) setError(errorMessage(e));
        });
    return () => {
      disposed = true;
    };
  }, [project, draftId, editor.view?.id]);
  async function act(work: () => Promise<void>) {
    setBusy(true);
    try {
      await work();
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title="服务器版本记录" onClose={onClose}>
      <div className={styles.history}>
        {error ? <p role="alert">{error}</p> : null}
        <p>恢复会创建一个新版本，原有历史继续保留。</p>
        {versions.map((v) => (
          <div key={v.revision}>
            <span>
              版本 {v.revision} ·{" "}
              {new Date(v.created_at).toLocaleString("zh-CN")} ·{" "}
              {v.reason === "ai_accept"
                ? "采纳 AI 修改"
                : v.reason === "manual"
                  ? "手工编辑"
                  : v.reason === "original"
                    ? "原始稿"
                    : v.reason === "restore"
                      ? "历史恢复"
                      : "导入稿"}
            </span>
            <button
              type="button"
              disabled={busy || v.revision === editor.view?.revision}
              onClick={() => void act(() => editor.restore(v.revision))}
            >
              恢复此版本
            </button>
          </div>
        ))}
        <h3>其他小说稿件</h3>
        {books
          .filter((b) => b.id !== editor.view?.id)
          .map((b) => (
            <button
              type="button"
              disabled={busy}
              key={b.id}
              onClick={() => void act(() => editor.switchNovel(b.id))}
            >
              {b.title} · 稿件 {b.id}
            </button>
          ))}
      </div>
    </Dialog>
  );
}
