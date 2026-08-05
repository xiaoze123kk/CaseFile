"use client";

import { useRef, useState } from "react";

import { useDemoPrototype } from "@/features/demo-prototype/demo-prototype-provider";

import {
  atomicReviewComplete,
  extractAuthorAnchors,
  extractCreativeConstraints,
  resolutionModes,
  reviewFieldBlockers,
  type PrototypeBriefReview,
  type PrototypeConstraintStrength,
  type PrototypeResolutionMode,
} from "./intake-prototype-model";
import styles from "./prototype-late-stages.module.css";

type ReviewTextField =
  | "creativeIntent"
  | "reasoningProposition"
  | "authorAnswer"
  | "boundaryText";

function originLabel(origin: "agent" | "manual" | "saved") {
  if (origin === "manual") return "人工新增";
  if (origin === "saved") return "已保存";
  return "Agent 拆解";
}

export function BriefReviewStage() {
  const {
    state,
    patchState,
    setReview,
    saveReview,
    freezeReview,
  } = useDemoPrototype();
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(
    "逐条核对后先保存审阅，再冻结为不可变生成依据。",
  );
  const manualSequence = useRef(0);
  const reviewState = state.review;

  if (!reviewState) {
    return (
      <section className={styles.emptyReview}>
        <span>审阅页尚未建立</span>
        <h1>先从创作简报进入审阅。</h1>
        <button
          onClick={() => patchState({ step: "confirmation" })}
          type="button"
        >
          返回创作简报
        </button>
      </section>
    );
  }

  const review: PrototypeBriefReview = reviewState;

  function commit(next: PrototypeBriefReview) {
    setReview(next);
    setError(null);
  }

  function updateText(field: ReviewTextField, value: string) {
    const invalidatesAnchors = field === "authorAnswer";
    const invalidatesConstraints = field === "boundaryText";
    commit({
      ...review,
      [field]: value,
      authorAnchors: invalidatesAnchors ? [] : review.authorAnchors,
      creativeConstraints: invalidatesConstraints
        ? []
        : review.creativeConstraints,
      dirty: true,
      saved: false,
    });
    if (invalidatesAnchors || invalidatesConstraints) {
      setNotice("原文已改变，旧拆解已失效；请重新拆解后保存。 ");
    }
  }

  function updateResolutionMode(value: PrototypeResolutionMode) {
    commit({
      ...review,
      resolutionMode: value,
      authorAnswer:
        value === "author_anchored" ? review.authorAnswer : "",
      authorAnchors:
        value === "author_anchored" ? review.authorAnchors : [],
      dirty: true,
      saved: false,
    });
  }

  function reextract() {
    commit({
      ...review,
      authorAnchors:
        review.resolutionMode === "author_anchored"
          ? extractAuthorAnchors(review.authorAnswer)
          : [],
      creativeConstraints: extractCreativeConstraints(review.boundaryText),
      dirty: true,
      saved: false,
    });
    setNotice("Fixture Agent 已重新拆解；请逐条确认并保存审阅。 ");
  }

  function addAnchor() {
    manualSequence.current += 1;
    commit({
      ...review,
      authorAnchors: [
        ...review.authorAnchors,
        {
          id: `anchor-manual-${manualSequence.current}`,
          statement: "",
          origin: "manual",
        },
      ],
      dirty: true,
      saved: false,
    });
  }

  function addConstraint() {
    manualSequence.current += 1;
    commit({
      ...review,
      creativeConstraints: [
        ...review.creativeConstraints,
        {
          id: `constraint-manual-${manualSequence.current}`,
          statement: "",
          strength: "hard",
          origin: "manual",
        },
      ],
      dirty: true,
      saved: false,
    });
  }

  function handleSave() {
    const blockers = reviewFieldBlockers(review);
    if (blockers.length) {
      setError(`保存前请补齐：${blockers.join("、")}。`);
      return;
    }
    saveReview();
    setError(null);
    setNotice("审阅已保存。原子项完整后即可冻结。 ");
  }

  function handleFreeze() {
    if (review.dirty || !review.saved) {
      setError("请先保存当前审阅修改。 ");
      return;
    }
    if (!atomicReviewComplete(review)) {
      setError("请先确认底牌与创作边界的原子项。 ");
      return;
    }
    if (!freezeReview()) {
      setError("当前简报尚未通过冻结门禁。 ");
      return;
    }
    setError(null);
  }

  const atomicsComplete = atomicReviewComplete(review);
  const freezeReady =
    review.saved &&
    !review.dirty &&
    atomicsComplete &&
    reviewFieldBlockers(review).length === 0;

  return (
    <section className={styles.reviewStage} aria-labelledby="review-stage-title">
      <header className={styles.stageHeader}>
        <div>
          <span>创作简报审阅 · 作者控制</span>
          <h1 id="review-stage-title">把生成依据逐条钉在纸面上。</h1>
        </div>
        <dl>
          <div>
            <dt>简报版本</dt>
            <dd>V{String(state.workingBriefVersion).padStart(2, "0")}</dd>
          </div>
          <div>
            <dt>审阅状态</dt>
            <dd>{review.dirty ? "有未保存修改" : review.saved ? "已保存" : "待保存"}</dd>
          </div>
        </dl>
      </header>

      <div className={styles.reviewSpread}>
        <form
          className={styles.reviewSheet}
          onSubmit={(event) => {
            event.preventDefault();
            handleSave();
          }}
        >
          <div className={styles.paperIndex} aria-hidden="true">
            <span>BRIEF</span>
            <b>04</b>
          </div>
          <header className={styles.sheetHeader}>
            <div>
              <small>目标无关创作简报</small>
              <strong>{review.reasoningProposition || "等待核心命题"}</strong>
            </div>
            <span data-state={freezeReady ? "ready" : "editing"}>
              {freezeReady ? "可冻结" : review.dirty ? "待保存" : "待确认"}
            </span>
          </header>

          <div className={styles.reviewFields}>
            <label>
              <span><b>01</b><strong>创作意图</strong><em>作者确认</em></span>
              <textarea
                aria-label="审阅创作意图"
                onChange={(event) => updateText("creativeIntent", event.target.value)}
                rows={3}
                value={review.creativeIntent}
              />
            </label>
            <label>
              <span><b>02</b><strong>核心推理命题</strong><em>作者确认</em></span>
              <textarea
                aria-label="审阅核心推理命题"
                onChange={(event) => updateText("reasoningProposition", event.target.value)}
                rows={3}
                value={review.reasoningProposition}
              />
            </label>
            <label>
              <span><b>03</b><strong>结论处理方式</strong><em>作者决定</em></span>
              <select
                aria-label="审阅结论处理方式"
                onChange={(event) =>
                  updateResolutionMode(event.target.value as PrototypeResolutionMode)
                }
                value={review.resolutionMode}
              >
                {resolutionModes.map((mode) => (
                  <option key={mode.value} value={mode.value}>{mode.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span><b>04</b><strong>作者底牌原文</strong><em>硬约束来源</em></span>
              <textarea
                aria-label="审阅作者底牌原文"
                disabled={review.resolutionMode !== "author_anchored"}
                onChange={(event) => updateText("authorAnswer", event.target.value)}
                placeholder="只有按作者底牌展开时需要填写。"
                rows={3}
                value={review.authorAnswer}
              />
            </label>
            <label className={styles.wideField}>
              <span><b>05</b><strong>创作边界原文</strong><em>硬约束或软偏好</em></span>
              <textarea
                aria-label="审阅创作边界原文"
                onChange={(event) => updateText("boundaryText", event.target.value)}
                placeholder="每行一项；修改后需要重新拆解。"
                rows={4}
                value={review.boundaryText}
              />
            </label>
          </div>

          <section className={styles.atomicSection} aria-label="原子底牌审阅">
            <header>
              <div><span>作者锚点</span><strong>原子底牌</strong></div>
              <button onClick={addAnchor} type="button">＋ 人工新增</button>
            </header>
            {review.authorAnchors.length ? (
              <div className={styles.atomicRows}>
                {review.authorAnchors.map((anchor) => (
                  <div key={anchor.id}>
                    <small data-origin={anchor.origin}>{originLabel(anchor.origin)}</small>
                    <input
                      aria-label={`底牌原子项 ${anchor.id}`}
                      onChange={(event) =>
                        commit({
                          ...review,
                          authorAnchors: review.authorAnchors.map((item) =>
                            item.id === anchor.id
                              ? { ...item, statement: event.target.value }
                              : item,
                          ),
                          dirty: true,
                          saved: false,
                        })
                      }
                      value={anchor.statement}
                    />
                    <b>硬约束</b>
                    <button
                      aria-label={`删除底牌原子项 ${anchor.id}`}
                      onClick={() =>
                        commit({
                          ...review,
                          authorAnchors: review.authorAnchors.filter(
                            (item) => item.id !== anchor.id,
                          ),
                          dirty: true,
                          saved: false,
                        })
                      }
                      type="button"
                    >×</button>
                  </div>
                ))}
              </div>
            ) : (
              <p className={styles.atomicEmpty}>
                {review.authorAnswer
                  ? "原文已改变，请重新拆解或人工新增。"
                  : "当前没有需要原子化的作者底牌。"}
              </p>
            )}
          </section>

          <section className={styles.atomicSection} aria-label="原子创作约束审阅">
            <header>
              <div><span>创作边界</span><strong>原子创作约束</strong></div>
              <button onClick={addConstraint} type="button">＋ 人工新增</button>
            </header>
            {review.creativeConstraints.length ? (
              <div className={styles.atomicRows}>
                {review.creativeConstraints.map((constraint) => (
                  <div key={constraint.id}>
                    <small data-origin={constraint.origin}>{originLabel(constraint.origin)}</small>
                    <input
                      aria-label={`创作约束 ${constraint.id}`}
                      onChange={(event) =>
                        commit({
                          ...review,
                          creativeConstraints: review.creativeConstraints.map((item) =>
                            item.id === constraint.id
                              ? { ...item, statement: event.target.value }
                              : item,
                          ),
                          dirty: true,
                          saved: false,
                        })
                      }
                      value={constraint.statement}
                    />
                    <select
                      aria-label={`约束强度 ${constraint.id}`}
                      onChange={(event) =>
                        commit({
                          ...review,
                          creativeConstraints: review.creativeConstraints.map((item) =>
                            item.id === constraint.id
                              ? {
                                  ...item,
                                  strength: event.target.value as PrototypeConstraintStrength,
                                }
                              : item,
                          ),
                          dirty: true,
                          saved: false,
                        })
                      }
                      value={constraint.strength}
                    >
                      <option value="hard">硬约束</option>
                      <option value="soft">软偏好</option>
                    </select>
                    <button
                      aria-label={`删除创作约束 ${constraint.id}`}
                      onClick={() =>
                        commit({
                          ...review,
                          creativeConstraints: review.creativeConstraints.filter(
                            (item) => item.id !== constraint.id,
                          ),
                          dirty: true,
                          saved: false,
                        })
                      }
                      type="button"
                    >×</button>
                  </div>
                ))}
              </div>
            ) : (
              <p className={styles.atomicEmpty}>
                {review.boundaryText
                  ? "原文已改变，请重新拆解或人工新增。"
                  : "当前没有额外创作边界。"}
              </p>
            )}
          </section>

          <footer className={styles.reviewActions}>
            <button onClick={reextract} type="button">重新拆解底牌与边界</button>
            <div>
              <button type="submit">保存审阅</button>
              <button
                data-primary="true"
                disabled={!freezeReady}
                onClick={handleFreeze}
                type="button"
              >确认并冻结 →</button>
            </div>
          </footer>
          {error ? <p className={styles.formError} role="alert">{error}</p> : null}
        </form>

        <aside className={styles.reviewLedger}>
          <header><span>生成门禁</span><strong>作者确认卷</strong></header>
          <ol>
            <li data-complete={atomicsComplete}>
              <b>{atomicsComplete ? "✓" : "1"}</b>
              <span><strong>原子拆解</strong><small>{atomicsComplete ? "结构完整" : "等待确认"}</small></span>
            </li>
            <li data-complete={review.saved && !review.dirty}>
              <b>{review.saved && !review.dirty ? "✓" : "2"}</b>
              <span><strong>保存审阅</strong><small>{review.dirty ? "存在修改" : review.saved ? "已保存" : "尚未保存"}</small></span>
            </li>
            <li data-complete={freezeReady}>
              <b>{freezeReady ? "✓" : "3"}</b>
              <span><strong>冻结简报</strong><small>{freezeReady ? "可以冻结" : "门禁未通过"}</small></span>
            </li>
          </ol>
          <section>
            <span>待决定事项</span>
            {review.pendingDecisions.length ? (
              <ul>{review.pendingDecisions.map((item) => <li key={item}>{item}</li>)}</ul>
            ) : <p>没有被隐藏的待决定事项。</p>}
          </section>
          <section>
            <span>简报补充页</span>
            <dl>
              <div><dt>核心卖点</dt><dd>{state.brief.sellingPoints || "尚未补充"}</dd></div>
              <div><dt>内容骨架</dt><dd>{state.brief.outline || "尚未补充"}</dd></div>
              <div><dt>预计规模</dt><dd>{state.brief.scopeEstimate || "尚未估算"}</dd></div>
            </dl>
          </section>
          <p className={styles.ledgerNotice} aria-live="polite">{notice}</p>
          <button
            className={styles.backToBrief}
            onClick={() => patchState({ step: "confirmation" })}
            type="button"
          >← 返回简报成案</button>
        </aside>
      </div>
    </section>
  );
}

