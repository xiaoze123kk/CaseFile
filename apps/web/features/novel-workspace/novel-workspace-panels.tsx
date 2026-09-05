import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  wordCount,
  type NovelChange,
  type NovelChapter,
  type NovelDraft,
} from "./novel-document";
import styles from "./novel-workspace.module.css";

function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      aria-label={title}
      onCancel={onClose}
    >
      <header>
        <h2>{title}</h2>
        <button aria-label="关闭" onClick={onClose} type="button">
          ×
        </button>
      </header>
      {children}
    </dialog>
  );
}

export function NovelImportDialog({
  hasDraft,
  errorMessage,
  onClose,
  onImport,
}: {
  hasDraft: boolean;
  errorMessage?: string;
  onClose: () => void;
  onImport: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [reading, setReading] = useState(false);
  const readId = useRef(0);
  return (
    <Dialog title="导入整篇初稿" onClose={onClose}>
      <p>
        粘贴小说全文，或选择 TXT / Markdown 文件。按“第一章”或 Markdown
        标题自动分章。
      </p>
      <label className={styles.fileInput}>
        选择文件
        <input
          accept=".txt,.md,.markdown,text/plain,text/markdown"
          type="file"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            const current = ++readId.current;
            if (!file) return;
            if (file.size > 4 * 1024 * 1024) {
              setError("文件超过 4 MB，请缩小后重试。");
              return;
            }
            if (!/\.(txt|md|markdown)$/iu.test(file.name)) {
              setError("请选择 TXT 或 Markdown 文件。");
              return;
            }
            setReading(true);
            setError("");
            try {
              const content = await file.text();
              if (current === readId.current) setText(content);
            } catch {
              if (current === readId.current)
                setError("文件读取失败，可以直接粘贴全文。");
            } finally {
              if (current === readId.current) setReading(false);
            }
          }}
        />
      </label>
      <textarea
        aria-label="整篇小说初稿"
        placeholder="第一章 · …\n\n粘贴已经生成的小说全文…"
        value={text}
        disabled={reading}
        onChange={(event) => setText(event.target.value)}
      />
      {hasDraft ? (
        <p>
          导入会打开一份新的编辑稿。当前稿会先在此浏览器中备份；建议同时导出一份文件。
        </p>
      ) : null}
      {error || errorMessage ? (
        <p role="alert" className={styles.error}>
          {error || errorMessage}
        </p>
      ) : null}
      <footer>
        <button onClick={onClose} type="button">
          取消
        </button>
        <button
          className={styles.primary}
          disabled={!text.trim() || reading}
          onClick={() => onImport(text)}
          type="button"
        >
          {reading ? "正在读取…" : "打开初稿"}
        </button>
      </footer>
    </Dialog>
  );
}

export function NovelHistory({
  drafts,
  errorMessage,
  onClose,
  onRestore,
}: {
  drafts: NovelDraft[];
  errorMessage: string;
  onClose: () => void;
  onRestore: (draft: NovelDraft) => void;
}) {
  return (
    <Dialog title="历史编辑稿" onClose={onClose}>
      <p>导入新初稿前会保留当前编辑稿。恢复时也会先备份正在编辑的这一份。</p>
      {drafts.length ? (
        <div className={styles.reviewContent}>
          {drafts.map((draft, index) => (
            <article
              className={styles.historyEntry}
              key={`${draft.original.id}:${draft.revision}:${index}`}
            >
              <div>
                <strong>{draft.original.title}</strong>
                <p>
                  {draft.original.sourceLabel} · {draft.chapters.length} 章 ·{" "}
                  {draft.chapters.reduce(
                    (sum, chapter) => sum + wordCount(chapter.text),
                    0,
                  )}{" "}
                  字
                </p>
                <small>{draft.chapters[0]?.text.slice(0, 90)}</small>
              </div>
              <button onClick={() => onRestore(draft)} type="button">
                恢复此稿
              </button>
            </article>
          ))}
        </div>
      ) : (
        <p>还没有历史稿。导入下一份初稿时，当前稿会保留在这里。</p>
      )}
      {errorMessage ? (
        <p className={styles.error} role="alert">
          {errorMessage}
        </p>
      ) : null}
      <footer>
        <button onClick={onClose} type="button">
          关闭历史稿
        </button>
      </footer>
    </Dialog>
  );
}

export function NovelReview({
  changes,
  chapters,
  onClose,
  onApply,
}: {
  changes: NovelChange[];
  chapters: NovelChapter[];
  onClose: () => void;
  onApply: () => void;
}) {
  return (
    <Dialog title="审阅小说修改" onClose={onClose}>
      <p>以下修改只应用于小说编辑稿，原始初稿保持原样。</p>
      <div className={styles.reviewContent}>
        {changes.map((change, index) => (
          <section key={`${change.chapterId}:${index}`}>
            <h3>
              {chapters.find((chapter) => chapter.id === change.chapterId)
                ?.title ?? "章节已不存在"}
            </h3>
            <div className={styles.comparison}>
              <div>
                <small>修改前</small>
                <p>{change.before}</p>
              </div>
              <div>
                <small>修改后</small>
                <p>{change.after}</p>
              </div>
            </div>
          </section>
        ))}
      </div>
      <footer>
        <button onClick={onClose} type="button">
          暂不采纳
        </button>
        <button className={styles.primary} onClick={onApply} type="button">
          采纳全部修改
        </button>
      </footer>
    </Dialog>
  );
}
