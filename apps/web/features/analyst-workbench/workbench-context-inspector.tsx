"use client";

import type { CaseFileDocument } from "@/lib/api-client";
import { useMemo, useState } from "react";

import {
  objectKindLabels,
  type CaseObject,
  type TimelineEvent,
  type WorkbenchAuditEntry,
} from "./analyst-fixture";
import {
  buildContextInspectorModel,
  type ContextChangeEntry,
} from "./workbench-context-inspector-model";
import type {
  ContextIncomingReference,
  ContextRelation,
  ContextRelationModel,
} from "./workbench-relation-model";
import {
  WorkbenchAuditPanel,
  type WorkbenchContextState,
} from "./workbench-context-panels";
import { WorkbenchObjectEditor } from "./workbench-object-editor";
import type { ObjectSaveResult } from "./workbench-object-persistence";
import { formatCaseWallClock } from "./workbench-presenters";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-context-inspector.module.css";

export function CandidatePreviewFactBoundary({
  area,
}: {
  area: "sources" | "audit" | "relations";
}) {
  const copy = {
    sources: {
      title: "候选预览不读取当前工作稿来源",
      detail:
        "候选正文中的稳定引用仍可核对，但来源记录正文只随当前工作稿读模型展示。",
    },
    audit: {
      title: "候选尚未进入当前工作稿",
      detail:
        "GET 预览不会产生采用或编辑审计；明确采用后才会新增只追加事实。",
    },
    relations: {
      title: "关系按候选预览内容计算",
      detail:
        "采用候选前，这些关系不会写入当前工作稿；与当前工作稿的对象关系可能不同。",
    },
  }[area];
  return (
    <div className={styles.previewBoundary} data-tone="success">
      <strong>{copy.title}</strong>
      <p>{copy.detail}</p>
    </div>
  );
}

function SectionToggleButton({
  collapsed,
  controls,
  label,
  onToggle,
  variant = "section",
}: {
  collapsed: boolean;
  controls: string;
  label: string;
  onToggle: () => void;
  variant?: "section" | "group";
}) {
  const className = variant === "section"
    ? styles.sectionToggle
    : styles.groupToggle;
  return (
    <button
      aria-controls={controls}
      aria-expanded={!collapsed}
      aria-label={`${collapsed ? "展开" : "收起"}${label}`}
      className={className}
      data-collapsed={collapsed}
      onClick={onToggle}
      title={collapsed ? `展开${label}` : `收起${label}`}
      type="button"
    >
      <WorkbenchIcon name="chevron" />
    </button>
  );
}

