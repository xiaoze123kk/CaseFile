import type { Ref } from "react";

import type {
  BriefConfirmationIssue,
  BriefResolutionDecision,
} from "./intake-model";
import styles from "./brief-confirmation-feedback.module.css";

export function BriefRevisionDialog({
  currentVersion,
  pending,
  onCancel,
  onConfirm,
}: {
  currentVersion: number;
  pending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const nextVersion = currentVersion + 1;
  return (
    <div className={styles.impactDialogBackdrop} role="presentation">
      <section
        aria-describedby="brief-revision-description"
        aria-labelledby="brief-revision-title"
        aria-modal="true"
        className={styles.impactDialog}
        role="dialog"
      >
        <small>VERSION REVISION / 建案修订</small>
        <span aria-hidden="true">↗</span>
        <h2 id="brief-revision-title">创建建案修订</h2>
        <p id="brief-revision-description">
          当前 V{currentVersion} 会继续保留。CaseFile 将以它为基础，创建一个可编辑的新版本。
        </p>
        <div className={styles.revisionVersionPreview}>
          <span>Brief V{currentVersion} · 已确认</span>
          <i aria-hidden="true" />
          <strong>Brief V{nextVersion} · 编辑中</strong>
        </div>
        <strong>原版本、现有候选和 Agent 对话都不会丢失。</strong>
        <footer>
          <button disabled={pending} onClick={onCancel} type="button">
            取消
          </button>
          <button autoFocus disabled={pending} onClick={onConfirm} type="button">
            {pending ? "正在创建…" : `创建 V${nextVersion}`}
            <span aria-hidden="true">→</span>
          </button>
        </footer>
      </section>
    </div>
  );
}

export function BriefConfirmationTransition({
  completed,
}: {
  completed: boolean;
}) {
  return (
    <section className={styles.confirmationTransition}>
      <div
        aria-live="polite"
        aria-atomic="true"
        className={styles.confirmationTransitionCard}
        role="status"
      >
        <span>CASE BRIEF / 03</span>
        <i aria-hidden="true" data-completed={completed || undefined}>
          {completed ? "✓" : null}
        </i>
        <h1>{completed ? "建案完成" : "正在确认建案"}</h1>
        <p>
          {completed
            ? "CaseFile 已准备好进入深稿阶段。"
            : "正在整理创作边界与生成依据……"}
        </p>
      </div>
    </section>
  );
}

export function BriefConfirmationInterruption({
  decision,
  issue,
  issueRef,
  onConfirmDecision,
  onDecisionChange,
  onReturnToField,
}: {
  decision: BriefResolutionDecision | null;
  issue: BriefConfirmationIssue;
  issueRef: Ref<HTMLElement>;
  onConfirmDecision: () => void;
  onDecisionChange: (decision: BriefResolutionDecision) => void;
  onReturnToField: () => void;
}) {
  if (issue.kind === "missing_field") {
    return (
      <section
        className={styles.confirmationInterruption}
        ref={issueRef}
        role="alert"
        tabIndex={-1}
      >
        <span aria-hidden="true">!</span>
        <div>
          <strong>还需要补充一项</strong>
          <p>“{issue.label}”完成后，才能确认并冻结这份创作简报。</p>
        </div>
        <button onClick={onReturnToField} type="button">
          返回填写
        </button>
      </section>
    );
  }

  const authorAnswerMissing = issue.kind === "author_answer_required";
  const uniqueConflict = issue.kind === "unique_open_conflict";

  return (
    <section
      className={styles.confirmationDecision}
      ref={issueRef}
      role="alert"
      tabIndex={-1}
    >
      <header>
        <span aria-hidden="true">!</span>
        <div>
          <strong>还有一个判断需要你确认</strong>
          <p>
            {authorAnswerMissing
              ? "你选择了由自己提供答案，但当前还没有填写作者答案。"
              : uniqueConflict
                ? "你选择了“唯一解”，同时又要求“保持开放”，两者不能同时作为生成依据。"
                : "当前还没有确定最终答案由谁提供。"}
          </p>
        </div>
      </header>

      <fieldset>
        <legend>请选择：</legend>
        <label>
          <input
            checked={decision === "author_anchored"}
            name="brief-confirmation-resolution"
            onChange={() => onDecisionChange("author_anchored")}
            type="radio"
          />
          <span>
            <strong>{authorAnswerMissing ? "填写我的答案" : "我已经知道答案"}</strong>
            <small>由你确定答案，后续深稿只能围绕它铺设证据。</small>
          </span>
        </label>
        <label>
          <input
            checked={decision === "agent_proposed"}
            name="brief-confirmation-resolution"
            onChange={() => onDecisionChange("agent_proposed")}
            type="radio"
          />
          <span>
            <strong>让 Agent 在深稿中形成答案</strong>
            <small>Agent 形成可验证候选，最终仍由你决定是否采用。</small>
          </span>
        </label>
        <label>
          <input
            checked={decision === "open"}
            name="brief-confirmation-resolution"
            onChange={() => onDecisionChange("open")}
            type="radio"
          />
          <span>
            <strong>改成开放解释</strong>
            <small>不提前锁死唯一真相，保留多种有依据的解释。</small>
          </span>
        </label>
      </fieldset>

      <footer>
        <button
          disabled={decision === null}
          onClick={onConfirmDecision}
          type="button"
        >
          确认后继续 →
        </button>
      </footer>
    </section>
  );
}
