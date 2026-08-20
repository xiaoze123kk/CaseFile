"use client";

import { useMemo, useState } from "react";

import type {
  AgentAuditFindingView,
  AgentMessageView,
  AgentPatchOperationView,
  AgentPatchSetView,
} from "@/lib/api-client";

import styles from "./workbench-agent.module.css";

const findingKindLabels: Record<string, string> = {
  dangling_ref: "断链",
  contradiction: "矛盾",
  temporal: "时序错误",
  motivation_gap: "动机缺口",
  scope_gap: "范围缺口",
};

const findingSeverityLabels: Record<string, string> = {
  S1: "致命",
  S2: "主要",
  S3: "次要",
};

const patchStatusLabels: Record<AgentPatchSetView["status"], string> = {
  pending: "待审阅",
  stale: "已失效",
  applied: "已应用",
  undone: "已撤销",
  rejected: "已拒绝",
};

const operationDecisionLabels: Record<string, string> = {
  pending: "待决定",
  accepted: "已采纳",
  rejected: "已拒绝",
};

function displayValue(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 96 ? `${text.slice(0, 95)}…` : text;
}

function objectRefLabel(
  ref: { object_type?: string; object_id?: string },
  labels: Record<string, string>,
) {
  return labels[ref.object_id ?? ""] ?? ref.object_id ?? ref.object_type ?? "对象";
}

export function WorkbenchAgentInspector({
  patches,
  patchError,
  findings,
  focusPatchSetId,
  focusFindingId,
  objectLabels,
  eventLabels,
  issueLabels,
  onApply,
  requireApplyConfirmation = true,
  onFocusPatch,
  onUndo,
  onRetry,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
  busyPatchSetId,
}: {
  patches: Array<{ message: AgentMessageView; patchSet: AgentPatchSetView }>;
  patchError?: string | null;
  findings: Array<{ message: AgentMessageView; finding: AgentAuditFindingView }>;
  focusPatchSetId: number | null;
  focusFindingId: string | null;
  objectLabels: Record<string, string>;
  eventLabels: Record<string, string>;
  issueLabels: Record<string, string>;
  onApply: (patchSet: AgentPatchSetView, operationIds: number[] | null) => void;
  requireApplyConfirmation?: boolean;
  onFocusPatch: (patchSetId: number) => void;
  onUndo: (patchSet: AgentPatchSetView) => void;
  onRetry: (message: AgentMessageView) => void;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onLocateIssue: (issueId: string) => void;
  busyPatchSetId: number | null;
}) {
  const activePatch = useMemo(
    () =>
      patches.find(({ patchSet }) => patchSet.patch_set_id === focusPatchSetId) ??
      patches.find(({ patchSet }) => patchSet.status === "pending") ??
      patches[0] ??
      null,
    [focusPatchSetId, patches],
  );

  return (
    <section aria-label="Agent 审阅" className={styles.agentInspector}>
      <header className={styles.agentInspectorHeader}>
        <div>
          <span>AGENT REVIEW</span>
          <strong>修改与发现</strong>
        </div>
        <small>
          {patches.length > 0 ? `待审修改 ${patches.length}` : "暂无待审修改"}
          {findings.length > 0 ? ` · 验证发现 ${findings.length}` : ""}
          {findings.some(({ finding }) => finding.needs_manual_review)
            ? ` · 待人工确认 ${findings.filter(({ finding }) => finding.needs_manual_review).length}`
            : ""}
        </small>
      </header>

      {patches.length > 1 ? (
        <nav aria-label="修改建议列表" className={styles.agentInspectorNav}>
          {patches.map(({ patchSet }) => (
            <button
              aria-current={activePatch?.patchSet.patch_set_id === patchSet.patch_set_id}
              key={patchSet.patch_set_id}
              onClick={() => onFocusPatch(patchSet.patch_set_id)}
              type="button"
            >
              #{patchSet.patch_set_id} · {patchStatusLabels[patchSet.status]}
            </button>
          ))}
        </nav>
      ) : null}

      {activePatch ? (
        <AgentPatchReview
          key={`${activePatch.patchSet.patch_set_id}:${activePatch.patchSet.status}:${activePatch.patchSet.updated_at}`}
          busy={busyPatchSetId === activePatch.patchSet.patch_set_id}
          objectLabels={objectLabels}
          onApply={(operationIds) => onApply(activePatch.patchSet, operationIds)}
          requireApplyConfirmation={requireApplyConfirmation}
          onLocateObject={onLocateObject}
          onRetry={() => onRetry(activePatch.message)}
          onUndo={() => onUndo(activePatch.patchSet)}
          patchSet={activePatch.patchSet}
        />
      ) : null}
      {patchError ? <p className={styles.agentPatchBlocker}>操作未完成：{patchError}。请以当前 Draft 重新预演后重试；服务端未执行静默部分应用。</p> : null}

      {findings.map(({ finding }) => (
        <AgentFindingReview
          key={finding.finding_id}
          ariaLabel={finding === findings[0]?.finding ? "逻辑漏洞复查发现" : `验证发现 ${finding.finding_id}`}
          focused={finding.finding_id === focusFindingId}
          eventLabels={eventLabels}
          finding={finding}
          issueLabels={issueLabels}
          objectLabels={objectLabels}
          onLocateEvent={onLocateEvent}
          onLocateIssue={onLocateIssue}
          onLocateObject={onLocateObject}
        />
      ))}

      {patches.length === 0 && findings.length === 0 ? (
        <p className={styles.agentInspectorEmpty}>对话产生的修改建议和验证发现会出现在这里。</p>
      ) : null}
    </section>
  );
}

