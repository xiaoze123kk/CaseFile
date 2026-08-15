"use client";

import type { CaseFileDocument } from "@/lib/api-client";
import { useMemo, useState } from "react";

import {
  objectKindLabels,
  type CaseObject,
  type SourceItem,
  type TimelineEvent,
  type WorkbenchAuditEntry,
} from "./analyst-fixture";
import {
  buildContextInspectorModel,
  type ContextChangeEntry,
  type ContextSourceEvidence,
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
import type { WorkbenchModel } from "./workbench-real-data-types";
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

export function WorkbenchContextInspector({
  document,
  selectedObjectId,
  selectedObject,
  selectedEventId,
  relatedEvents,
  seed,
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
  selectedEventId: string | null;
  relatedEvents: TimelineEvent[];
  seed: WorkbenchModel;
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

  const fixtureSources = seed.sourceItems.filter(
    (source) =>
      source.eventId === selectedEventId ||
      (selectedObject?.relatedEventIds.includes(source.eventId) ?? false),
  );

  return (
    <div className={styles.contextInspector}>
      {document ? (
        <WorkbenchObjectEditor
          document={document}
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

      <SourceEvidenceSection
        fixtureSources={fixtureSources}
        modelSources={model?.sourceEvidence ?? []}
        onOpenSources={onOpenSources}
        onRetry={onReloadContext}
        realData={document !== null}
        state={contextState}
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
  const relationCount = modelRelations
    ? modelRelations.totals.all
    : relatedEvents.length;
  const incomingCount = modelRelations?.totals.incoming ?? 0;
  const hasRelations = modelRelations
    ? relationCount + incomingCount > 0
    : relatedEvents.length > 0;

  return (
    <section aria-labelledby={titleId} aria-label="关系上下文" className={styles.contextSection}>
      <header className={styles.contextSectionHeader}>
        <h3 id={titleId}>关系上下文</h3>
        <span>
          {relationCount} 项关系{incomingCount ? ` · 被 ${incomingCount} 个字段引用` : ""}
        </span>
      </header>
      {writeLocked ? <CandidatePreviewFactBoundary area="relations" /> : null}
      {!hasRelations ? (
        <p className={styles.contextEmpty}>
          当前对象没有关系依据；它既不引用其他对象，也未被其他对象引用。
        </p>
      ) : null}
      {modelRelations ? (
        <div className={styles.relationGroups}>
          {modelRelations.groups.map((group) => (
            <section aria-label={group.title} className={styles.relationGroup} key={group.id}>
              <header className={styles.relationGroupHeader}>
                <h4>{group.title}</h4>
                <span>{group.relations.length}</span>
              </header>
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
            </section>
          ))}
          {modelRelations.incoming.length ? (
            <section aria-label="反向引用" className={styles.relationGroup}>
              <header className={styles.relationGroupHeader}>
                <h4>反向引用</h4>
                <span>被 {modelRelations.incoming.length} 个字段引用</span>
              </header>
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
            </section>
          ) : null}
        </div>
      ) : null}
      {!realData && relatedEvents.length ? (
        <section aria-label="参与事件" className={styles.relationGroup}>
          <header className={styles.relationGroupHeader}>
            <h4>参与事件</h4>
            <span>{relatedEvents.length}</span>
          </header>
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
        </section>
      ) : null}
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

function SourceEvidenceSection({
  realData,
  writeLocked,
  state,
  modelSources,
  fixtureSources,
  onRetry,
  onOpenSources,
}: {
  realData: boolean;
  writeLocked: boolean;
  state: WorkbenchContextState;
  modelSources: ContextSourceEvidence[];
  fixtureSources: SourceItem[];
  onRetry?: () => void;
  onOpenSources: () => void;
}) {
  const titleId = "context-source-evidence-title";
  if (writeLocked) {
    return <CandidatePreviewFactBoundary area="sources" />;
  }
  if (!realData) {
    return (
      <section aria-labelledby={titleId} className={styles.contextSection}>
        <header className={styles.contextSectionHeader}>
          <h3 id={titleId}>来源依据</h3>
          <span>{fixtureSources.length}</span>
        </header>
        {fixtureSources.length ? (
          <div className={styles.sourceList}>
            {fixtureSources.map((source) => (
              <article className={styles.sourceCard} key={source.id}>
                <header>
                  <span>{fixtureSourceKindLabel(source.kind)}</span>
                  <small>{source.meta}</small>
                </header>
                <p>{source.excerpt}</p>
                <button onClick={onOpenSources} type="button">打开来源 →</button>
              </article>
            ))}
          </div>
        ) : (
          <p className={styles.contextEmpty}>当前对象还没有直接来源依据。</p>
        )}
      </section>
    );
  }

  if (state.loading) {
    return (
      <section aria-busy="true" aria-labelledby={titleId} className={styles.contextSection}>
        <header className={styles.contextSectionHeader}>
          <h3 id={titleId}>来源依据</h3>
        </header>
        <p className={styles.contextEmpty}>正在读取来源正文…</p>
      </section>
    );
  }
  if (state.error) {
    return (
      <section aria-labelledby={titleId} className={styles.contextSection} role="alert">
        <header className={styles.contextSectionHeader}>
          <h3 id={titleId}>来源依据</h3>
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

  return (
    <section aria-labelledby={titleId} className={styles.contextSection}>
      <header className={styles.contextSectionHeader}>
        <h3 id={titleId}>来源依据</h3>
        <span>{modelSources.length}</span>
      </header>
      {modelSources.length ? (
        <div className={styles.sourceList}>
          {modelSources.map((source) => (
            <article className={styles.sourceCard} key={source.id}>
              <header>
                <span>{source.kindLabel}</span>
                <time dateTime={source.createdAt}>{formatContextTime(source.createdAt)}</time>
              </header>
              <p>{source.excerpt}</p>
              <button onClick={onOpenSources} type="button">查看原文 →</button>
            </article>
          ))}
        </div>
      ) : (
        <p className={styles.contextEmpty}>当前工作稿没有登记来源记录。</p>
      )}
    </section>
  );
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
  const titleId = "context-recent-changes-title";

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
      </header>
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
    </section>
  );
}

function fixtureSourceKindLabel(kind: "audio" | "transcript" | "record" | "retrieval") {
  if (kind === "audio") return "证词录音";
  if (kind === "transcript") return "转写文本";
  if (kind === "record") return "卷宗记录";
  return "检索命中";
}

function formatContextTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
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
