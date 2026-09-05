"use client";

import { useMemo, useState, type ReactNode, type SetStateAction } from "react";
import type {
  PublicAgentMessage,
  PublicFinding,
  PublicPatchReviewResult,
  PublicPatchSet,
} from "@casefile/contracts";

import styles from "./workbench-agent.module.css";
import { AgentPatchCard, patchStatusLabels } from "./workbench-agent-patch-card";
import patchStyles from "./workbench-agent-patch-card.module.css";
import { WorkbenchIcon } from "./workbench-icon";

const findingSeverityLabels: Record<PublicFinding["severity"], string> = {
  blocker: "阻断",
  warning: "提醒",
  note: "记录",
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
  renderPatch,
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
  renderPatch?: (message: PublicAgentMessage) => ReactNode;
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

      {activePatch && renderPatch ? renderPatch(activePatch.message) : activePatch ? (
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

export interface PatchReviewState {
  selectedIds: number[];
  confirmingApply: boolean;
  review: PublicPatchReviewResult | null;
  confirmationNote: string;
  acceptedWarningIds: string[];
}

export function initialPatchReview(patch: PublicPatchSet): PatchReviewState {
  return { selectedIds: patch.review_rule === "selective" ? patch.changes.map((change) => change.change_id) : [], confirmingApply: false, review: null, confirmationNote: "", acceptedWarningIds: [] };
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
  reviewState,
  onReviewStateChange,
  conversation = false,
  onDetails,
  onAdjust,
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
  reviewState?: PatchReviewState;
  onReviewStateChange?: (state: SetStateAction<PatchReviewState>) => void;
  conversation?: boolean;
  onDetails?: () => void;
  onAdjust?: () => void;
}) {
  const [localReviewState, setLocalReviewState] = useState(() => initialPatchReview(patchSet));
  const state = reviewState ?? localReviewState;
  const { selectedIds, confirmingApply, review, confirmationNote, acceptedWarningIds } = state;
  function fieldSetter<K extends keyof PatchReviewState>(key: K) {
    return (value: SetStateAction<PatchReviewState[K]>) => {
      const update = (previous: PatchReviewState) => ({ ...previous, [key]: typeof value === "function" ? (value as (previous: PatchReviewState[K]) => PatchReviewState[K])(previous[key]) : value });
      if (onReviewStateChange) onReviewStateChange(update);
      else setLocalReviewState(update);
    };
  }
  const setSelectedIds = fieldSetter("selectedIds");
  const setConfirmingApply = fieldSetter("confirmingApply");
  const setReview = fieldSetter("review");
  const setConfirmationNote = fieldSetter("confirmationNote");
  const setAcceptedWarningIds = fieldSetter("acceptedWarningIds");
  const actionable = patchSet.status === "pending";
  const selective = patchSet.review_rule === "selective";
  const selectedChangeIds = selective ? selectedIds : null;
  const warningIds = review?.warnings.map((warning) => warning.notice_id) ?? [];
  const warningsAccepted = warningIds.every((id) => acceptedWarningIds.includes(id));
  const canApply = actionable && review?.patch_id === patchSet.patch_id && review?.can_apply === true && warningsAccepted && (!selective || selectedIds.length > 0);

  function toggleChange(changeId: number) {
    if (!selective) return;
    setSelectedIds((previous) =>
      previous.includes(changeId)
        ? previous.filter((id) => id !== changeId)
        : [...previous, changeId],
    );
    setReview(null);
    setAcceptedWarningIds([]);
    setConfirmingApply(false);
  }

  async function simulate(
    warningIdsToAccept: string[] = [],
    note?: string,
    confirmAfterCheck = false,
  ) {
    if (!onSimulate || busy) return;
    const result = await onSimulate(selectedChangeIds, warningIdsToAccept, note);
    setAcceptedWarningIds(warningIdsToAccept);
    setReview(result);
    setConfirmingApply(confirmAfterCheck && result?.can_apply === true && result.warnings.every((warning) => warningIdsToAccept.includes(warning.notice_id)));
  }

  async function acceptWarningsAndResimulate() {
    if (warningIds.length === 0 || !confirmationNote.trim()) return;
    await simulate(warningIds, confirmationNote.trim());
  }

  function applyReviewed() {
    if (busy || !canApply || review === null) return;
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

  return (
    <AgentPatchCard patchSet={patchSet} busy={busy} selectedIds={selectedIds}
      onToggle={selective ? toggleChange : undefined} onLocateObject={onLocateObject}>

      {review ? (
        <div className={patchStyles.review} data-can-apply={review.can_apply} role="status">
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
            <div className={patchStyles.warnings}>
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

      <div className={patchStyles.actions}>
        {onDetails ? <button className={patchStyles.details} type="button" onClick={onDetails}><WorkbenchIcon name="search" />查看细节</button> : null}
        {actionable && onAdjust ? <button type="button" disabled={busy} onClick={onAdjust}><WorkbenchIcon name="settings" />调整</button> : null}
        {actionable ? (
          confirmingApply ? (
            <span className={patchStyles.confirm}>
              <strong>确认应用这组已经通过检查的修改（{selective ? selectedIds.length : patchSet.changes.length} 项）？</strong>
              <button className={patchStyles.primary} disabled={busy || !canApply} onClick={applyReviewed} type="button">
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
              {onSimulate && patchSet.actions.can_simulate && !conversation ? (
                <button
                  disabled={busy || (selective && selectedIds.length === 0)}
                  onClick={() => void simulate()}
                  type="button"
                >
                  检查修改影响
                </button>
              ) : null}
              <button
                className={patchStyles.primary}
                disabled={busy || (conversation ? (!canApply && (!onSimulate || !patchSet.actions.can_simulate)) || (selective && selectedIds.length === 0) : !canApply)}
                onClick={() =>
                  conversation && !canApply ? void simulate([], undefined, true) : requireApplyConfirmation ? setConfirmingApply(true) : applyReviewed()
                }
                type="button"
              >
                <WorkbenchIcon name="validate" />{conversation ? busy ? "正在检查…" : `应用 ${selective ? selectedIds.length : patchSet.changes.length} 项` : "应用修改"}
              </button>
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
    </AgentPatchCard>
  );
}
