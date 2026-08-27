"use client";

import { useMemo, useState } from "react";
import type {
  PublicAgentMessage,
  PublicFinding,
  PublicPatchChange,
  PublicPatchReviewResult,
  PublicPatchSet,
} from "@casefile/contracts";

import styles from "./workbench-agent.module.css";

const patchStatusLabels: Record<PublicPatchSet["status"], string> = {
  pending: "待审阅",
  stale: "已失效",
  applied: "已应用",
  undone: "已撤销",
  rejected: "已拒绝",
};

const findingSeverityLabels: Record<PublicFinding["severity"], string> = {
  blocker: "阻断",
  warning: "提醒",
  note: "记录",
};

const relationshipLabels: Record<PublicPatchChange["relationship"], string> = {
  requested: "你要求的修改",
  consistency_support: "为保持一致性同步调整",
};

interface PatchConfirmation {
  confirmationToken?: string;
  acceptedWarningIds?: string[];
  confirmationNote?: string;
}

export function WorkbenchAgentInspector({
  patches,
  patchError,
  findings,
  focusPatchSetId,
  focusFindingId,
  onApply,
  onSimulate,
  requireApplyConfirmation = true,
  onFocusPatch,
  onUndo,
  onRedo,
  onRetry,
  onLocateObject,
  busyPatchSetId,
}: {
  patches: Array<{ message: PublicAgentMessage; patchSet: PublicPatchSet }>;
  patchError?: string | null;
  findings: Array<{ message: PublicAgentMessage; finding: PublicFinding }>;
  focusPatchSetId: number | null;
  focusFindingId: string | null;
  onApply: (
    patchSet: PublicPatchSet,
    changeIds: number[] | null,
    confirmation?: PatchConfirmation,
  ) => void;
  onSimulate?: (
    patchSet: PublicPatchSet,
    changeIds: number[] | null,
    acceptedWarningIds?: string[],
    confirmationNote?: string,
  ) => Promise<PublicPatchReviewResult | null>;
  requireApplyConfirmation?: boolean;
  onFocusPatch: (patchId: number) => void;
  onUndo: (patchSet: PublicPatchSet) => void;
  onRedo?: (patchSet: PublicPatchSet) => void;
  onRetry: (message: PublicAgentMessage) => void;
  onLocateObject?: (objectId: string) => void;
  busyPatchSetId: number | null;
}) {
  const activePatch = useMemo(
    () =>
      patches.find(({ patchSet }) => patchSet.patch_id === focusPatchSetId) ??
      patches.find(({ patchSet }) => patchSet.status === "pending") ??
      patches[0] ??
      null,
    [focusPatchSetId, patches],
  );

  return (
    <section aria-label="Agent 审阅" className={styles.agentInspector}>
      <header className={styles.agentInspectorHeader}>
        <div>
          <span>作者审阅</span>
          <strong>修改与发现</strong>
        </div>
        <small>
          {patches.length > 0 ? `修改建议 ${patches.length} 组` : "暂无修改建议"}
          {findings.length > 0 ? ` · 验证发现 ${findings.length}` : ""}
        </small>
      </header>

      {patches.length > 1 ? (
        <nav aria-label="修改建议列表" className={styles.agentInspectorNav}>
          {patches.map(({ patchSet }, index) => (
            <button
              aria-current={activePatch?.patchSet.patch_id === patchSet.patch_id}
              key={patchSet.patch_id}
              onClick={() => onFocusPatch(patchSet.patch_id)}
              type="button"
            >
              修改建议 {index + 1} · {patchStatusLabels[patchSet.status]}
            </button>
          ))}
        </nav>
      ) : null}

      {activePatch ? (
        <AgentPatchReview
          key={`${activePatch.patchSet.patch_id}:${activePatch.patchSet.status}`}
          busy={busyPatchSetId === activePatch.patchSet.patch_id}
          onApply={(changeIds, confirmation) =>
            confirmation === undefined
              ? onApply(activePatch.patchSet, changeIds)
              : onApply(activePatch.patchSet, changeIds, confirmation)
          }
          onSimulate={
            onSimulate
              ? (changeIds, warningIds, note) =>
                  onSimulate(activePatch.patchSet, changeIds, warningIds, note)
              : undefined
          }
          requireApplyConfirmation={requireApplyConfirmation}
          onLocateObject={onLocateObject}
          onRetry={() => onRetry(activePatch.message)}
          onUndo={() => onUndo(activePatch.patchSet)}
          onRedo={onRedo ? () => onRedo(activePatch.patchSet) : undefined}
          patchSet={activePatch.patchSet}
        />
      ) : null}
      {patchError ? (
        <p className={styles.agentPatchBlocker}>
          操作未完成：{patchError}。请按当前卷宗重新审阅后再试；系统没有应用部分修改。
        </p>
      ) : null}

      {findings.map(({ finding }, index) => (
        <PublicFindingReview
          finding={finding}
          focused={finding.finding_id === focusFindingId}
          key={finding.finding_id}
          position={index + 1}
        />
      ))}

      {patches.length === 0 && findings.length === 0 ? (
        <p className={styles.agentInspectorEmpty}>
          对话产生的修改建议和验证发现会出现在这里。
        </p>
      ) : null}
    </section>
  );
}

