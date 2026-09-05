"use client";

import type { CaseFileDocument } from "@/lib/api-client";
import { useMemo } from "react";

import {
  objectKindLabels,
  type CaseObject,
  type TimelineEvent,
} from "./analyst-fixture";
import { buildContextInspectorModel } from "./workbench-context-inspector-model";
import type { ContextRelationModel } from "./workbench-relation-model";
import { buildRelationOverview, type RelationOverviewItem } from "./workbench-relation-overview";
import { RelationFlow, RelationObjectMark } from "./workbench-relation-visual";
import type { WorkbenchContextState } from "./workbench-context-panels";
import { WorkbenchObjectEditor } from "./workbench-object-editor";
import type { ObjectSaveResult } from "./workbench-object-persistence";
import { formatCaseWallClock } from "./workbench-presenters";
import styles from "./workbench-context-inspector.module.css";

export function WorkbenchContextInspector({
  document,
  selectedObjectId,
  selectedObject,
  relatedEvents,
  contextState,
  writeLocked,
  revision,
  revisionLabel,
  saving,
  navigationNotice,
  readOnly,
  readOnlyReason,
  onDirtyChange,
  onSave,
  onSelectObject,
  onSelectRelatedEvent,
  onOpenRelation,
}: {
  document: CaseFileDocument | null;
  selectedObjectId: string | null;
  selectedObject: CaseObject | null;
  relatedEvents: TimelineEvent[];
  contextState: WorkbenchContextState;
  writeLocked: boolean;
  revision: number;
  revisionLabel?: string;
  saving: boolean;
  navigationNotice: string | null;
  readOnly: boolean;
  readOnlyReason?: string;
  onDirtyChange: (dirty: boolean) => void;
  onSave?: (objectId: string, changes: Record<string, unknown>) => Promise<ObjectSaveResult>;
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
  onOpenRelation?: (relationId: string) => void;
}) {
  const model = useMemo(
    () =>
      document
        ? buildContextInspectorModel(document, selectedObjectId, contextState.data)
        : null,
    [document, selectedObjectId, contextState.data],
  );

  if (!selectedObjectId) {
    return (
      <div className={styles.contextInspector}>
        <div className={styles.contextEmptyState}>
          <span aria-hidden="true">CTX</span>
          <strong>选择一个对象开始查看上下文</strong>
          <p>
            从左侧目录选择人物、信息、事件、地点或假设后，这里会说明它是什么，
            以及与哪些关键对象有关。
          </p>
        </div>
      </div>
    );
  }

  const relations = (
    <ContextOverviewRelations
      key={selectedObjectId}
      modelRelations={model?.relations ?? null}
      onSelectObject={onSelectObject}
      onSelectRelatedEvent={onSelectRelatedEvent}
      relatedEvents={relatedEvents}
      onOpenRelation={onOpenRelation}
    />
  );

  return (
    <div className={styles.contextInspector} data-agent-object-id={selectedObjectId}>
      <div className={styles.contextPanel}>
        {document ? (
          <WorkbenchObjectEditor
            document={document}
            fieldCitations={writeLocked ? [] : model?.provenance.citations ?? []}
            key={selectedObjectId}
            navigationNotice={navigationNotice}
            onDirtyChange={onDirtyChange}
            onSave={onSave}
            onSelectObject={onSelectObject}
            readOnly={readOnly}
            readOnlyReason={readOnlyReason}
            revision={revision}
            revisionLabel={revisionLabel}
            saving={saving}
            selectedObjectId={selectedObjectId}
            relatedContent={relations}
            relationCount={model?.relations.totals.all}
          />
        ) : (
          <><FixtureObjectContext selectedObject={selectedObject} />{relations}</>
        )}
      </div>
    </div>
  );
}
function ContextOverviewRelations({
  modelRelations,
  relatedEvents,
  onSelectObject,
  onSelectRelatedEvent,
  onOpenRelation,
}: {
  modelRelations: ContextRelationModel | null;
  relatedEvents: TimelineEvent[];
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
  onOpenRelation?: (relationId: string) => void;
}) {
  const overview = modelRelations ? buildRelationOverview(modelRelations) : null;
  const preview = overview?.preview ?? [];
  const total = overview?.total ?? relatedEvents.length;
  const countLabel = !total
    ? "暂无关联"
    : `显示 ${preview.length || Math.min(relatedEvents.length, 4)} 条 · 共 ${total} 项`;

  function renderRelations(items: RelationOverviewItem[]) {
    return <ol className={styles.overviewRelationList}>
      {items.map((item) => <OverviewRelationItem
        key={item.relation.id}
        item={item}
        onSelectObject={onSelectObject}
        onSelectRelatedEvent={onSelectRelatedEvent}
        onOpenRelation={onOpenRelation}
      />)}
    </ol>;
  }

  return (
    <section aria-label="关键关联" className={styles.overviewRelations}>
      <span aria-hidden="true" className={styles.sectionOrnament}>✦</span>
      <header>
        <h3>关键关联</h3>
        <small>{countLabel}</small>
      </header>
      {preview.length ? (
        renderRelations(preview)
      ) : !modelRelations && relatedEvents.length ? (
        <ol className={styles.overviewRelationList}>
          {relatedEvents.slice(0, 4).map((event) => (
            <li key={event.id}>
              <button className={styles.overviewFixtureEvent} onClick={() => onSelectRelatedEvent(event.id)} type="button">
                <strong>{event.label}</strong>
                <small>{formatCaseWallClock(event.time)} · 事件</small>
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className={styles.contextEmpty}>当前对象没有关联对象或事件。</p>
      )}
    </section>
  );
}

function FixtureObjectContext({
  selectedObject,
}: {
  selectedObject: CaseObject | null;
}) {
  if (!selectedObject) {
    return (
      <div className={styles.contextEmptyState}>
        <strong>本地样例未提供该对象</strong>
        <p>采用真实候选后，这里会显示服务端工作稿的对象上下文。</p>
      </div>
    );
  }
  const kindLabel = objectKindLabels[selectedObject.kind];
  return (
    <section aria-label="对象上下文（本地样例）" className={styles.fixtureContext}>
      <header className={styles.fixtureHeader}>
        <span aria-hidden="true" className={styles.fixtureIndex}>
          {kindLabel.slice(0, 1)}
        </span>
        <div className={styles.fixtureIdentity}>
          <p>{kindLabel} · {selectedObject.subtype || selectedObject.meta}</p>
          <h2>{selectedObject.label}</h2>
        </div>
      </header>
      <div className={styles.fixtureBadges}>
        <span data-tone="revision">本地样例</span>
      </div>
      <section className={styles.contextSection}>
        <header className={styles.contextSectionHeader}>
          <h3>核心信息</h3>
        </header>
        <dl className={styles.fixtureFacts}>
          <div><dt>类别</dt><dd>{selectedObject.meta || kindLabel}</dd></div>
          <div><dt>编号</dt><dd>{selectedObject.code}</dd></div>
        </dl>
      </section>
    </section>
  );
}

function OverviewRelationItem({
  item,
  onSelectObject,
  onSelectRelatedEvent,
  onOpenRelation,
}: {
  item: RelationOverviewItem;
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
  onOpenRelation?: (relationId: string) => void;
}) {
  const { relation, label, description, flow } = item;
  const navigate = () => {
    if (onOpenRelation) { onOpenRelation(relation.id); return; }
    if (relation.counterpart.objectType === "event") {
      onSelectRelatedEvent(relation.counterpart.id);
    } else {
      onSelectObject(relation.counterpart.id);
    }
  };
  const content = <>
    <span className={styles.relationIdentity}>
      <RelationObjectMark label={relation.counterpart.label} objectType={relation.counterpart.objectType} />
      <span className={styles.relationSentence}>
        <strong>{relation.counterpart.label}</strong>
        <small>{description}</small>
      </span>
    </span>
    <RelationFlow flow={flow} />
  </>;
  return (
    <li>
      {relation.counterpart.selectable && !relation.counterpart.missing ? (
        <button aria-label={`${label} ${relation.counterpart.label} ${description}`} className={styles.relationItem} onClick={navigate} type="button">
          {content}
        </button>
      ) : (
        <div className={styles.relationItem} data-missing={relation.counterpart.missing}>
          {content}
        </div>
      )}
    </li>
  );
}
