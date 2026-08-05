import { useMemo, useState } from "react";

import { StatusBadge } from "@/components/archive-ui";
import type {
  BriefIntakeQuestionView,
  TaskView,
} from "@/lib/api-client";

import { IntakeSourceBadge } from "./intake-source-badge";
import styles from "./brief-intake-workspace.module.css";

interface IntakeQuestionsStepProps {
  sourceText: string;
  questions: BriefIntakeQuestionView[];
  hardQuestionsResolved: boolean;
  providerReady: boolean;
  busy: boolean;
  questionsTask: TaskView | null;
  synthesizeTask: TaskView | null;
  error: string | null;
  onBack: () => void;
  onOpenSettings: () => void;
  onRetryQuestions: () => void;
  onAnswer: (
    question: BriefIntakeQuestionView,
    answer:
      | { mode: "answer"; text: string }
      | { mode: "suggestion"; suggestionIndex: number }
      | { mode: "pending" },
  ) => void;
  onGenerate: () => void;
  onManualContinue: () => void;
}

const runningStatuses = new Set<TaskView["status"]>([
  "queued",
  "running",
  "cancelling",
]);

function taskRunning(task: TaskView | null) {
  return Boolean(task && runningStatuses.has(task.status));
}

export function IntakeQuestionsStep({
  sourceText,
  questions,
  hardQuestionsResolved,
  providerReady,
  busy,
  questionsTask,
  synthesizeTask,
  error,
  onBack,
  onOpenSettings,
  onRetryQuestions,
  onAnswer,
  onGenerate,
  onManualContinue,
}: IntakeQuestionsStepProps) {
  const [draftAnswers, setDraftAnswers] = useState<Record<string, string>>({});
  const questionsRunning = taskRunning(questionsTask);
  const synthesisRunning = taskRunning(synthesizeTask);

  const previewRows = useMemo(
    () => [
      {
        label: "一句话概念",
        value:
          sourceText
            .split(/\r?\n/u)
            .map((line) => line.trim())
            .find(Boolean) ?? "尚未形成",
        source: "user_original" as const,
      },
      ...questions.map((question) => ({
        label: question.required ? "关键方向" : "规模与偏好",
        value: question.answer_text ?? "暂时不决定",
        source: question.answer_source ?? ("unresolved" as const),
      })),
    ],
    [questions, sourceText],
  );

  return (
    <section className={styles.stepSheet} aria-labelledby="intake-questions-title">
      <header className={styles.stepSheetHeader}>
        <div>
          <span>STEP 02 / 方向核验</span>
          <h2 id="intake-questions-title">只问真正会改变方向的问题。</h2>
        </div>
        <StatusBadge tone={hardQuestionsResolved ? "dark" : "red"}>
          {hardQuestionsResolved ? "硬问题已解决" : "等待关键回答"}
        </StatusBadge>
      </header>

      <div className={styles.questionsBody}>
        {questionsRunning && questions.length === 0 ? (
          <div className={styles.taskWaiting} aria-live="polite">
            <span className={styles.taskPulse} aria-hidden="true" />
            <div>
              <b>Agent 正在核对方向</b>
              <p>最多生成两道追问，且最多一道会阻止继续。</p>
            </div>
          </div>
        ) : null}

        {!questionsRunning && questions.length === 0 ? (
          <div className={styles.emptyQuestions}>
            <b>
              {questionsTask?.status === "failed"
                ? "本次追问没有完成"
                : questionsTask?.status === "succeeded"
                  ? "当前方向不需要追加关键追问"
                  : providerReady
                    ? "尚未生成关键追问"
                    : "未配置 Agent，也可以手动继续"}
            </b>
            <p>
              {questionsTask?.status === "failed"
                ? "原文仍已保存。可以重试，也可以直接进入人工简报。"
                : "人工继续不会制造本地假 Agent 结果；你将在下一步填写必要字段。"}
            </p>
            <div className={styles.emptyQuestionActions}>
              {providerReady && questionsTask?.status !== "succeeded" ? (
                <button disabled={busy} onClick={onRetryQuestions} type="button">
                  重新生成追问
                </button>
              ) : null}
              {!providerReady || questionsTask?.status === "failed" ? (
                <button onClick={onOpenSettings} type="button">
                  {providerReady ? "检查模型设置" : "打开 Agent 设置"}
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        <div className={styles.questionList}>
          {questions.map((question) => {
            const draft =
              draftAnswers[question.question_key] ?? question.answer_text ?? "";
            const resolved = question.answer_status !== "unanswered";
            return (
              <article
                className={styles.questionCard}
                data-resolved={resolved}
                key={question.question_key}
              >
                <header>
                  <span>Q{String(question.ordinal).padStart(2, "0")}</span>
                  <div>
                    <b>{question.prompt}</b>
                    <p>{question.impact}</p>
                  </div>
                  <StatusBadge tone={question.required ? "red" : "neutral"}>
                    {question.required ? "必须回答" : "可以暂缓"}
                  </StatusBadge>
                </header>

                <div className={styles.questionAnswer}>
                  <label htmlFor={`answer-${question.question_key}`}>
                    你的回答
                  </label>
                  <textarea
                    id={`answer-${question.question_key}`}
                    onChange={(event) =>
                      setDraftAnswers((current) => ({
                        ...current,
                        [question.question_key]: event.target.value,
                      }))
                    }
                    placeholder="用一句话锁定你的方向……"
                    rows={2}
                    value={draft}
                  />
                  <button
                    disabled={busy || !draft.trim()}
                    onClick={() =>
                      onAnswer(question, { mode: "answer", text: draft })
                    }
                    type="button"
                  >
                    保存回答
                  </button>
                </div>

                {question.suggestions.length ? (
                  <div className={styles.suggestionRow}>
                    <span>采用建议</span>
                    {question.suggestions.map((suggestion, index) => (
                      <button
                        disabled={busy}
                        key={suggestion}
                        onClick={() =>
                          {
                            setDraftAnswers((current) => ({
                              ...current,
                              [question.question_key]: suggestion,
                            }));
                            onAnswer(question, {
                              mode: "suggestion",
                              suggestionIndex: index,
                            });
                          }
                        }
                        type="button"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                ) : null}

                <footer>
                  {question.answer_source ? (
                    <IntakeSourceBadge source={question.answer_source} />
                  ) : (
                    <IntakeSourceBadge source="unresolved" />
                  )}
                  <span>
                    {question.answer_text ??
                      (question.required
                        ? "还需要你的决定"
                        : "未回答时会进入待决定事项")}
                  </span>
                  {!question.required ? (
                    <button
                      disabled={busy}
                      onClick={() => onAnswer(question, { mode: "pending" })}
                      type="button"
                    >
                      暂不决定
                    </button>
                  ) : null}
                </footer>
              </article>
            );
          })}
        </div>

        <details className={styles.briefPreview}>
          <summary>
            <span>简报预览</span>
            <small>查看当前原文与回答将如何进入候选</small>
          </summary>
          <div>
            {previewRows.map((row, index) => (
              <section key={`${row.label}-${index}`}>
                <header>
                  <b>{row.label}</b>
                  <IntakeSourceBadge source={row.source} />
                </header>
                <p>{row.value}</p>
              </section>
            ))}
          </div>
        </details>

        {synthesisRunning ? (
          <div className={styles.taskRibbon} data-status="running">
            <b>正在整理创作简报</b>
            <span>原文、回答和约束已冻结为本次任务输入。</span>
          </div>
        ) : null}
        {synthesizeTask?.status === "failed" ? (
          <div className={styles.taskRibbon} data-status="failed">
            <b>Agent 简报未完成</b>
            <span>原文与回答仍已保存；可重试，也可改用人工简报继续。</span>
            <div className={styles.ribbonActions}>
              <button onClick={onOpenSettings} type="button">
                检查设置
              </button>
              <button onClick={onManualContinue} type="button">
                人工整理
              </button>
            </div>
          </div>
        ) : null}
        {error ? (
          <p className={styles.inlineError} role="alert">
            {error}
          </p>
        ) : null}
      </div>

      <footer className={styles.stepActions}>
        <button className={styles.secondaryAction} onClick={onBack} type="button">
          ← 返回原文
        </button>
        {providerReady ? (
          <button
            className={styles.primaryAction}
            disabled={busy || synthesisRunning || !hardQuestionsResolved}
            onClick={onGenerate}
            type="button"
          >
            <span>
              {synthesisRunning
                ? "正在整理…"
                : synthesizeTask?.status === "failed"
                  ? "重试生成简报"
                  : "生成创作简报"}
            </span>
            <b aria-hidden="true">→</b>
          </button>
        ) : (
          <button
            className={styles.primaryAction}
            disabled={busy || !hardQuestionsResolved}
            onClick={onManualContinue}
            type="button"
          >
            <span>手动整理简报</span>
            <b aria-hidden="true">→</b>
          </button>
        )}
      </footer>
    </section>
  );
}
