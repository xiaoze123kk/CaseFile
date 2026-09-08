"use client";
import { useEffect, useState } from "react";
import type { NovelEditorVersionDetail } from "@casefile/contracts";
import { errorMessage } from "@/lib/api-client";
import { novelEditorApi as api } from "./novel-editor-api";
import { novelTextDiff } from "./novel-diff";
import { wordCount } from "./novel-document";
import styles from "./novel-collaboration.module.css";

export function NovelVersionPreview({ project, manuscript, revision, versionNumber = revision }: {
  project: number; manuscript: number; revision: number; versionNumber?: number;
}) {
  const [detail, setDetail] = useState<NovelEditorVersionDetail | null>(null);
  const [error, setError] = useState("");
  const [chapterId, setChapterId] = useState("");
  const [diff, setDiff] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let disposed = false;
    void api.version(project, manuscript, revision).then((value) => {
      if (disposed) return;
      setDetail(value);
      setChapterId(value.chapters.find((c) => {
        const old = value.previous_chapters.find((p) => p.id === c.id);
        return !old || old.text !== c.text || old.title !== c.title;
      })?.id ?? value.chapters[0]?.id ?? value.previous_chapters[0]?.id ?? "");
    }).catch((cause) => { if (!disposed) setError(errorMessage(cause)); });
    return () => { disposed = true; };
  }, [project, manuscript, revision, attempt]);
  if (error) return <section className={styles.versionPreview}>
    <p role="alert">版本内容加载失败：{error}</p>
    <button type="button" onClick={() => { setError(""); setAttempt((n) => n + 1); }}>重试预览</button>
  </section>;
  if (!detail) return <p role="status">正在加载版本 {versionNumber} 的正文…</p>;
  const chapters = [...detail.chapters, ...detail.previous_chapters.filter(
    (p) => !detail.chapters.some((c) => c.id === p.id),
  )];
  const changed = chapters.filter((c) => {
    const currentIndex = detail.chapters.findIndex((v) => v.id === c.id);
    const oldIndex = detail.previous_chapters.findIndex((v) => v.id === c.id);
    const current = detail.chapters[currentIndex], old = detail.previous_chapters[oldIndex];
    return !current || !old || current.text !== old.text || current.title !== old.title || currentIndex !== oldIndex;
  });
  const current = detail.chapters.find((c) => c.id === chapterId);
  const old = detail.previous_chapters.find((c) => c.id === chapterId);
  return <section className={styles.versionPreview} aria-label={`版本 ${versionNumber} 正文预览`}>
    <h3>版本 {versionNumber} · {detail.title}</h3>
    <p>{detail.chapters.length} 章 · {detail.chapters.reduce((n, c) => n + wordCount(c.text), 0).toLocaleString()} 字</p>
    <p>{versionNumber === 1 ? "最初保存的正文" : `相较版本 ${versionNumber - 1}，${changed.length} 章有变化`}</p>
    {versionNumber > 1 && detail.previous_title !== detail.title ? <p>书名：{detail.previous_title} → {detail.title}</p> : null}
    <label>预览章节
      <select aria-label="预览章节" value={chapterId} onChange={(e) => setChapterId(e.target.value)}>
        {chapters.map((c) => <option key={c.id} value={c.id}>{c.title}{
          !detail.chapters.some((v) => v.id === c.id) ? " · 已删除"
          : versionNumber > 1 && changed.some((v) => v.id === c.id) ? " · 有变化" : ""
        }</option>)}
      </select>
    </label>
    <div className={styles.actions} role="group" aria-label="版本预览方式">
      <button type="button" aria-pressed={!diff} onClick={() => setDiff(false)}>此版本正文</button>
      {versionNumber > 1 ? <button type="button" aria-pressed={diff} onClick={() => setDiff(true)}>与上一版对比</button> : null}
    </div>
    {diff ? <p>红色划线为删除，绿色为新增。</p> : null}
    <div className={styles.versionText}>
      {diff ? novelTextDiff(old?.text ?? "", current?.text ?? "").map((part, i) =>
        part.kind === "same" ? <span key={i}>{part.text}</span>
        : part.kind === "delete" ? <del key={i}>{part.text}</del>
        : <ins key={i}>{part.text}</ins>,
      ) : current ? current.text || "该章节暂无正文。" : "此章节在该版本中已删除。"}
    </div>
  </section>;
}
