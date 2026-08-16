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

function statusLabel(status: IssueStatus) {
  if (status === "patch-ready") return "补丁待审批";
  if (status === "resolved") return "已解决";
  if (status === "exception") return "已知例外";
  return "待处理";
}

interface ValidationIssuePanelProps {
  seed: WorkbenchSeed;
  issueId: string | null;
  issueStatuses: Record<string, IssueStatus>;
  selectedObjectId: string | null;
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
}

export function ValidationIssuePanel({
  seed,
  issueId,
  issueStatuses,
  selectedObjectId,
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
}: ValidationIssuePanelProps) {
  const issue =
    seed.validationIssues.find((item) => item.id === issueId) ??
    seed.validationIssues[0];
  const selectedObject = getObject(seed, selectedObjectId);

  if (!issue) {
    return (
      <section className={styles.realEmptyState} aria-labelledby="evidence-heading">
        <span>验证问题</span>
        <strong id="evidence-heading">
          {selectedObject
            ? `“${selectedObject.label}”没有关联的验证问题`
            : "没有验证问题"}
        </strong>
        <p>
          {selectedObject
            ? `当前工作稿未报告与${objectKindLabels[selectedObject.kind]}“${selectedObject.label}”关联的验证问题。`
            : "当前工作稿已通过确定性验证，或尚未生成可对照的验证问题。"}
        </p>
      </section>
    );
  }

  const issueBar = (
    <div className={styles.evidenceIssueBar} aria-label="验证问题列表">
      {seed.validationIssues.map((item) => {
        const itemStatus = issueStatuses[item.id] ?? "open";
        return (
          <button
            aria-pressed={item.id === issueId}
            data-status={itemStatus}
            key={item.id}
            onClick={() => onSelectIssue(item.id)}
            type="button"
          >
            <span data-severity={item.severity}>{item.severity}</span>
            <span>
              <strong>{item.title}</strong>
              <small>{statusLabel(itemStatus)}</small>
            </span>
          </button>
        );
      })}
    </div>
  );

  if (issue.source === "validator") {
    const targetObject = issue.targetObjectId
      ? getObject(seed, issue.targetObjectId)
      : undefined;
    return (
      <section className={styles.evidenceCompare} aria-labelledby="evidence-heading">
        <header className={styles.sectionHeader}>
          <div><span>确定性验证</span><h2 id="evidence-heading">{issue.title}</h2></div>
          <small>{issue.severity}</small>
        </header>
        {issueBar}
        <div className={styles.validatorIssueBody}>
          <dl className={styles.validatorIssueFacts}>
            <div>
              <dt>规则代码</dt>
              <dd>{issue.rule}</dd>
            </div>
            <div>
              <dt>JSON 路径</dt>
              <dd>{issue.jsonPath || "—"}</dd>
            </div>
            <div>
              <dt>字段路径</dt>
              <dd>{issue.fieldPath || "—"}</dd>
            </div>
          </dl>
          <div className={styles.validatorIssueTarget}>
            <span>目标对象</span>
            {targetObject ? (
              <button
                aria-label={`定位到目标对象：${targetObject.label}`}
                onClick={() => onSelectObject(targetObject.id)}
                type="button"
              >
                <strong>{targetObject.label}</strong>
                <small>{objectTypeLabel(issue.targetObjectType ?? "")}</small>
              </button>
            ) : (
              <p>该问题不指向当前工作稿中的单个对象，通常来自卷宗级结构约束。</p>
            )}
          </div>
          <p className={styles.validatorIssueHint}>
            这是确定性门禁发现的结构或引用问题，需要回到对象编辑修正；不会由 Agent 生成建议补丁。
          </p>
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
