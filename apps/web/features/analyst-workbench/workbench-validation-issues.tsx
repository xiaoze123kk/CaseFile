"use client";

import {
  getEvent,
  getObject,
  type IssueStatus,
  objectKindLabels,
  type WorkbenchSeed,
} from "./analyst-fixture";
import { objectTypeLabel } from "./workbench-presenters";
import styles from "./analyst-workbench.module.css";

type ValidationIssue = WorkbenchSeed["validationIssues"][number];

function statusLabel(status: IssueStatus) {
  if (status === "patch-ready") return "建议待确认";
  if (status === "resolved") return "已解决";
  if (status === "exception") return "已知例外";
  return "待处理";
}

function severityLabel(severity: string) {
  return {
    S0: "必须处理",
    S1: "重要",
    S2: "建议检查",
    S3: "提示",
    error: "必须处理",
    warning: "重要",
  }[severity] ?? "需要查看";
}

function issueCategoryLabel(issue: ValidationIssue) {
  if (issue.verificationFinding?.kind === "llm") return "Agent 复查";
  if (issue.rule.includes("knowledge") || issue.rule.includes("temporal")) {
    return "故事一致性";
  }
  if (issue.rule.includes("reference") || issue.rule.includes("ref")) {
    return "内容关联";
  }
  return "结构检查";
}

function creatorFacingText(
  value: string | null | undefined,
  seed: WorkbenchSeed,
) {
  if (!value) return null;
  return seed.caseObjects.reduce(
    (text, object) => text.replaceAll(object.id, object.label),
    value,
  );
}

function issueExplanation(issue: ValidationIssue, seed: WorkbenchSeed) {
  if (issue.rule.includes("knowledge_state_available_before_source")) {
    return "角色在真正获得这条信息之前就表现得已经知情，会让调查推进显得前后矛盾。";
  }
  if (issue.rule.includes("missing_reference")) {
    return "故事中的一处内容指向了不存在或已经移除的对象，需要重新选择关联内容。";
  }
  return creatorFacingText(issue.explanation, seed) ?? issue.title;
}

function findingStatusLabel(
  status: NonNullable<
    ValidationIssuePanelProps["seed"]["validationIssues"][number]["verificationFinding"]
  >["status"],
) {
  return {
    open: "待处理",
    resolved: "已解决",
    reopened: "已重开",
    dismissed: "已忽略",
  }[status];
}

interface ValidationIssuePanelProps {
  seed: WorkbenchSeed;
  issueId: string | null;
  issueStatuses: Record<string, IssueStatus>;
  status: IssueStatus;
  manualValue: string;
  editing: boolean;
  onManualValueChange: (value: string) => void;
  onStartEditing: () => void;
  onSaveManual: () => void;
  onSelectIssue: (issueId: string) => void;
  onRequestPatch: () => void;
  onRejectPatch: () => void;
  onResolveIssue: (action: "approve" | "exception") => void;
  onSelectObject: (objectId: string) => void;
  onSendToAgent: (issueId: string) => void;
  onRerunVerification: () => void;
  realData: boolean;
}

