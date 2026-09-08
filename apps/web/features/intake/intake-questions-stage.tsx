import type { FieldSource, IntakeAnswer, IntakeQuestion } from "./intake-model";
import { SourceBadge } from "./intake-field-shell";
import { Glyph } from "./intake-glyph";
import feedbackStyles from "./brief-confirmation-feedback.module.css";
import stageStyles from "./intake-early-stages.module.css";

interface IntakeQuestionsStageProps {
  intakeFrozen: boolean;
  currentDependencyInvalidation: { brief: boolean; changedAnswerKeys: string[] };
  questionsPending: boolean;
  hardQuestionsResolved: boolean;
  generateBrief: () => void;
  questionGenerationMode: "initial" | "additional" | null;
  error: string | null;
  questionGenerationFailed: boolean;
  sourceText: string;
  visibleQuestions: IntakeQuestion[];
  questionPageIndex: number;
  answers: Record<string, IntakeAnswer>;
  updateAnswer: (questionKey: string, text: string, source?: FieldSource) => void;
  openReachableStep: (target: "idea") => void;
  setQuestionPageIndex: (index: number) => void;
  markQuestionPending: (key: string) => void;
  generateMoreQuestions: () => void;
}

export function IntakeQuestionsStage({
  intakeFrozen,
  currentDependencyInvalidation,
  questionsPending,
  hardQuestionsResolved,
  generateBrief,
  questionGenerationMode,
  error,
  questionGenerationFailed,
  sourceText,
  visibleQuestions,
  questionPageIndex,
  answers,
  updateAnswer,
  openReachableStep,
  setQuestionPageIndex,
  markQuestionPending,
  generateMoreQuestions,
}: IntakeQuestionsStageProps) {
  const questionCount = visibleQuestions.length;
  const visibleQuestionIndex = Math.min(
    questionPageIndex,
    Math.max(0, questionCount - 1),
  );
  const currentQuestion = visibleQuestions[visibleQuestionIndex] ?? null;
  const currentQuestionAnswer = currentQuestion
    ? answers[currentQuestion.key]
    : undefined;
  return (
    <section
      className={`${stageStyles.stepView} ${stageStyles.questionFlow}`}
      aria-labelledby="questions-step-title"
    >
      <header className={stageStyles.questionFlowHero}>
        <div>
          <h1 id="questions-step-title">沿着疑问的微光，辨认故事的方向。</h1>
        </div>
        <p>
          一次只确认一个判断；前后切换不会丢失已经选择或写下的回答。
        </p>
      </header>

      {intakeFrozen ? (
        <p className={feedbackStyles.frozenNotice} role="status">
          创作简报已冻结，回答只读。需要修改时请先建立简报修订。
        </p>
      ) : null}

      {!intakeFrozen && currentDependencyInvalidation.brief ? (
        <section
          aria-label="创作简报需要更新"
          className={stageStyles.dependencyNotice}
          role="status"
        >
          <span aria-hidden="true">!</span>
          <div>
            <b>
              {currentDependencyInvalidation.changedAnswerKeys.length
                ? `已修改 ${currentDependencyInvalidation.changedAnswerKeys.length} 个创作判断`
                : "上游内容已经重新研查"}
            </b>
            <p>
              现有 Brief 与候选不会被删除；下一步会基于新的判断重新整理，未受影响的作者修改继续保留。
            </p>
          </div>
          <button
            disabled={questionsPending || !hardQuestionsResolved}
            onClick={generateBrief}
            type="button"
          >
            更新建案简报
            <Glyph name="arrow" />
          </button>
        </section>
      ) : null}

      {questionsPending ? (
        <div
          aria-label={
            questionGenerationMode === "additional"
              ? "Agent 正在继续研查"
              : "Agent 正在思考"
          }
          aria-live="polite"
          className={stageStyles.agentThinking}
          role="status"
        >
          <span aria-hidden="true" className={stageStyles.agentThinkingMark}>
            <i />
            <i />
            <i />
          </span>
          <div className={stageStyles.agentThinkingCopy}>
            <strong>
              {questionGenerationMode === "additional"
                ? "Agent 正在继续研查"
                : "Agent 正在思考"}
            </strong>
            <p>
              {questionGenerationMode === "additional"
                ? "正在避开已问内容，补充新的方向问题……"
                : "正在从起案原文中提炼会改变方向的关键问题……"}
            </p>
            <span
              aria-hidden="true"
              className={stageStyles.agentThinkingTrace}
              data-testid="agent-thinking-motion"
            >
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
          </div>
        </div>
      ) : null}

      {!questionsPending && questionCount === 0 && !error ? (
        <section
          aria-labelledby="questions-complete-title"
          aria-live="polite"
          className={stageStyles.questionsComplete}
          role="status"
        >
          <span
            aria-hidden="true"
            className={stageStyles.questionsCompleteSweep}
            data-testid="questions-complete-motion"
          />
          <div className={stageStyles.questionsCompleteCopy}>
            <span className={stageStyles.questionsCompleteEyebrow}>
              QUESTION REVIEW / COMPLETE
            </span>
            <h2 id="questions-complete-title">
              <span>当前信息</span>
              <span>已经足够。</span>
            </h2>
            <p>
              Agent 已完成方向缺口研查，没有发现仍需作者确认、且会改变创作方向的问题。
            </p>
            <strong>无需追问；可以直接形成创作简报。</strong>
          </div>
          <div aria-hidden="true" className={stageStyles.questionsCompleteSeal}>
            <span>02</span>
            <Glyph name="check" />
            <small>研查完成</small>
          </div>
          <div className={stageStyles.questionsCompleteMeta}>
            <span>方向缺口 <b>0</b></span>
            <span>下一步 <b>03 / 创作简报</b></span>
          </div>
        </section>
      ) : null}
      {!questionsPending && questionGenerationFailed && !error ? (
        <p className={stageStyles.emptyQuestions}>
          Agent 未能生成关键追问。请返回原稿后重试。
        </p>
      ) : null}

      {currentQuestion ? (
        <section
          aria-label={`关键追问 ${visibleQuestionIndex + 1} / ${questionCount}`}
          className={stageStyles.questionWorkspace}
        >
          <header className={stageStyles.questionContextBar}>
            <div>
              <span>当前起案依据</span>
              <p>{sourceText}</p>
            </div>
            <strong>{visibleQuestionIndex + 1} / {questionCount}</strong>
          </header>

          <div className={stageStyles.questionPrompt} key={currentQuestion.key}>
            <h2>{currentQuestion.prompt}</h2>
            <p>{currentQuestion.impact}</p>

            {currentQuestion.suggestions.length > 0 ? (
              <fieldset
                aria-label={`选择“${currentQuestion.prompt}”的回答`}
                className={stageStyles.questionOptions}
              >
                {currentQuestion.suggestions.map((suggestion) => (
                  <label
                    data-selected={
                      (!currentQuestionAnswer?.pending &&
                        currentQuestionAnswer?.text === suggestion) ||
                      undefined
                    }
                    key={suggestion}
                  >
                    <input
                      checked={
                        !currentQuestionAnswer?.pending &&
                        currentQuestionAnswer?.text === suggestion
                      }
                      disabled={questionsPending || intakeFrozen}
                      name={`question-suggestion-${currentQuestion.key}`}
                      onChange={() =>
                        updateAnswer(
                          currentQuestion.key,
                          suggestion,
                          "agent_suggestion",
                        )
                      }
                      type="radio"
                    />
                    <span>{suggestion}</span>
                  </label>
                ))}
              </fieldset>
            ) : null}

            <label className={stageStyles.questionCustomAnswer}>
              <span>或写下你的补充判断 <small>选填</small></span>
              <textarea
                aria-label={`回答：${currentQuestion.prompt}`}
                disabled={questionsPending || intakeFrozen}
                onChange={(event) =>
                  updateAnswer(currentQuestion.key, event.target.value)
                }
                placeholder="用一句话锁定你的方向……"
                rows={5}
                value={
                  currentQuestionAnswer?.pending
                    ? ""
                    : currentQuestionAnswer?.text ?? ""
                }
              />
            </label>
            <div className={stageStyles.questionAnswerSource}>
              {currentQuestionAnswer ? (
                <SourceBadge source={currentQuestionAnswer.source} />
              ) : (
                <span>等待你的判断</span>
              )}
            </div>
          </div>

          <footer className={stageStyles.questionPager}>
            <button
              className={stageStyles.secondaryAction}
              disabled={questionsPending}
              onClick={() => {
                if (visibleQuestionIndex === 0) openReachableStep("idea");
                else setQuestionPageIndex(visibleQuestionIndex - 1);
              }}
              type="button"
            >
              {visibleQuestionIndex === 0 ? "返回起案" : "← 上一题"}
            </button>
            <div aria-label="追问进度" className={stageStyles.questionDots}>
              {visibleQuestions.map((question, index) => (
                <button
                  aria-current={index === visibleQuestionIndex ? "step" : undefined}
                  aria-label={`前往第 ${index + 1} 题`}
                  key={question.key}
                  onClick={() => setQuestionPageIndex(index)}
                  type="button"
                />
              ))}
            </div>
            <div>
              {!currentQuestion.required ? (
                <button
                  className={stageStyles.textAction}
                  disabled={questionsPending || intakeFrozen}
                  onClick={() => markQuestionPending(currentQuestion.key)}
                  type="button"
                >
                  稍后决定
                </button>
              ) : null}
              {visibleQuestionIndex < questionCount - 1 ? (
                <button
                  className={stageStyles.primaryAction}
                  disabled={
                    questionsPending ||
                    (currentQuestion.required && !currentQuestionAnswer?.text.trim())
                  }
                  onClick={() => setQuestionPageIndex(visibleQuestionIndex + 1)}
                  type="button"
                >
                  下一题 <Glyph name="arrow" />
                </button>
              ) : (
                <button
                  className={stageStyles.primaryAction}
                  disabled={
                    questionsPending ||
                    questionGenerationFailed ||
                    !hardQuestionsResolved ||
                    intakeFrozen
                  }
                  onClick={generateBrief}
                  type="button"
                >
                  {intakeFrozen
                    ? "简报已冻结"
                    : currentDependencyInvalidation.brief
                      ? "更新建案简报"
                      : "形成创作简报"}
                  <Glyph name="arrow" />
                </button>
              )}
            </div>
          </footer>
        </section>
      ) : null}

      {questionCount > 0 ? (
        <section className={stageStyles.questionAuxiliaryActions}>
          <div><strong>需要再确认一层？</strong><span>已有问题和回答不会被改写。</span></div>
          <button
            disabled={questionsPending || intakeFrozen}
            onClick={generateMoreQuestions}
            type="button"
          >
            {questionGenerationMode === "additional" ? "正在补充问题…" : "再生成一些问题"}
          </button>
        </section>
      ) : null}

      {error ? (
        <p className={stageStyles.inlineError} role="alert">
          {error}
        </p>
      ) : null}

      {questionCount === 0 ? (
        <footer className={stageStyles.stepActions}>
        <div>
          <button
            className={stageStyles.secondaryAction}
            disabled={questionsPending}
            onClick={() => openReachableStep("idea")}
            type="button"
          >
            返回原稿
          </button>
          <button
            className={stageStyles.primaryAction}
            disabled={
              questionsPending ||
              questionGenerationFailed ||
              !hardQuestionsResolved ||
              intakeFrozen
            }
            onClick={generateBrief}
            type="button"
          >
            {intakeFrozen
              ? "简报已冻结"
              : currentDependencyInvalidation.brief
                ? "更新建案简报"
                : "形成创作简报"}
            <Glyph name="arrow" />
          </button>
        </div>
        </footer>
      ) : null}
    </section>
  );
}