export function WorkbenchContextInspector({
  document,
  selectedObjectId,
  selectedObject,
  relatedEvents,
  auditEntries,
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
  onReloadContext,
  onOpenSources,
}: {
  document: CaseFileDocument | null;
  selectedObjectId: string | null;
  selectedObject: CaseObject | null;
  relatedEvents: TimelineEvent[];
  auditEntries: WorkbenchAuditEntry[];
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
  onReloadContext?: () => void;
  onOpenSources: () => void;
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
            从左侧目录选择人物、信息、事件、地点或假设后，这里会连续解释它是什么、
            从哪里来、与什么有关，以及最近谁动过它。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.contextInspector}>
      {document ? (
        <WorkbenchObjectEditor
          document={document}
          fieldCitations={writeLocked ? [] : model?.provenance.citations ?? []}
          key={selectedObjectId}
          navigationNotice={navigationNotice}
          onDirtyChange={onDirtyChange}
          onOpenSources={onOpenSources}
          onSave={onSave}
          onSelectObject={onSelectObject}
          readOnly={readOnly}
          readOnlyReason={readOnlyReason}
          revision={revision}
          revisionLabel={revisionLabel}
          saving={saving}
          selectedObjectId={selectedObjectId}
        />
      ) : (
        <FixtureObjectContext selectedObject={selectedObject} />
      )}

      <RelationContextSection
        modelRelations={model?.relations ?? null}
        onSelectObject={onSelectObject}
        onSelectRelatedEvent={onSelectRelatedEvent}
        realData={document !== null}
        relatedEvents={relatedEvents}
        selectedObject={selectedObject}
        writeLocked={writeLocked}
      />

      <RecentChangesSection
        fixtureEntries={auditEntries}
        modelChanges={model?.recentChanges ?? []}
        onRetry={onReloadContext}
        realData={document !== null}
        state={contextState}
        writeLocked={writeLocked}
      />
    </div>
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

function RelationContextSection({
  realData,
  writeLocked,
  modelRelations,
  selectedObject,
  relatedEvents,
  onSelectObject,
  onSelectRelatedEvent,
}: {
  realData: boolean;
  writeLocked: boolean;
  modelRelations: ContextRelationModel | null;
  selectedObject: CaseObject | null;
  relatedEvents: TimelineEvent[];
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
}) {
  const titleId = "context-relations-title";
  const bodyId = "context-relations-body";
  const [collapsed, setCollapsed] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const relationCount = modelRelations
    ? modelRelations.totals.all
    : relatedEvents.length;
  const incomingCount = modelRelations?.totals.incoming ?? 0;
  const hasRelations = modelRelations
    ? relationCount + incomingCount > 0
    : relatedEvents.length > 0;
  const groupIsCollapsed = (groupId: string) => collapsedGroups[groupId] ?? false;
  const toggleGroup = (groupId: string) => {
    setCollapsedGroups((groups) => ({
      ...groups,
      [groupId]: !groups[groupId],
    }));
  };

  return (
    <section aria-labelledby={titleId} aria-label="关系上下文" className={styles.contextSection}>
      <header className={styles.contextSectionHeader}>
        <h3 id={titleId}>关系上下文</h3>
        <span>
          {relationCount} 项关系{incomingCount ? ` · 被 ${incomingCount} 个字段引用` : ""}
        </span>
        <SectionToggleButton
          collapsed={collapsed}
          controls={bodyId}
          label="关系上下文"
          onToggle={() => setCollapsed((value) => !value)}
        />
      </header>
      <div hidden={collapsed} id={bodyId}>
        {writeLocked ? <CandidatePreviewFactBoundary area="relations" /> : null}
        {!hasRelations ? (
          <p className={styles.contextEmpty}>
            当前对象没有关系依据；它既不引用其他对象，也未被其他对象引用。
          </p>
        ) : null}
        {modelRelations ? (
          <div className={styles.relationGroups}>
            {modelRelations.groups.map((group) => {
              const groupBodyId = `relations-group-${group.id}`;
              const groupCollapsed = groupIsCollapsed(group.id);
              return (
                <section aria-label={group.title} className={styles.relationGroup} key={group.id}>
                  <header className={styles.relationGroupHeader}>
                    <h4>{group.title}</h4>
                    <span>{group.relations.length}</span>
                    <SectionToggleButton
                      collapsed={groupCollapsed}
                      controls={groupBodyId}
                      label={group.title}
                      onToggle={() => toggleGroup(group.id)}
                      variant="group"
                    />
                  </header>
                  <div hidden={groupCollapsed} id={groupBodyId}>
                    <ol className={styles.relationList}>
                      {group.relations.map((relation) => (
                        <RelationItem
                          key={relation.id}
                          onSelectObject={onSelectObject}
                          onSelectRelatedEvent={onSelectRelatedEvent}
                          relation={relation}
                          selectedObjectId={selectedObject?.id ?? ""}
                        />
                      ))}
                    </ol>
                  </div>
                </section>
              );
            })}
            {modelRelations.incoming.length ? (
              <section aria-label="反向引用" className={styles.relationGroup}>
                <header className={styles.relationGroupHeader}>
                  <h4>反向引用</h4>
                  <span>被 {modelRelations.incoming.length} 个字段引用</span>
                  <SectionToggleButton
                    collapsed={groupIsCollapsed("incoming")}
                    controls="relations-group-incoming"
                    label="反向引用"
                    onToggle={() => toggleGroup("incoming")}
                    variant="group"
                  />
                </header>
                <div hidden={groupIsCollapsed("incoming")} id="relations-group-incoming">
                  <ol className={styles.incomingList}>
                    {modelRelations.incoming.map((incoming) => (
                      <IncomingReferenceItem
                        incoming={incoming}
                        key={incoming.id}
                        onSelectObject={onSelectObject}
                        onSelectRelatedEvent={onSelectRelatedEvent}
                      />
                    ))}
                  </ol>
                </div>
              </section>
            ) : null}
          </div>
        ) : null}
        {!realData && relatedEvents.length ? (
          <section aria-label="参与事件" className={styles.relationGroup}>
            <header className={styles.relationGroupHeader}>
              <h4>参与事件</h4>
              <span>{relatedEvents.length}</span>
              <SectionToggleButton
                collapsed={groupIsCollapsed("related-events")}
                controls="relations-group-related-events"
                label="参与事件"
                onToggle={() => toggleGroup("related-events")}
                variant="group"
              />
            </header>
            <div hidden={groupIsCollapsed("related-events")} id="relations-group-related-events">
              <ol className={styles.relationList}>
                {relatedEvents
                  .filter((event) => event.id !== selectedObject?.id)
                  .map((event) => (
                    <li key={event.id}>
                      <button
                        className={styles.relationItem}
                        onClick={() => onSelectRelatedEvent(event.id)}
                        type="button"
                      >
                        <span className={styles.relationSentence}>
                          <strong>{selectedObject?.label ?? "当前对象"}</strong>
                          <em>{fixtureEventVerb(selectedObject?.kind)}</em>
                          <b aria-hidden="true">→</b>
                          <strong>{event.label}</strong>
                        </span>
                        <small>{formatCaseWallClock(event.time)} · 事件</small>
                      </button>
                    </li>
                  ))}
              </ol>
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}

function RelationItem({
  relation,
  selectedObjectId,
  onSelectObject,
  onSelectRelatedEvent,
}: {
  relation: ContextRelation;
  selectedObjectId: string;
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
}) {
  const navigate = () => {
    if (relation.counterpart.objectType === "event") {
      onSelectRelatedEvent(relation.counterpart.id);
    } else {
      onSelectObject(relation.counterpart.id);
    }
  };
  const sentence = (
    <span className={styles.relationSentence}>
      <strong data-current={relation.subject.id === selectedObjectId}>
        {relation.subject.label}
      </strong>
      <em>{relation.verb}</em>
      <b aria-hidden="true">{relation.arrow}</b>
      <strong data-current={relation.object.id === selectedObjectId}>
        {relation.object.label}
      </strong>
    </span>
  );
  const detail = (
    <small>
      {relation.counterpart.kindLabel}{relation.fieldLabel ? ` · ${relation.fieldLabel}` : ""}
    </small>
  );
  return (
    <li>
      {relation.counterpart.selectable && !relation.counterpart.missing ? (
        <button className={styles.relationItem} onClick={navigate} type="button">
          {sentence}
          {detail}
        </button>
      ) : (
        <div className={styles.relationItem} data-missing={relation.counterpart.missing}>
          {sentence}
          {detail}
        </div>
      )}
    </li>
  );
}

function IncomingReferenceItem({
  incoming,
  onSelectObject,
  onSelectRelatedEvent,
}: {
  incoming: ContextIncomingReference;
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
}) {
  const navigate = () => {
    if (incoming.source.objectType === "event") {
      onSelectRelatedEvent(incoming.sourceObjectId);
    } else {
      onSelectObject(incoming.sourceObjectId);
    }
  };
  const content = (
    <>
      <span className={styles.incomingSentence}>
        <strong>{incoming.source.label}</strong>
        <small>{incoming.source.kindLabel}</small>
        <em>{incoming.fieldLabel}</em>
      </span>
      <i>引用当前对象</i>
    </>
  );
  return (
    <li>
      {incoming.source.selectable && !incoming.source.missing ? (
        <button className={styles.incomingItem} onClick={navigate} type="button">
          {content}
        </button>
      ) : (
        <div className={styles.incomingItem}>{content}</div>
      )}
    </li>
  );
}

function fixtureEventVerb(kind: CaseObject["kind"] | undefined) {
  if (kind === "person") return "参与";
  if (kind === "evidence") return "作为证据出现在";
  if (kind === "location") return "发生地指向";
  if (kind === "event") return "关联";
  return "关联";
}

function RecentChangesSection({
  realData,
  writeLocked,
  state,
  modelChanges,
  fixtureEntries,
  onRetry,
}: {
  realData: boolean;
  writeLocked: boolean;
  state: WorkbenchContextState;
  modelChanges: ContextChangeEntry[];
  fixtureEntries: WorkbenchAuditEntry[];
  onRetry?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const titleId = "context-recent-changes-title";
  const bodyId = "context-recent-changes-body";

  if (writeLocked) {
    return <CandidatePreviewFactBoundary area="audit" />;
  }

  if (realData) {
    if (state.loading) {
      return (
        <section aria-busy="true" aria-labelledby={titleId} className={styles.contextSection}>
          <header className={styles.contextSectionHeader}>
            <h3 id={titleId}>最近变更</h3>
          </header>
          <p className={styles.contextEmpty}>正在读取只追加审计事实…</p>
        </section>
      );
    }
    if (state.error) {
      return (
        <section aria-labelledby={titleId} className={styles.contextSection} role="alert">
          <header className={styles.contextSectionHeader}>
            <h3 id={titleId}>最近变更</h3>
          </header>
          <p className={styles.contextEmpty}>{state.error}</p>
          {onRetry ? (
            <button className={styles.retryButton} onClick={onRetry} type="button">
              重新读取
            </button>
          ) : null}
        </section>
      );
    }
  }

  const entries = realData
    ? modelChanges.map((change) => ({
        id: change.id,
        time: formatContextClock(change.occurredAt),
        actor: change.actorLabel,
        action: change.actionLabel,
        detail: change.detail,
      }))
    : fixtureEntries.map((entry) => ({
        id: entry.id,
        time: entry.time,
        actor: entry.actor,
        action: entry.action,
        detail: entry.detail,
      }));
  const recent = entries.slice(0, 3);

  return (
    <section aria-labelledby={titleId} className={styles.contextSection}>
      <header className={styles.contextSectionHeader}>
        <h3 id={titleId}>最近变更</h3>
        <span>{entries.length}</span>
        <SectionToggleButton
          collapsed={collapsed}
          controls={bodyId}
          label="最近变更"
          onToggle={() => setCollapsed((value) => !value)}
        />
      </header>
      <div hidden={collapsed} id={bodyId}>
        {recent.length ? (
          <ol className={styles.changeList}>
            {recent.map((entry) => (
              <li key={entry.id}>
                <time>{entry.time}</time>
                <div>
                  <span>{entry.actor}</span>
                  <strong>{entry.action}</strong>
                  <small>{entry.detail}</small>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className={styles.contextEmpty}>当前工作稿还没有变更记录。</p>
        )}
        {entries.length ? (
          <button
            aria-expanded={expanded}
            className={styles.expandButton}
            onClick={() => setExpanded((value) => !value)}
            type="button"
          >
            {expanded ? "收起完整历史" : "查看完整历史"}
          </button>
        ) : null}
        {expanded && entries.length ? (
          realData ? (
            <WorkbenchAuditPanel onRetry={onRetry ?? (() => undefined)} state={state} />
          ) : (
            <ol className={styles.changeList}>
              {entries.map((entry) => (
                <li key={entry.id}>
                  <time>{entry.time}</time>
                  <div>
                    <span>{entry.actor}</span>
                    <strong>{entry.action}</strong>
                    <small>{entry.detail}</small>
                  </div>
                </li>
              ))}
            </ol>
          )
        ) : null}
      </div>
    </section>
  );
}

function formatContextClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}