function PublicFindingReview({
  finding,
  focused,
  position,
}: {
  finding: PublicFinding;
  focused: boolean;
  position: number;
}) {
  return (
    <article
      aria-label={`验证发现 ${position}`}
      className={styles.agentFindingReview}
      data-focused={focused || undefined}
    >
      <header className={styles.agentAuditHeader}>
        <strong>{finding.title}</strong>
        <span>{findingSeverityLabels[finding.severity]}</span>
      </header>
      <p>{finding.statement}</p>
      <p className={styles.agentFindingNotice}>
        这项发现只说明问题，不代表已经授权自动修改。
      </p>
    </article>
  );
}

export function AgentPatchReview({
  patchSet,
  busy,
  onApply,
  onSimulate,
  requireApplyConfirmation,
  onUndo,
  onRedo,
  onRetry,
  onLocateObject,
}: {
  patchSet: PublicPatchSet;
  busy: boolean;
  onApply: (changeIds: number[] | null, confirmation?: PatchConfirmation) => void;
  onSimulate?: (
    changeIds: number[] | null,
    acceptedWarningIds?: string[],
    confirmationNote?: string,
  ) => Promise<PublicPatchReviewResult | null>;
  requireApplyConfirmation: boolean;
  onUndo: () => void;
  onRedo?: () => void;
  onRetry?: () => void;
  onLocateObject?: (objectId: string) => void;
}) {
  const [selectedIds, setSelectedIds] = useState<number[]>(() =>
    patchSet.review_rule === "selective"
      ? patchSet.changes.map((change) => change.change_id)
      : [],
  );
  const [confirmingApply, setConfirmingApply] = useState(false);
  const [confirmingReject, setConfirmingReject] = useState(false);
  const [review, setReview] = useState<PublicPatchReviewResult | null>(null);
  const [confirmationNote, setConfirmationNote] = useState("");
  const [acceptedWarningIds, setAcceptedWarningIds] = useState<string[]>([]);
  const actionable = patchSet.status === "pending";
  const selective = patchSet.review_rule === "selective";
  const groupedChanges = useMemo(
    () => ({
      requested: patchSet.changes.filter(
        (change) => change.relationship === "requested",
      ),
      consistency_support: patchSet.changes.filter(
        (change) => change.relationship === "consistency_support",
      ),
    }),
    [patchSet.changes],
  );
  const selectedChangeIds = selective ? selectedIds : null;
  const warningIds = review?.warnings.map((warning) => warning.notice_id) ?? [];
  const warningsAccepted = warningIds.every((id) => acceptedWarningIds.includes(id));
  const canApply = review?.can_apply === true && warningsAccepted;

  function toggleChange(changeId: number) {
    if (!selective) return;
    setSelectedIds((previous) =>
      previous.includes(changeId)
        ? previous.filter((id) => id !== changeId)
        : [...previous, changeId],
    );
    setReview(null);
    setAcceptedWarningIds([]);
  }

  async function simulate(
    warningIdsToAccept: string[] = [],
    note?: string,
  ) {
    if (!onSimulate || busy) return;
    const result = await onSimulate(selectedChangeIds, warningIdsToAccept, note);
    setAcceptedWarningIds(warningIdsToAccept);
    setReview(result);
    setConfirmingApply(false);
  }

  async function acceptWarningsAndResimulate() {
    if (warningIds.length === 0 || !confirmationNote.trim()) return;
    await simulate(warningIds, confirmationNote.trim());
  }

  function applyReviewed() {
    if (!canApply || review === null) return;
    onApply(selectedChangeIds, {
      ...(review.confirmation_token === null
        ? {}
        : { confirmationToken: review.confirmation_token }),
      ...(acceptedWarningIds.length === 0 ? {} : { acceptedWarningIds }),
      ...(confirmationNote.trim()
        ? { confirmationNote: confirmationNote.trim() }
        : {}),
    });
    setConfirmingApply(false);
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
    <article
      className={styles.agentPatchCard}
      data-status={patchSet.status}
      data-stale={patchSet.status === "stale" || undefined}
    >
      <header className={styles.agentPatchHeader}>
        <strong>{patchSet.title}</strong>
        <span>{patchStatusLabels[patchSet.status]}</span>
      </header>
      <p className={styles.agentPatchReason}>{patchSet.summary}</p>
      <p className={styles.agentPatchSimulationNote}>
        {patchSet.review_rule === "atomic"
          ? "这些修改必须作为一组审阅，不能拆开应用。"
          : "这组历史建议允许选择其中的修改项。"}
      </p>
      <p className={styles.agentPatchImpact}>{patchSet.impact.summary}</p>
      {patchSet.status === "stale" ? (
        <p className={styles.agentPatchBlocker}>
          当前卷宗已经变化，请重新生成并审阅修改建议。
        </p>
      ) : null}

      {(["requested", "consistency_support"] as const).map((relationship) => {
        const changes = groupedChanges[relationship];
        if (changes.length === 0) return null;
        return (
          <section
            className={styles.agentPatchGroup}
            data-relationship={relationship}
            key={relationship}
          >
            <header>
              <strong>{relationshipLabels[relationship]}</strong>
              <span>{changes.length} 项</span>
            </header>
            <div className={styles.agentPatchOps}>
              {changes.map((change) => (
                <PublicPatchChangeRow
                  busy={busy}
                  change={change}
                  checked={selectedIds.includes(change.change_id)}
                  key={change.change_id}
                  onLocateObject={onLocateObject}
                  onToggle={selective ? () => toggleChange(change.change_id) : undefined}
                />
              ))}
            </div>
          </section>
        );
      })}

      {review ? (
        <div className={styles.agentPatchSimulation} data-can-apply={review.can_apply}>
          <header>
            <strong>应用前审阅</strong>
            <span>{review.can_apply ? "检查通过" : "还不能应用"}</span>
          </header>
          {review.blockers.length > 0 ? (
            <ul>
              {review.blockers.map((blocker) => (
                <li key={blocker.notice_id}>{blocker.message}</li>
              ))}
            </ul>
          ) : null}
          {review.warnings.length > 0 ? (
            <div className={styles.agentPatchWarnings}>
              <strong>需要你确认的影响</strong>
              <ul>
                {review.warnings.map((warning) => (
                  <li key={warning.notice_id}>{warning.message}</li>
                ))}
              </ul>
              {!warningsAccepted ? (
                <label>
                  确认说明
                  <input
                    onChange={(event) => setConfirmationNote(event.target.value)}
                    placeholder="说明你已审阅并接受这项影响"
                    value={confirmationNote}
                  />
                </label>
              ) : (
                <small>你已确认这些影响。</small>
              )}
              {!warningsAccepted ? (
                <button
                  disabled={!confirmationNote.trim() || busy}
                  onClick={() => void acceptWarningsAndResimulate()}
                  type="button"
                >
                  接受影响并重新检查
                </button>
              ) : null}
            </div>
          ) : null}
          {review.requires_author_confirmation && review.warnings.length === 0 ? (
            <p>这组修改包含需要你亲自确认的影响。</p>
          ) : null}
        </div>
      ) : null}

      <div className={styles.agentPatchActions}>
        {actionable ? (
          confirmingApply ? (
            <span className={styles.agentPatchConfirm}>
              <strong>确认应用这组已经通过检查的修改？</strong>
              <button disabled={busy || !canApply} onClick={applyReviewed} type="button">
                确认应用
              </button>
              <button
                disabled={busy}
                onClick={() => setConfirmingApply(false)}
                type="button"
              >
                返回审阅
              </button>
            </span>
          ) : (
            <>
              {onSimulate && patchSet.actions.can_simulate ? (
                <button
                  disabled={busy || (selective && selectedIds.length === 0)}
                  onClick={() => void simulate()}
                  type="button"
                >
                  检查修改影响
                </button>
              ) : null}
              <button
                disabled={busy || !canApply}
                onClick={() =>
                  requireApplyConfirmation ? setConfirmingApply(true) : applyReviewed()
                }
                type="button"
              >
                应用修改
              </button>
              {confirmingReject ? (
                <>
                  <button
                    className={styles.agentPatchDanger}
                    disabled={busy}
                    onClick={rejectAll}
                    type="button"
                  >
                    确认拒绝
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => setConfirmingReject(false)}
                    type="button"
                  >
                    取消
                  </button>
                </>
              ) : (
                <button disabled={busy} onClick={rejectAll} type="button">
                  拒绝这组修改
                </button>
              )}
            </>
          )
        ) : null}
        {patchSet.actions.can_undo ? (
          <button disabled={busy} onClick={onUndo} type="button">
            撤销应用
          </button>
        ) : null}
        {patchSet.actions.can_redo && onRedo ? (
          <button disabled={busy} onClick={onRedo} type="button">
            重做应用
          </button>
        ) : null}
        {patchSet.status === "stale" && onRetry ? (
          <button disabled={busy} onClick={onRetry} type="button">
            重新生成建议
          </button>
        ) : null}
      </div>
    </article>
  );
}