function AgentFindingReview({
  finding,
  ariaLabel,
  focused,
  objectLabels,
  eventLabels,
  issueLabels,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
}: {
  finding: AgentAuditFindingView;
  ariaLabel: string;
  focused: boolean;
  objectLabels: Record<string, string>;
  eventLabels: Record<string, string>;
  issueLabels: Record<string, string>;
  onLocateObject: (id: string) => void;
  onLocateEvent: (id: string) => void;
  onLocateIssue: (id: string) => void;
}) {
  return (
    <article aria-label={ariaLabel} className={styles.agentFindingReview} data-focused={focused || undefined}>
      <header className={styles.agentAuditHeader}>
        <strong>逻辑漏洞复查发现 · {finding.finding_id}</strong>
        <span>{findingSeverityLabels[finding.severity] ?? finding.severity}</span>
      </header>
      <div className={styles.agentFindingMeta}>
        <span>{findingKindLabels[finding.kind] ?? finding.kind}</span>
        {finding.needs_manual_review ? <b>待人工确认</b> : <b>已取证</b>}
      </div>
      <strong>{finding.title}</strong>
      <p>{finding.statement}</p>
      <div className={styles.agentFindingEvidence}>
        <span>证据链与影响集</span>
        {finding.evidence_object_ids.map((id) => (
          <button key={`object:${id}`} onClick={() => onLocateObject(id)} type="button">
            对象 · {objectLabels[id] ?? id}
          </button>
        ))}
        {finding.evidence_event_ids.map((id) => (
          <button key={`event:${id}`} onClick={() => onLocateEvent(id)} type="button">
            事件 · {eventLabels[id] ?? id}
          </button>
        ))}
        {finding.evidence_validation_issue_ids.map((id) => (
          <button key={`issue:${id}`} onClick={() => onLocateIssue(id)} type="button">
            验证 · {issueLabels[id] ?? id}
          </button>
        ))}
        {finding.impact_refs?.map((ref, index) => (
          <button
            key={`impact:${ref.object_type ?? "ref"}:${ref.object_id ?? index}`}
            onClick={() => {
              if (ref.object_type === "event") onLocateEvent(ref.object_id ?? "");
              else if (ref.object_type === "validation_issue") onLocateIssue(ref.object_id ?? "");
              else onLocateObject(ref.object_id ?? "");
            }}
            type="button"
          >
            影响 · {objectRefLabel(ref, objectLabels)}
          </button>
        ))}
        {!finding.impact_refs?.length &&
        (finding.evidence_object_ids.length > 0 ||
          finding.evidence_event_ids.length > 0 ||
          finding.evidence_validation_issue_ids.length > 0) ? (
          <small>服务端未提供影响集；上方仅为证据引用。</small>
        ) : null}
        {finding.evidence_object_ids.length === 0 &&
        finding.evidence_event_ids.length === 0 &&
        finding.evidence_validation_issue_ids.length === 0 &&
        !finding.impact_refs?.length ? <small>服务端未提供可定位引用。</small> : null}
      </div>
      <p className={styles.agentFindingNotice}>
        Finding 只说明问题与影响，不代表已授权自动修复；如有 Patch，请在上方逐项确认。
      </p>
    </article>
  );
}

