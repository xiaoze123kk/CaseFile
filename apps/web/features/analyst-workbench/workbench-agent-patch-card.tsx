import { useState, type ReactNode } from "react";
import type { PublicPatchChange, PublicPatchSet } from "@casefile/contracts";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-agent-patch-card.module.css";

export const patchStatusLabels: Record<PublicPatchSet["status"], string> = {
  pending: "需你审阅", stale: "已失效", applied: "已应用", undone: "已撤销", rejected: "已拒绝",
};

const relationshipLabels = {
  requested: "你要求的修改",
  consistency_support: "为保持一致性同步调整",
};

const legacyGenericReasons = new Set([
  "这是你要求新增的卷宗内容。", "这是你要求删除的卷宗内容。", "这是你要求调整的卷宗内容。",
  "为保持卷宗前后一致，需要同步调整这项内容。",
]);

export function patchChangeExplanation(explanation: string) {
  return !explanation.trim() || legacyGenericReasons.has(explanation.trim())
    ? "这项修改未提供具体原因，请让 Agent 补充依据后再决定是否应用。"
    : explanation;
}

/** Group by identity, never by display name; anonymous new objects stay separate. */
export function groupPatchChanges(changes: PublicPatchChange[]) {
  const groups = new Map<string, { target: PublicPatchChange["target"]; changes: PublicPatchChange[] }>();
  for (const change of changes) {
    const key = change.target.target_id === null ? `new:${change.change_id}` : `id:${change.target.target_id}`;
    const group = groups.get(key);
    if (group) group.changes.push(change);
    else groups.set(key, { target: change.target, changes: [change] });
  }
  return [...groups.values()];
}

export function AgentPatchCard({ patchSet, children, selectedIds = [], onToggle, busy = false, onLocateObject, onDetails }: {
  patchSet: PublicPatchSet;
  children?: ReactNode;
  selectedIds?: number[];
  onToggle?: (id: number) => void;
  busy?: boolean;
  onLocateObject?: (id: string) => void;
  onDetails?: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const groups = groupPatchChanges(patchSet.changes);
  const large = patchSet.changes.length > 8;
  const visibleChanges = large && !showAll ? patchSet.changes.slice(0, 6) : patchSet.changes;
  const visibleGroups = groupPatchChanges(visibleChanges);
  const counts = { create: 0, update: 0, delete: 0 };
  for (const change of patchSet.changes) counts[change.kind]++;

  return <article aria-label={`修改建议：${patchSet.title}`} className={styles.card} data-status={patchSet.status}>
    <header className={styles.header}>
      <span className={styles.documentMark}><WorkbenchIcon name="document" /></span>
      <h3>{patchSet.title}</h3>
      <span className={styles.count}>{patchSet.changes.length} 项修改</span>
      <span className={styles.status} role="status">{patchStatusLabels[patchSet.status]}</span>
    </header>
    <p className={styles.summary}>{patchSet.summary}</p>
    <div className={styles.tags} aria-label="修改范围">
      <span><WorkbenchIcon name="archive" />{groups.length} 个对象</span>
      {counts.create > 0 ? <span>新增 {counts.create} 项</span> : null}
      {counts.update > 0 ? <span>调整 {counts.update} 项</span> : null}
      {counts.delete > 0 ? <span className={styles.deletion}>删除 {counts.delete} 项</span> : null}
      <span>{patchSet.review_rule === "atomic" ? "整组应用" : "可逐项选择"}</span>
    </div>
    {patchSet.status === "stale" ? <p className={styles.notice}>当前卷宗已经变化，请重新生成并审阅修改建议。</p> : null}
    <div className={styles.groups}>
      {visibleGroups.map(({ target, changes }) => <section className={styles.group} key={changes[0]!.change_id}>
        <header className={styles.targetHeader}>
          <span className={styles.targetMark} aria-hidden="true">{Array.from(target.name)[0] ?? "卷"}</span>
          <h4>{target.name}</h4><span className={styles.targetType}>{target.type_label}</span>
          <span className={styles.targetCount}>{changes.length} 项{large && !showAll ? "展示" : "修改"}</span>
          {target.target_id !== null && onLocateObject && changes.some((change) => change.kind !== "create") ? <button
            className={styles.locate} type="button" onClick={() => onLocateObject(target.target_id!)}
            aria-label={`在工作台定位：${target.name}`}>查看对象 <WorkbenchIcon name="chevron-right" /></button> : null}
        </header>
        <div className={styles.changes}>
          {changes.map((change) => <PatchChange key={change.change_id} change={change} selected={selectedIds.includes(change.change_id)}
            onToggle={onToggle ? () => onToggle(change.change_id) : undefined} busy={busy || patchSet.status !== "pending"} />)}
        </div>
      </section>)}
    </div>
    {large ? <button className={styles.expand} type="button" aria-expanded={showAll} onClick={() => setShowAll(!showAll)}>
      {showAll ? "收起修改清单" : `展开全部 ${patchSet.changes.length} 项修改（还有 ${patchSet.changes.length - visibleChanges.length} 项）`}
    </button> : null}
    <p className={styles.impact}>{patchSet.impact.summary}</p>
    {children}
    {!children && onDetails ? <footer className={styles.actions}><button type="button" onClick={onDetails}>查看细节 <WorkbenchIcon name="chevron-right" /></button></footer> : null}
  </article>;
}

function PatchChange({ change, selected, onToggle, busy }: { change: PublicPatchChange; selected: boolean; onToggle?: () => void; busy: boolean }) {
  return <div className={styles.change} data-kind={change.kind}>
    <div className={styles.changeLabel}>
      {onToggle ? <input type="checkbox" checked={selected} onChange={onToggle} disabled={busy}
        aria-label={`选择修改 ${change.target.name}${change.kind === "update" ? ` ${change.field_label}` : ""}`} /> : null}
      <strong>{change.kind === "update" ? change.field_label : change.kind === "create" ? "新增对象" : "删除对象"}</strong>
      <span data-support={change.relationship === "consistency_support" || undefined}>{relationshipLabels[change.relationship]}</span>
    </div>
    <div className={styles.values}>
      <ValueLine tone="before" label={change.kind === "create" ? "新增前" : change.kind === "delete" ? "删除前" : "修改前"}
        text={change.kind === "create" ? "尚无此对象" : change.before.text} />
      <span className={styles.arrow} aria-hidden="true">→</span>
      <ValueLine tone="after" label={change.kind === "delete" ? "删除后" : change.kind === "create" ? "新增后" : "修改后"}
        text={change.kind === "delete" ? "移除此对象" : change.after.text} />
    </div>
    <details className={styles.reason}><summary>修改依据</summary><p>{patchChangeExplanation(change.explanation)}</p></details>
  </div>;
}

function ValueLine({ tone, label, text }: { tone: string; label: string; text: string }) {
  return <div className={styles.value} data-tone={tone}><span className={styles.valueLabel}>{label}</span><span>{text || "（空）"}</span></div>;
}
