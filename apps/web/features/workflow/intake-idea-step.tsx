import { StatusBadge } from "@/components/archive-ui";
import type {
  BriefPolishResult,
  PolishMode,
  SourceRecordView,
  TaskView,
} from "@/lib/api-client";

import { buildTextDiff, polishModes } from "./intake-model";

import styles from "./brief-intake-workspace.module.css";

interface IntakeIdeaStepProps {
  sourceText: string;
  savedSource: SourceRecordView | null;
  providerReady: boolean;
  busy: boolean;
  polishTask: TaskView | null;
  polishResult: BriefPolishResult | null;
  polishDraft: string;
  polishMode: PolishMode;
  polishReviewOpen: boolean;
  polishCandidateStale: boolean;
  closed: boolean;
  error: string | null;
  onSourceChange: (value: string) => void;
  onPolish: () => void;
  onOpenSettings: () => void;
  onOpenPolish: () => void;
  onContinue: () => void;
  onPolishDraftChange: (value: string) => void;
  onPolishModeChange: (value: PolishMode) => void;
  onClosePolish: () => void;
  onAdoptPolish: () => void;
  onOpenBrief: () => void;
  onStartNewCase: () => void;
}

const activeStatuses = new Set<TaskView["status"]>([
  "queued",
  "running",
  "cancelling",
]);

