"use client";
import { useMemo, useState } from "react";
import { novelParagraphDiff, novelTextDiff } from "./novel-diff";
import styles from "./novel-collaboration.module.css";

export function NovelParagraphReview({ before, after }: { before: string; after: string }) {
  const [onlyChanges, setOnlyChanges] = useState(true);
  const rows = useMemo(() => novelParagraphDiff(before, after), [before, after]);
  const changed = rows.filter((row) => row.before !== row.after).length;
  return <section aria-label="整章段落差异">
    <p>{changed} 处段落变化。段落用于对照，关联改动仍随整章一起采纳。</p>
    <label><input type="checkbox" checked={onlyChanges}
      onChange={(e) => setOnlyChanges(e.target.checked)} />只看变化段落</label>
    {rows.map((row, index) => onlyChanges && row.before === row.after ? null : <article className={styles.paragraphDiff} key={index}>
      <h4>{row.beforeParagraph ? `原第 ${row.beforeParagraph} 段` : "新增段落"}
        {row.afterParagraph ? ` → 新第 ${row.afterParagraph} 段` : " → 已删除"}</h4>
      <div className={styles.diff}>
        {novelTextDiff(row.before, row.after).map((part, i) => part.kind === "same"
          ? <span key={i}>{part.text}</span> : part.kind === "delete"
            ? <del key={i}><span className={styles.diffMark}>−</span>{part.text}</del>
            : <ins key={i}><span className={styles.diffMark}>＋</span>{part.text}</ins>)}
      </div>
    </article>)}
  </section>;
}
