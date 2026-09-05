import type { CaseFileDocument } from "@/lib/api-client";
import type { ContextItem } from "./workbench-agent-context";
import type { CollaborationDetail } from "./workbench-collaboration-state";
import { findWorkbenchDetailObject } from "./workbench-object-detail-model";
import { creatorText, objectSubtypeLabel } from "./workbench-presenters";
import type { ContextRelation } from "./workbench-relation-model";
import { describeRelation } from "./workbench-relation-overview";
import { RelationFlow, RelationObjectMark } from "./workbench-relation-visual";
import styles from "./workbench-relation-detail.module.css";

export function WorkbenchRelationDetail({ relation, document, onLocate, onOpenDetail, onAddContext }: {
  relation: ContextRelation;
  document: CaseFileDocument;
  onLocate: (id: string) => void;
  onOpenDetail: (detail: CollaborationDetail) => void;
  onAddContext: (items: ContextItem[]) => void;
}) {
  const overview = describeRelation(relation);
  const cognition = relation.fieldLabel === "认知时点";
  const title = cognition ? "认知时点" : creatorText(relation.verb, "关联");
  const direction = cognition ? "认知记录" : relation.arrow === "⇄" ? "双向关系" : relation.arrow === "—" ? "无向关系" : "单向关系";
  const declared = document.relationships.find((item) => `relationship:${item.id}` === relation.id);
  const description = creatorText(declared?.description, "");
  const endpoints = [relation.subject, relation.object];
  const sentence = cognition
    ? `${relation.subject.label}的认知记录，截至${relation.object.label}`
    : relation.arrow === "→"
      ? `${relation.subject.label} ${title} ${relation.object.label}`
      : `${relation.subject.label}与${relation.object.label}：${title}`;
  const context = endpoints.filter((item) => !item.missing).map((item): ContextItem => ({
    kind: item.objectType === "event" ? "event" : "object", id: item.id, label: item.label,
  }));

  return <div className={styles.relationDetail}>
    <header className={styles.sheetHeader}>
      <span aria-hidden="true" className={styles.relationSeal}>关</span>
      <div><p>关系档案 · {direction}</p><h3>{title}</h3></div>
    </header>
    {description ? <p className={styles.description}>{description}</p> : null}
    <figure className={styles.directionFigure} aria-label={sentence}>
      <RelationFlow flow={{ ...overview.flow, label: title }} />
    </figure>

    <section className={styles.objects} aria-label="关联对象">
      <h4>关联对象</h4>
      {endpoints.map((endpoint, index) => {
        const selected = findWorkbenchDetailObject(document, endpoint.id);
        const record = selected?.object as Record<string, unknown> | undefined;
        const kind = typeof record?.entity_type === "string" ? objectSubtypeLabel(record.entity_type) : endpoint.kindLabel;
        const summary = creatorText(typeof record?.description === "string" ? record.description : "", "");
        return <article key={`${endpoint.id}:${index}`} className={styles.objectEntry}>
          <div className={styles.objectIdentity}>
            <RelationObjectMark label={endpoint.label} objectType={endpoint.objectType} />
            <div><small>{kind}</small><h5>{endpoint.label}</h5></div>
          </div>
          {summary ? <p>{summary}</p> : null}
          {endpoint.missing ? <p className={styles.missing}>此对象已不在当前工作稿中。</p> : null}
          <div className={styles.objectActions}>
            <button aria-label={`打开对象 ${endpoint.label}`} type="button" disabled={endpoint.missing || !endpoint.selectable} onClick={() => onLocate(endpoint.id)}>查看对象 <span aria-hidden="true">↗</span></button>
            <button aria-label={`查看来源 ${endpoint.label}`} type="button" disabled={endpoint.missing || !selected} onClick={() => onOpenDetail({ kind: "provenance", objectId: endpoint.id })}>查看来源</button>
          </div>
        </article>;
      })}
    </section>
    <footer className={styles.sheetFooter}>
      <button type="button" disabled={!context.length} onClick={() => onAddContext(context)}>添加到问题 <span aria-hidden="true">＋</span></button>
    </footer>
  </div>;
}