export function IntakeIdeaStep({
  sourceText,
  savedSource,
  providerReady,
  busy,
  polishTask,
  polishResult,
  polishDraft,
  polishMode,
  polishReviewOpen,
  polishCandidateStale,
  closed,
  error,
  onSourceChange,
  onPolish,
  onOpenSettings,
  onOpenPolish,
  onContinue,
  onPolishDraftChange,
  onPolishModeChange,
  onClosePolish,
  onAdoptPolish,
  onOpenBrief,
  onStartNewCase,
}: IntakeIdeaStepProps) {
  const sourceSaved =
    savedSource !== null && savedSource.content_text === sourceText.trim();
  const polishRunning = Boolean(
    polishTask && activeStatuses.has(polishTask.status),
  );
  const comparisonOpen = !closed && polishReviewOpen;
  const comparisonStatus = polishResult
    ? "待你决定"
    : polishTask?.status === "failed"
      ? "润色未完成"
      : "生成中";
  const comparisonOriginal =
    savedSource?.content_text ?? sourceText.trim();
  const agentDiff = buildTextDiff(
    comparisonOriginal,
    polishResult?.polished_text ?? "",
  );
  const agentMadeNoChanges = Boolean(polishResult && agentDiff.changeCount === 0);
  const draftMatchesOriginal = polishDraft.trim() === comparisonOriginal.trim();
  const introducedDetails = polishResult?.introduced_details ?? [];
  const effectiveMode = polishResult?.polish_mode ?? polishMode;
  const modeLabel =
    polishModes.find((mode) => mode.value === effectiveMode)?.label ?? "表达优化";
  const providerLabel = polishTask?.provider === "deepseek" ? "DeepSeek" : "OpenAI";

  return (
    <section
      aria-labelledby="intake-idea-title"
      className={styles.stepSheet}
      data-comparing={comparisonOpen}
    >
      <header className={styles.stepSheetHeader}>
        <div>
          <span>
            {comparisonOpen ? "STEP 01 / 原稿对校" : "STEP 01 / 起案原件"}
          </span>
          <h2 id="intake-idea-title">
            {comparisonOpen
              ? "并排核对原文与 Agent 校样。"
              : "先把最初的念头留在卷宗里。"}
          </h2>
        </div>
        <StatusBadge tone={comparisonOpen ? "red" : "dark"}>
          {comparisonOpen ? comparisonStatus : closed ? "只读原件" : "你的原文"}
        </StatusBadge>
      </header>

      {comparisonOpen ? (
        <div
          aria-label="Agent 润色左右对照"
          className={styles.polishInlineBody}
        >
          {polishResult ? (
            <div
              className={styles.polishAuditRibbon}
              data-unchanged={agentMadeNoChanges}
              role="status"
            >
              <b>
                {agentMadeNoChanges
                  ? "Agent 已完成审阅：原文表达清晰，本次未建议文字调整。"
                  : `${providerLabel} 已审阅 · ${modeLabel} · 修改 ${agentDiff.changeCount} 处`}
              </b>
              <span>
                {agentMadeNoChanges
                  ? `${providerLabel} 已完成 ${modeLabel}，你仍可在候选区继续编辑。`
                  : `新增 ${agentDiff.insertedCharacters} 字 · 删除 ${agentDiff.deletedCharacters} 字 · 保留 ${polishResult.ambiguities.length} 项歧义`}
              </span>
            </div>
          ) : null}
          <div className={styles.polishInlineColumns}>
            <section className={styles.polishPane}>
              <header>
                <span>你的原文</span>
                <small>只读保留，不会被覆盖</small>
              </header>
              <textarea
                aria-label="当前作者原稿"
                readOnly
                rows={10}
                value={comparisonOriginal}
              />
              <footer>
                <span>{comparisonOriginal.length} 字</span>
                <span>原稿保持不动</span>
              </footer>
            </section>

            <section className={styles.polishPane} data-agent="true">
              <header>
                <span>Agent 润色</span>
                <small>
                  {polishResult
                    ? "采用前可以继续编辑"
                    : polishTask?.status === "failed"
                      ? "本次没有形成可用校样"
                      : "正在形成独立校样"}
                </small>
              </header>
              {polishResult ? (
                <div className={styles.polishDraftStack}>
                  {!agentMadeNoChanges ? (
                    <div
                      aria-label="Agent 修改差异"
                      className={styles.polishDiff}
                    >
                      {agentDiff.segments.map((segment, index) => {
                        if (segment.type === "delete") {
                          return <del key={`${segment.type}-${index}`}>{segment.text}</del>;
                        }
                        if (segment.type === "insert") {
                          return <ins key={`${segment.type}-${index}`}>{segment.text}</ins>;
                        }
                        return <span key={`${segment.type}-${index}`}>{segment.text}</span>;
                      })}
                    </div>
                  ) : null}
                  <textarea
                    aria-label="编辑 Agent 润色工作稿"
                    onChange={(event) => onPolishDraftChange(event.target.value)}
                    rows={10}
                    value={polishDraft}
                  />
                </div>
              ) : (
                <div
                  className={styles.polishPending}
                  data-status={polishTask?.status ?? "queued"}
                >
                  {polishRunning ? (
                    <span className={styles.taskPulse} aria-hidden="true" />
                  ) : null}
                  <div>
                    <b>
                      {polishTask?.status === "failed"
                        ? "Agent 润色未能完成"
                        : "Agent 正在润色"}
                    </b>
                    <p>
                      {polishTask?.status === "failed"
                        ? "关闭对照后可以重试，原稿仍保持不动。"
                        : "校样完成后会直接出现在这里。"}
                    </p>
                  </div>
                </div>
              )}
              <footer>
                <span>{polishDraft.trim().length} 字</span>
                <span>{polishResult ? "独立候选" : "等待校样"}</span>
              </footer>
            </section>
          </div>

          {polishResult ? (
            <div className={styles.polishNotes}>
              <span>
                <b>保真摘要</b>
                <p>{polishResult.preserved_intent_summary}</p>
              </span>
              <span>
                <b>仍有歧义</b>
                <p>
                  {polishResult.ambiguities.length
                    ? polishResult.ambiguities.join("；")
                    : "未标出需要作者补充的歧义。"}
                </p>
              </span>
              {effectiveMode === "narrative_enhance" ? (
                <span data-warning={introducedDetails.length > 0}>
                  <b>新增细节审阅</b>
                  <p>
                    {introducedDetails.length
                      ? introducedDetails.join("；")
                      : "本次叙事增强未加入原稿之外的细节。"}
                  </p>
                </span>
              ) : null}
            </div>
          ) : null}

          {polishCandidateStale ? (
            <p className={styles.dialogError} role="alert">
              当前原稿已变化。旧校样继续保留，但不能再采用。
            </p>
          ) : null}
        </div>
      ) : (
        <div className={styles.ideaBody}>
          <label htmlFor="casefile-source-text">
            <span>最初想法</span>
            <small>
              {closed
                ? "这份原件已进入正式审阅，只读保留。"
                : "Agent 只生成独立候选；你的原文不会被覆盖。"}
            </small>
          </label>
          <textarea
            autoFocus={!closed}
            id="casefile-source-text"
            onChange={(event) => onSourceChange(event.target.value)}
            placeholder="例如：一名档案员发现三份可靠记录都指向一段不存在的时间……"
            readOnly={closed}
            rows={10}
            value={sourceText}
          />
          <div className={styles.ideaMeta}>
            <span>{sourceText.trim().length} 字</span>
            <span>
              {closed
                ? "已锁定为建案原件"
                : sourceSaved
                ? savedSource.source_kind === "human_original"
                  ? "原件已保存"
                  : "作者修订已保存"
                : savedSource
                  ? "有尚未保存的修改"
                  : "保存后生成不可变原件"}
            </span>
          </div>
        </div>
      )}

      {!closed && !comparisonOpen && providerReady ? (
        <fieldset className={styles.polishModeRail}>
          <legend>润色强度</legend>
          {polishModes.map((mode) => (
            <label data-selected={polishMode === mode.value} key={mode.value}>
              <input
                checked={polishMode === mode.value}
                disabled={busy || polishRunning}
                name="polish-mode"
                onChange={() => onPolishModeChange(mode.value)}
                type="radio"
                value={mode.value}
              />
              <span>
                <b>{mode.label}</b>
                <small>{mode.hint}</small>
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}

      {closed ? (
        <div className={styles.closedIntakeNotice} role="status">
          <b>这份建案已进入正式审阅</b>
          <span>原文、追问和候选已锁定为历史。要润色新的想法，请新建案件。</span>
        </div>
      ) : !comparisonOpen && polishTask ? (
        <div className={styles.taskRibbon} data-status={polishTask.status}>
          <b>Agent 润色</b>
          <span>
            {polishRunning
              ? "正在形成独立校样，原稿保持不动。"
              : polishTask.status === "succeeded"
                ? "校样已完成，等待你决定是否采用。"
                : polishTask.status === "failed"
                  ? "本次润色失败，可以重试或直接继续。"
                  : "任务已结束。"}
          </span>
          {polishResult && !polishCandidateStale ? (
            <button onClick={onOpenPolish} type="button">
              查看校样
            </button>
          ) : null}
        </div>
      ) : null}

      {!closed && error ? (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      ) : null}

      <footer className={styles.stepActions}>
        {comparisonOpen ? (
          <>
            <button
              className={styles.secondaryAction}
              onClick={onClosePolish}
              type="button"
            >
              保留原稿
            </button>
            <button
              className={styles.primaryAction}
              disabled={
                busy ||
                !polishResult ||
                polishCandidateStale ||
                !polishDraft.trim() ||
                draftMatchesOriginal
              }
              onClick={onAdoptPolish}
              type="button"
            >
              <span>
                {busy
                  ? "正在记录…"
                  : draftMatchesOriginal
                    ? "原文无需替换"
                  : polishResult
                    ? "采用润色稿"
                    : comparisonStatus}
              </span>
              <b aria-hidden="true">→</b>
            </button>
          </>
        ) : closed ? (
          <>
            <button
              className={styles.secondaryAction}
              onClick={onOpenBrief}
              type="button"
            >
              返回正式审阅
            </button>
            <button
              className={styles.primaryAction}
              onClick={onStartNewCase}
              type="button"
            >
              <span>新建案件再润色</span>
              <b aria-hidden="true">→</b>
            </button>
          </>
        ) : (
          <>
            {providerReady ? (
              <button
                className={styles.secondaryAction}
                disabled={busy || polishRunning || !sourceText.trim()}
                onClick={onPolish}
                title="保存当前原文并生成独立润色校样"
                type="button"
              >
                {polishRunning
                  ? "润色中…"
                  : polishTask?.status === "failed"
                    ? "重试 Agent 润色"
                    : "Agent 润色"}
              </button>
            ) : (
              <button
                className={styles.secondaryAction}
                onClick={onOpenSettings}
                type="button"
              >
                打开 Agent 设置
              </button>
            )}
            <button
              className={styles.primaryAction}
              disabled={busy || !sourceText.trim()}
              onClick={onContinue}
              type="button"
            >
              <span>{busy ? "正在保存…" : "进入关键追问"}</span>
              <b aria-hidden="true">→</b>
            </button>
          </>
        )}
      </footer>
    </section>
  );
}