export function ValidationIssuePanel({
  seed,
  issueId,
  issueStatuses,
  status,
  manualValue,
  editing,
  onManualValueChange,
  onStartEditing,
  onSaveManual,
  onSelectIssue,
  onRequestPatch,
  onRejectPatch,
  onResolveIssue,
  onSelectObject,
  onSendToAgent,
  onRerunVerification,
  realData,
}: ValidationIssuePanelProps) {
  const issue =
    seed.validationIssues.find((item) => item.id === issueId) ??
    seed.validationIssues[0];

  if (!issue) {
    return (
      <section className={styles.realEmptyState} aria-labelledby="evidence-heading">
        <span>故事检查</span>
        <strong id="evidence-heading">当前没有需要处理的问题</strong>
        <p>
          人物认知、事件顺序和内容关联目前保持一致。故事发生变化后，可以重新检查一次。
        </p>
        {realData ? (
          <button onClick={onRerunVerification} type="button">
            重新检查
          </button>
        ) : null}
      </section>
    );
  }

  const groupedIssues = [...seed.validationIssues.reduce((groups, item) => {
    const key = `${item.severity}\u0000${item.rule}\u0000${item.title}`;
    const group = groups.get(key) ?? [];
    group.push(item);
    groups.set(key, group);
    return groups;
  }, new Map<string, ValidationIssue[]>()).values()];
  const openIssueCount = seed.validationIssues.filter((item) => {
    const itemStatus = issueStatuses[item.id] ?? "open";
    return itemStatus !== "resolved" && itemStatus !== "exception";
  }).length;

  const issueBar = (
    <section className={styles.evidenceIssueIndex} aria-label="待处理问题列表">
      <header>
        <div>
          <span>待处理问题</span>
          <strong>{openIssueCount ? `${openIssueCount} 处需要查看` : "全部处理完成"}</strong>
        </div>
        <small>{groupedIssues.length} 类问题</small>
      </header>
      <div className={styles.evidenceIssueBar}>
      {groupedIssues.map((items) => {
        const item = items.find((candidate) => candidate.id === issueId) ??
          items.find((candidate) => {
            const candidateStatus = issueStatuses[candidate.id] ?? "open";
            return candidateStatus !== "resolved" && candidateStatus !== "exception";
          }) ?? items[0];
        const statuses = items.map((candidate) => issueStatuses[candidate.id] ?? "open");
        const itemStatus = statuses.includes("open")
          ? "open"
          : statuses.includes("patch-ready")
            ? "patch-ready"
            : statuses.includes("exception")
              ? "exception"
              : "resolved";
        return (
          <button
            aria-pressed={items.some((candidate) => candidate.id === issueId)}
            data-severity={item.severity}
            data-status={itemStatus}
            key={items[0].id}
            onClick={() => onSelectIssue(item.id)}
            type="button"
          >
            <i aria-hidden="true" />
            <span>
              <strong>{item.title}</strong>
              <small>
                {severityLabel(item.severity)} · {items.length} 处 · {statusLabel(itemStatus)}
              </small>
            </span>
          </button>
        );
      })}
      </div>
    </section>
  );

  if (issue.source === "validator" || issue.source === "agent") {
    const activeIssues =
      groupedIssues.find((items) => items.some((item) => item.id === issue.id)) ??
      [issue];
    const targetObjects = activeIssues
      .flatMap((item) => {
        const object = item.targetObjectId
          ? getObject(seed, item.targetObjectId)
          : undefined;
        return object
          ? [{ object, objectType: item.targetObjectType ?? "" }]
          : [];
      })
      .filter(
        (entry, index, entries) =>
          entries.findIndex((candidate) => candidate.object.id === entry.object.id) ===
          index,
      );
    const evidenceObjects = activeIssues
      .flatMap((item) =>
        item.evidenceIds.flatMap((id) => {
          const object = getObject(seed, id);
          return object ? [object] : [];
        }),
      )
      .filter(
        (object, index, objects) =>
          objects.findIndex((candidate) => candidate.id === object.id) === index,
      );
    return (
      <section className={styles.evidenceCompare} aria-labelledby="evidence-heading">
        <header className={styles.evidenceIssueHero} data-severity={issue.severity}>
          <div>
            <span>{issueCategoryLabel(issue)}</span>
            <h2 id="evidence-heading">{issue.title}</h2>
            <p>{issueExplanation(issue, seed)}</p>
          </div>
          <div className={styles.evidenceIssueHeroActions}>
            <strong>{severityLabel(issue.severity)}</strong>
            {realData ? (
              <button onClick={onRerunVerification} type="button">
                重新检查
              </button>
            ) : null}
          </div>
        </header>
        {issueBar}
        <div className={styles.validatorIssueBody}>
          <section className={styles.validatorIssueSection}>
            <h3>涉及内容</h3>
            <div className={styles.validatorIssueConnections}>
              {targetObjects.map(({ object, objectType }) => (
                <button
                  aria-label={`定位到目标对象：${object.label}`}
                  key={object.id}
                  onClick={() => onSelectObject(object.id)}
                  type="button"
                >
                  <span>需要修改</span>
                  <strong>{object.label}</strong>
                  <small>{objectTypeLabel(objectType)}</small>
                </button>
              ))}
              {targetObjects.length === 0 ? (
                <p>这是一项全局故事问题，不只指向某一个对象。</p>
              ) : null}
              {evidenceObjects.map((object) => (
                <button
                  aria-label={`定位到证据：${object.label}`}
                  key={object.id}
                  onClick={() => onSelectObject(object.id)}
                  type="button"
                >
                  <span>相关线索</span>
                  <strong>{object.label}</strong>
                  <small>{objectKindLabels[object.kind]}</small>
                </button>
              ))}
            </div>
          </section>
          <section className={styles.validatorIssueAdvice}>
            <h3>建议怎么做</h3>
            <p>
              {creatorFacingText(issue.fixHint, seed) ??
                (issue.verificationFinding?.kind === "llm"
                  ? "先核对相关线索，再决定是否接受这项发现。"
                  : "打开涉及对象，修正不一致的内容后重新检查。")}
            </p>
          </section>
          {issue.verificationFinding && realData ? (
            <div className={styles.evidenceActions}>
              {issue.verificationFinding.status === "open" || issue.verificationFinding.status === "reopened" ? (
                <>
                  <button onClick={() => onResolveIssue("approve")} type="button">标记已解决</button>
                  <button onClick={() => onResolveIssue("exception")} type="button">忽略此项</button>
                </>
              ) : (
                <button onClick={() => onSendToAgent(issue.id)} type="button">让 Agent 复查</button>
              )}
            </div>
          ) : null}
          <details className={styles.validatorTechnicalDetails}>
            <summary>查看技术详情</summary>
            <dl className={styles.validatorIssueFacts}>
              {issue.verificationFinding ? (
                <>
                  <div>
                    <dt>检查来源</dt>
                    <dd>{issue.verificationFinding.kind === "deterministic" ? "自动检查" : "Agent 复查"}</dd>
                  </div>
                  <div>
                    <dt>处理状态</dt>
                    <dd>{findingStatusLabel(issue.verificationFinding.status)}</dd>
                  </div>
                  <div>
                    <dt>参考置信度</dt>
                    <dd>{issue.verificationFinding.confidence === null ? "—" : `${Math.round(issue.verificationFinding.confidence * 100)}%`}</dd>
                  </div>
                </>
              ) : null}
              <div>
                <dt>检查规则</dt>
                <dd>{issue.rule}</dd>
              </div>
              <div>
                <dt>数据位置</dt>
                <dd>{issue.jsonPath || "—"}</dd>
              </div>
              <div>
                <dt>字段位置</dt>
                <dd>{issue.fieldPath || "—"}</dd>
              </div>
            </dl>
          </details>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.evidenceCompare} aria-labelledby="evidence-heading">
      <header className={styles.sectionHeader}>
        <div><span>证据 × 知识状态</span><h2 id="evidence-heading">{issue.title}</h2></div>
        <small>{issue.severity} · {statusLabel(status)}</small>
      </header>
      {realData ? (
        <div className={styles.evidenceActions}>
          <button onClick={onRerunVerification} type="button">
            重跑验证
          </button>
        </div>
      ) : null}
      {issueBar}
      <div className={styles.knowledgeSequence}>
        <article>
          <span>事件前已知</span>
          <strong>22:31 前</strong>
          <p>{issue.beforeKnowledge}</p>
        </article>
        <i aria-hidden="true" />
        <article data-conflict="true">
          <span>事件声称</span>
          <strong>{getEvent(seed, issue.eventId)?.time}</strong>
          <p>{issue.eventClaim}</p>
        </article>
        <i aria-hidden="true" />
        <article>
          <span>证据实际进入</span>
          <strong>22:40</strong>
          <p>{issue.afterKnowledge}</p>
        </article>
      </div>
      <div className={styles.diffPanel}>
        <header><span>建议修订</span><b>人工批准前不会写入正式版本</b></header>
        <div className={styles.diffLine} data-kind="remove"><b>−</b><p>{issue.patchBefore}</p></div>
        <div className={styles.diffLine} data-kind="add"><b>+</b><p>{issue.patchAfter}</p></div>
        {editing ? (
          <label className={styles.manualEditor}>
            <span>人工修订文本</span>
            <textarea autoFocus onChange={(event) => onManualValueChange(event.target.value)} rows={4} value={manualValue} />
            <button onClick={onSaveManual} type="button">保存并局部重算</button>
          </label>
        ) : (
          <button className={styles.textAction} onClick={onStartEditing} type="button">改为人工修正</button>
        )}
        {status !== "resolved" && status !== "exception" ? (
          <div className={styles.evidenceActions}>
            {status === "patch-ready" ? (
              <>
                <span>Agent 建议已生成，等待人工批准。</span>
                <button onClick={onRejectPatch} type="button">退回待处理</button>
                <button onClick={() => onResolveIssue("approve")} type="button">批准并局部重算</button>
              </>
            ) : realData ? (
              <button onClick={() => onSendToAgent(issue.id)} type="button">
                让 Agent 处理
              </button>
            ) : (
              <>
                <button onClick={onRequestPatch} type="button">请求 Agent 补丁</button>
                <button onClick={() => onResolveIssue("exception")} type="button">标记已知例外</button>
              </>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