export function AgentPatchReview({
  patchSet,
  objectLabels,
  busy,
  onApply,
  requireApplyConfirmation,
  onUndo,
  onRetry,
  onLocateObject,
}: {
  patchSet: AgentPatchSetView;
  objectLabels: Record<string, string>;
  busy: boolean;
  onApply: (operationIds: number[] | null) => void;
  requireApplyConfirmation: boolean;
  onUndo: () => void;
  onRetry?: () => void;
  onLocateObject?: (objectId: string) => void;
}) {
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [confirmingApply, setConfirmingApply] = useState<
    { operationIds: number[] | null } | undefined
  >();
  const [confirmingReject, setConfirmingReject] = useState(false);
  const [issuesExpanded, setIssuesExpanded] = useState(false);
  const actionable = patchSet.status === "pending" && !patchSet.is_stale;
  const operations = useMemo(
    () => [...patchSet.operations].sort((left, right) => left.ordinal - right.ordinal),
    [patchSet.operations],
  );
  function toggleOperation(operationId: number) {
    setSelectedIds((previous) =>
      previous.includes(operationId)
        ? previous.filter((id) => id !== operationId)
        : [...previous, operationId],
    );
  }

  function requestApply(operationIds: number[] | null) {
    if (busy || !actionable) return;
    if (requireApplyConfirmation) setConfirmingApply({ operationIds });
    else onApply(operationIds);
  }

  function rejectAll() {
    if (confirmingReject) {
      setConfirmingReject(false);
      onApply([]);
      return;
    }
    setConfirmingReject(true);
  }

  return (
    <article className={styles.agentPatchCard} data-status={patchSet.status} data-stale={patchSet.is_stale || undefined}>
      <header className={styles.agentPatchHeader}>
        <strong>修改建议</strong>
        <span>
          {patchStatusLabels[patchSet.status]}
          {patchSet.is_stale ? " · 草稿已变化" : ""}
          {patchSet.status === "applied" && patchSet.applied_to_revision !== null
            ? ` · R${patchSet.applied_from_revision}→R${patchSet.applied_to_revision}`
            : ""}
        </span>
      </header>
      <p className={styles.agentPatchReason}>
        Patch #{patchSet.patch_set_id} · 目标 Draft R{patchSet.base_draft_revision}
        {patchSet.reason_summary ? ` · ${patchSet.reason_summary}` : ""}
      </p>
      <p className={styles.agentPatchSimulationNote}>
        只读预演 · 按 ordinal 执行 · 服务端会以 Draft、对象 revision 门禁原子校验，冲突时不会静默部分应用。
      </p>
      {patchSet.is_stale ? (
        <p className={styles.agentPatchBlocker}>阻断：当前 Draft 已不是生成该建议时的版本，请重新生成。</p>
      ) : null}
      <div className={styles.agentPatchOps}>
        {operations.map((operation: AgentPatchOperationView) => {
          const decision = operation.decision ?? "pending";
          const checked = decision === "accepted" || (actionable && selectedIds.includes(operation.operation_id));
          const label = objectLabels[operation.object_id ?? ""] ?? operation.object_id ?? "对象";
          return (
            <label className={styles.agentPatchOp} key={operation.operation_id}>
              <input
                aria-label={`选择修改 ${label} ${operation.field_path}`}
                checked={checked}
                disabled={!actionable || busy}
                onChange={() => toggleOperation(operation.operation_id)}
                type="checkbox"
              />
              <span>
                <strong><i>#{operation.ordinal}</i>{label}<code>{operation.field_path}</code></strong>
                <span className={styles.agentPatchOpMeta}>
                  {operation.object_type ? <span>{operation.object_type}</span> : null}
                  {operation.expected_object_revision !== null ? <span>对象 R{operation.expected_object_revision}</span> : null}
                  <span>{operation.operation_type}</span>
                  {operation.object_id !== null && onLocateObject ? (
                    <button aria-label={`定位对象 ${label}`} onClick={() => onLocateObject(operation.object_id ?? "")} type="button">在工作台定位</button>
                  ) : null}
                </span>
                <small>{displayValue(operation.old_value)} → {displayValue(operation.new_value)}</small>
                <em>{operation.reason}</em>
              </span>
              <b data-decision={decision}>{operationDecisionLabels[decision]}</b>
            </label>
          );
        })}
      </div>
      {patchSet.validator_issues.length > 0 ? (
        <div className={styles.agentPatchIssues}>
          <button aria-expanded={issuesExpanded} onClick={() => setIssuesExpanded((expanded) => !expanded)} type="button">
            {issuesExpanded ? "收起" : "查看"}验证警告（{patchSet.validator_issues.length}）
          </button>
          {issuesExpanded ? <ul>{patchSet.validator_issues.map((issue, index) => <li key={`${String(issue.rule_id ?? "issue")}:${index}`}><strong>{typeof issue.title === "string" ? issue.title : `验证警告 ${index + 1}`}</strong>{typeof issue.rule_id === "string" ? <code>{issue.rule_id}</code> : null}{typeof issue.message === "string" ? <span>{issue.message}</span> : null}</li>)}</ul> : null}
        </div>
      ) : null}
      {patchSet.validation_warning ? <p className={styles.agentPatchWarning}>服务端标记应用后仍有 {patchSet.validator_issues.length} 条验证警告，应用结果后会刷新工作台。</p> : null}
      <div className={styles.agentPatchActions}>
        {actionable ? confirmingApply ? (
          <span className={styles.agentPatchConfirm}>
            <strong>{confirmingApply.operationIds === null ? "确认按 ordinal 顺序应用全部修改？" : `确认应用所选 ${confirmingApply.operationIds.length} 项？`}</strong>
            <button disabled={busy} onClick={() => { const choice = confirmingApply.operationIds; setConfirmingApply(undefined); onApply(choice); }} type="button">确认应用</button>
            <button disabled={busy} onClick={() => setConfirmingApply(undefined)} type="button">返回预演</button>
          </span>
        ) : (
          <>
            <button disabled={busy} onClick={() => requestApply(null)} type="button">全部采纳</button>
            <button disabled={busy || selectedIds.length === 0} onClick={() => requestApply(selectedIds)} type="button">采纳所选（{selectedIds.length}）</button>
            {confirmingReject ? <><button className={styles.agentPatchDanger} disabled={busy} onClick={rejectAll} type="button">确认拒绝</button><button disabled={busy} onClick={() => setConfirmingReject(false)} type="button">取消</button></> : <button disabled={busy} onClick={rejectAll} type="button">全部拒绝</button>}
          </>
        ) : null}
        {patchSet.status === "applied" ? <button disabled={busy} onClick={onUndo} type="button">撤销应用</button> : null}
        {patchSet.is_stale && onRetry ? <button disabled={busy} onClick={onRetry} type="button">重新生成建议</button> : null}
      </div>
    </article>
  );
}