function PublicPatchChangeRow({
  change,
  checked,
  busy,
  onToggle,
  onLocateObject,
}: {
  change: PublicPatchChange;
  checked: boolean;
  busy: boolean;
  onToggle?: () => void;
  onLocateObject?: (objectId: string) => void;
}) {
  const fieldLabel = change.kind === "update" ? change.field_label : null;
  const actionLabel =
    change.kind === "create" ? "新增" : change.kind === "delete" ? "删除" : "调整";
  return (
    <article
      className={styles.agentPatchOp}
      data-selectable={onToggle ? true : undefined}
    >
      {onToggle ? (
        <input
          aria-label={`选择修改 ${change.target.name}${fieldLabel ? ` ${fieldLabel}` : ""}`}
          checked={checked}
          disabled={busy}
          onChange={onToggle}
          type="checkbox"
        />
      ) : null}
      <span>
        <strong>
          <i>{actionLabel}</i>
          {change.target.name}
          <small>{change.target.type_label}</small>
        </strong>
        {fieldLabel ? <b>{fieldLabel}</b> : null}
        {change.kind === "create" ? (
          <ValueLine label="新增后" value={change.after.text} />
        ) : change.kind === "delete" ? (
          <ValueLine label="删除前" value={change.before.text} />
        ) : (
          <div className={styles.agentPatchValues}>
            <ValueLine label="修改前" value={change.before.text} />
            <ValueLine label="修改后" value={change.after.text} />
          </div>
        )}
        <em>{change.explanation}</em>
        {change.target.target_id !== null && onLocateObject ? (
          <button
            className={styles.agentPatchLocate}
            onClick={() => onLocateObject(change.target.target_id ?? "")}
            type="button"
          >
            在工作台定位
          </button>
        ) : null}
      </span>
    </article>
  );
}

function ValueLine({ label, value }: { label: string; value: string }) {
  return (
    <span className={styles.agentPatchValue}>
      <small>{label}</small>
      <span>{value}</span>
    </span>
  );
}
