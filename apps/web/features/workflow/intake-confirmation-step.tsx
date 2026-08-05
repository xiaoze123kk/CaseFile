"use client";

import { type ReactNode, useMemo, useState } from "react";

import { StatusBadge } from "@/components/archive-ui";
import type {
  BriefIntakeCandidateContent,
  BriefIntakeCandidateView,
  BriefIntakeConstraintCategory,
  BriefIntakeView,
  ResolutionMode,
  TaskView,
} from "@/lib/api-client";

import {
  candidateOriginLabels,
  cloneCandidateContent,
  constraintCategories,
  discardCandidateTarget,
  missingCandidateHardFields,
  resolutionModeLabels,
  resolutionModeHints,
  splitLines,
} from "./intake-model";
import { IntakeSourceBadge } from "./intake-source-badge";
import styles from "./brief-intake-workspace.module.css";

interface IntakeConfirmationStepProps {
  intake: BriefIntakeView;
  currentCandidate: BriefIntakeCandidateView | null;
  manualSeed: BriefIntakeCandidateContent;
  providerReady: boolean;
  busy: boolean;
  synthesizeTask: TaskView | null;
  error: string | null;
  onBack: () => void;
  onOpenSettings: () => void;
  onCreateManualCandidate: (
    content: BriefIntakeCandidateContent,
    parentCandidateId: number | null,
  ) => void;
  onSaveCandidate: (candidateId: number) => void;
  onActivateCandidate: (candidateId: number) => void;
  onDialogueRevision: (candidateId: number, instruction: string) => void;
  onAdoptCandidate: (candidateId: number) => void;
}

const runningStatuses = new Set<TaskView["status"]>([
  "queued",
  "running",
  "cancelling",
]);

function sourceFor(
  content: BriefIntakeCandidateContent,
  field: keyof BriefIntakeCandidateContent["field_sources"],
) {
  return content.field_sources[field];
}

function updateFieldSource(
  content: BriefIntakeCandidateContent,
  field: keyof BriefIntakeCandidateContent["field_sources"],
) {
  return {
    ...content,
    field_sources: {
      ...content.field_sources,
      [field]: "user_confirmed" as const,
    },
  };
}

function CandidateField({
  label,
  source,
  hint,
  children,
}: {
  label: string;
  source: Parameters<typeof IntakeSourceBadge>[0]["source"];
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.confirmField}>
      <header>
        <div className={styles.confirmFieldHeading}>
          <b>{label}</b>
          {hint ? <small>{hint}</small> : null}
        </div>
        <IntakeSourceBadge source={source} />
      </header>
      {children}
    </section>
  );
}

export function IntakeConfirmationStep({
  intake,
  currentCandidate,
  manualSeed,
  providerReady,
  busy,
  synthesizeTask,
  error,
  onBack,
  onOpenSettings,
  onCreateManualCandidate,
  onSaveCandidate,
  onActivateCandidate,
  onDialogueRevision,
  onAdoptCandidate,
}: IntakeConfirmationStepProps) {
  const [draft, setDraft] = useState<BriefIntakeCandidateContent>(() =>
    cloneCandidateContent(currentCandidate?.content ?? manualSeed),
  );
  const [editing, setEditing] = useState(currentCandidate === null);
  const [dialogueInstruction, setDialogueInstruction] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);

  const synthesisRunning = Boolean(
    synthesizeTask && runningStatuses.has(synthesizeTask.status),
  );
  const shownContent = editing ? draft : (currentCandidate?.content ?? draft);
  const missingHardFields = useMemo(
    () => missingCandidateHardFields(shownContent),
    [shownContent],
  );
  const discardTarget = currentCandidate
    ? discardCandidateTarget(intake.candidates, currentCandidate)
    : null;

  function updateTextField(
    field: "concept" | "reasoning_goal" | "scope_estimate" | "author_answer",
    value: string,
  ) {
    setDraft((current) =>
      updateFieldSource(
        {
          ...current,
          [field]:
            field === "scope_estimate" || field === "author_answer"
              ? value || null
              : value,
        },
        field,
      ),
    );
  }

  function updateLineField(
    field: "core_selling_points" | "content_outline" | "risk_notes",
    value: string,
  ) {
    setDraft((current) =>
      updateFieldSource({ ...current, [field]: splitLines(value) }, field),
    );
  }

  function updateResolutionMode(value: ResolutionMode) {
    setDraft((current) => ({
      ...updateFieldSource(current, "resolution_mode"),
      resolution_mode: value,
      author_answer: value === "author_anchored" ? current.author_answer : null,
      field_sources: {
        ...current.field_sources,
        resolution_mode: "user_confirmed",
        author_answer:
          value === "author_anchored"
            ? current.field_sources.author_answer
            : "unresolved",
      },
    }));
  }

  function updateConstraint(
    category: Exclude<BriefIntakeConstraintCategory, "other">,
    patch: { statement?: string; strength?: "hard" | "soft" },
  ) {
    setDraft((current) => {
      const constraints = [...current.constraints];
      const index = constraints.findIndex((item) => item.category === category);
      const existing = index >= 0 ? constraints[index] : null;
      const next = {
        constraint_key: existing?.constraint_key ?? `constraint_${category}`,
        category,
        statement: patch.statement ?? existing?.statement ?? "",
        strength: patch.strength ?? existing?.strength ?? "soft",
        confirmed: true,
        source: "user_confirmed" as const,
      };
      if (!next.statement.trim()) {
        if (index >= 0) constraints.splice(index, 1);
      } else if (index >= 0) {
        constraints[index] = next;
      } else {
        constraints.push(next);
      }
      return {
        ...current,
        constraints,
        field_sources: {
          ...current.field_sources,
          constraints: constraints.length ? "user_confirmed" : "unresolved",
        },
      };
    });
  }

  function abandonChanges() {
    if (editing) {
      setDraft(cloneCandidateContent(currentCandidate?.content ?? manualSeed));
      setEditing(currentCandidate === null);
      return;
    }
    if (discardTarget) onActivateCandidate(discardTarget.candidate_id);
  }

  return (
    <section
      className={`${styles.stepSheet} ${styles.confirmationSheet}`}
      aria-labelledby="intake-confirm-title"
    >
      <header className={styles.stepSheetHeader}>
        <div>
          <span>STEP 03 / 简报校核</span>
          <h2 id="intake-confirm-title">确认整体方向，再交给正式审阅。</h2>
        </div>
        <StatusBadge tone={missingHardFields.length ? "red" : "dark"}>
          {missingHardFields.length
            ? `还缺 ${missingHardFields.length} 项`
            : "可以采用"}
        </StatusBadge>
      </header>

      <div className={styles.confirmationBody}>
        {synthesisRunning ? (
          <div className={styles.taskWaiting} aria-live="polite">
            <span className={styles.taskPulse} aria-hidden="true" />
            <div>
              <b>Agent 正在形成新候选</b>
              <p>旧候选和保存书签不会被覆盖。</p>
            </div>
          </div>
        ) : null}

        {!providerReady || synthesizeTask?.status === "failed" ? (
          <div className={styles.taskRibbon} data-status="failed">
            <b>
              {providerReady ? "Agent 任务失败" : "Agent 尚未配置"}
            </b>
            <span>人工候选仍可继续；模型服务可以随时补充或检查。</span>
            <button onClick={onOpenSettings} type="button">
              {providerReady ? "检查设置" : "打开设置"}
            </button>
          </div>
        ) : null}

        {!currentCandidate && !editing && !synthesisRunning ? (
          <div className={styles.emptyQuestions}>
            <b>还没有可审阅的候选</b>
            <p>可以返回重试 Agent，也可以直接用表单建立人工候选。</p>
            <button onClick={() => setEditing(true)} type="button">
              建立人工候选
            </button>
          </div>
        ) : null}

        {editing ? (
          <div className={styles.candidateEditor}>
            <CandidateField
              label="一句话概念 *"
              source={sourceFor(draft, "concept")}
              hint="概括核心设定与冲突"
            >
              <textarea
                onChange={(event) => updateTextField("concept", event.target.value)}
                placeholder="例如：四名玩家在不断重启的空间站中追查事故真相。"
                rows={2}
                value={draft.concept}
              />
            </CandidateField>
            <CandidateField
              label="核心卖点"
              source={sourceFor(draft, "core_selling_points")}
              hint="列出让人记住的亮点"
            >
              <textarea
                onChange={(event) =>
                  updateLineField("core_selling_points", event.target.value)
                }
                placeholder="例如：循环重启 / 第五人权限记录 / 保护协议"
                rows={3}
                value={draft.core_selling_points.join("\n")}
              />
            </CandidateField>
            <CandidateField
              label="内容骨架"
              source={sourceFor(draft, "content_outline")}
              hint="拆成可推进的阶段"
            >
              <textarea
                onChange={(event) =>
                  updateLineField("content_outline", event.target.value)
                }
                placeholder="例如：发现异常 → 追查权限记录 → 重建时间线 → 做出终止决定"
                rows={4}
                value={draft.content_outline.join("\n")}
              />
            </CandidateField>
            <CandidateField
              label="推理目标 *"
              source={sourceFor(draft, "reasoning_goal")}
              hint="定义要回答的关键问题"
            >
              <textarea
                onChange={(event) =>
                  updateTextField("reasoning_goal", event.target.value)
                }
                placeholder="例如：在第七次循环结束前找出谁触发了重启。"
                rows={3}
                value={draft.reasoning_goal}
              />
            </CandidateField>
            <CandidateField
              label="结论处理方式"
              source={sourceFor(draft, "resolution_mode")}
              hint="决定谁来锁定结论"
            >
              <div className={styles.modeChoices}>
                {(Object.keys(resolutionModeLabels) as ResolutionMode[]).map(
                  (mode) => (
                    <label key={mode}>
                      <input
                        checked={draft.resolution_mode === mode}
                        name="intake-resolution-mode"
                        onChange={() => updateResolutionMode(mode)}
                        type="radio"
                      />
                      <span>
                        <b>{resolutionModeLabels[mode]}</b>
                        <small>{resolutionModeHints[mode]}</small>
                      </span>
                    </label>
                  ),
                )}
              </div>
            </CandidateField>
            {draft.resolution_mode === "author_anchored" ? (
              <CandidateField
                label="作者底牌 *"
                source={sourceFor(draft, "author_answer")}
                hint="只有已知答案时填写"
              >
                <textarea
                  onChange={(event) =>
                    updateTextField("author_answer", event.target.value)
                  }
                  placeholder="例如：真正触发重启的是维护机器人，而不是玩家。"
                  rows={3}
                  value={draft.author_answer ?? ""}
                />
              </CandidateField>
            ) : null}
            <CandidateField
              label="预计规模"
              source={sourceFor(draft, "scope_estimate")}
              hint="估算交付体量"
            >
              <textarea
                onChange={(event) =>
                  updateTextField("scope_estimate", event.target.value)
                }
                placeholder="例如：4 名玩家 / 6 个场景 / 60–90 分钟"
                rows={2}
                value={draft.scope_estimate ?? ""}
              />
            </CandidateField>
            <CandidateField
              label="风险提示"
              source={sourceFor(draft, "risk_notes")}
              hint="提前标出体验风险"
            >
              <textarea
                onChange={(event) =>
                  updateLineField("risk_notes", event.target.value)
                }
                placeholder="例如：线索过多，玩家无法复盘。"
                rows={3}
                value={draft.risk_notes.join("\n")}
              />
            </CandidateField>

            <details className={styles.constraintsDrawer}>
              <summary>
                <span>
                  <b>约束抽屉</b>
                  <small>
                    <span className={styles.drawerInstruction}>点击展开</span>
                    必须保留、禁止出现、规模、人数、时长与内容尺度
                  </small>
                </span>
                <em>{draft.constraints.length} 项</em>
              </summary>
              <div>
                {constraintCategories.map((category) => {
                  const constraint = draft.constraints.find(
                    (item) => item.category === category.value,
                  );
                  return (
                    <label key={category.value}>
                      <span>
                        <b>{category.label}</b>
                        <small>{category.hint}</small>
                      </span>
                      <textarea
                        onChange={(event) =>
                          updateConstraint(category.value, {
                            statement: event.target.value,
                          })
                        }
                        placeholder={category.example}
                        rows={2}
                        value={constraint?.statement ?? ""}
                      />
                      <select
                        aria-label={`${category.label}约束强度`}
                        onChange={(event) =>
                          updateConstraint(category.value, {
                            strength: event.target.value as "hard" | "soft",
                          })
                        }
                        value={constraint?.strength ?? "soft"}
                      >
                        <option value="hard">硬约束</option>
                        <option value="soft">软偏好</option>
                      </select>
                    </label>
                  );
                })}
              </div>
            </details>

            <div className={styles.editorActions}>
              <button onClick={abandonChanges} type="button">
                放弃表单修改
              </button>
              <button
                disabled={busy || missingCandidateHardFields(draft).length > 0}
                onClick={() =>
                  onCreateManualCandidate(
                    draft,
                    currentCandidate?.candidate_id ?? null,
                  )
                }
                type="button"
              >
                {busy ? "正在保存…" : "保存为新候选"}
              </button>
            </div>
          </div>
        ) : currentCandidate ? (
          <div className={styles.candidateReview}>
            <div className={styles.candidateLead}>
              <span>{candidateOriginLabels[currentCandidate.origin]}</span>
              <h3>{currentCandidate.content.concept}</h3>
              <IntakeSourceBadge
                source={currentCandidate.content.field_sources.concept}
              />
            </div>

            <div className={styles.candidateGrid}>
              <CandidateField
                label="核心卖点"
                source={sourceFor(currentCandidate.content, "core_selling_points")}
              >
                {currentCandidate.content.core_selling_points.length ? (
                  <ul>
                    {currentCandidate.content.core_selling_points.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.emptyValue}>尚未补充</p>
                )}
              </CandidateField>
              <CandidateField
                label="内容骨架"
                source={sourceFor(currentCandidate.content, "content_outline")}
              >
                {currentCandidate.content.content_outline.length ? (
                  <ol>
                    {currentCandidate.content.content_outline.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ol>
                ) : (
                  <p className={styles.emptyValue}>尚未补充</p>
                )}
              </CandidateField>
              <CandidateField
                label="推理目标与结论模式"
                source={sourceFor(currentCandidate.content, "reasoning_goal")}
              >
                <p>{currentCandidate.content.reasoning_goal}</p>
                <small>
                  {resolutionModeLabels[currentCandidate.content.resolution_mode]}
                  {currentCandidate.content.author_answer
                    ? ` · ${currentCandidate.content.author_answer}`
                    : ""}
                </small>
              </CandidateField>
              <CandidateField
                label="预计规模与风险"
                source={sourceFor(currentCandidate.content, "scope_estimate")}
              >
                <p>{currentCandidate.content.scope_estimate ?? "尚未估算"}</p>
                {currentCandidate.content.risk_notes.length ? (
                  <ul>
                    {currentCandidate.content.risk_notes.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </CandidateField>
            </div>

            <details className={styles.constraintsDrawer}>
              <summary>
                <span>
                  <b>主要约束</b>
                  <small>采用后整理进正式简报的创作边界</small>
                </span>
                <em>{currentCandidate.content.constraints.length} 项</em>
              </summary>
              <div className={styles.constraintReviewList}>
                {currentCandidate.content.constraints.length ? (
                  currentCandidate.content.constraints.map((constraint) => (
                    <article key={constraint.constraint_key}>
                      <span>
                        {constraintCategories.find(
                          (item) => item.value === constraint.category,
                        )?.label ?? "其他约束"}
                      </span>
                      <p>{constraint.statement}</p>
                      <b>{constraint.strength === "hard" ? "硬约束" : "软偏好"}</b>
                    </article>
                  ))
                ) : (
                  <p className={styles.emptyValue}>当前没有已确认约束。</p>
                )}
              </div>
            </details>

            <section className={styles.pendingQueue}>
              <header>
                <b>待决定事项</b>
                <IntakeSourceBadge source="unresolved" />
              </header>
              {currentCandidate.content.pending_decisions.length ? (
                currentCandidate.content.pending_decisions.map((decision) => (
                  <article key={decision.decision_key}>
                    <b>{decision.prompt}</b>
                    <p>{decision.impact}</p>
                  </article>
                ))
              ) : (
                <p className={styles.emptyValue}>没有阻碍继续的待决定事项。</p>
              )}
            </section>

            <div className={styles.revisionTools}>
              <button onClick={() => setEditing(true)} type="button">
                表单修改
              </button>
              <button
                disabled={busy || currentCandidate.is_saved}
                onClick={() => onSaveCandidate(currentCandidate.candidate_id)}
                type="button"
              >
                {currentCandidate.is_saved ? "已保存候选" : "保存为候选"}
              </button>
              <button
                disabled={busy || discardTarget === null}
                onClick={abandonChanges}
                type="button"
              >
                放弃修改
              </button>
            </div>

            <form
              className={styles.dialogueRevision}
              onSubmit={(event) => {
                event.preventDefault();
                if (dialogueInstruction.trim()) {
                  onDialogueRevision(
                    currentCandidate.candidate_id,
                    dialogueInstruction.trim(),
                  );
                }
              }}
            >
              <label htmlFor="intake-dialogue-revision">
                <b>对话修改</b>
                <small>只提交这一轮指令，并从当前候选生成子版本。</small>
              </label>
              <textarea
                id="intake-dialogue-revision"
                onChange={(event) => setDialogueInstruction(event.target.value)}
                placeholder="例如：把内容骨架压缩成三个阶段，其他已确认内容不变。"
                rows={2}
                value={dialogueInstruction}
              />
              <button
                disabled={
                  busy ||
                  synthesisRunning ||
                  !providerReady ||
                  !dialogueInstruction.trim()
                }
                title={providerReady ? undefined : "请先配置模型服务"}
                type="submit"
              >
                {synthesisRunning ? "生成中…" : "生成修改候选"}
              </button>
            </form>
          </div>
        ) : null}

        <section className={styles.candidateHistory} data-open={historyOpen}>
          <button onClick={() => setHistoryOpen((open) => !open)} type="button">
            <span>
              候选历史 <b>{intake.candidates.length}</b>
            </span>
            <em>{historyOpen ? "收起" : "展开"}</em>
          </button>
          {historyOpen ? (
            <div>
              {intake.candidates.map((candidate, index) => (
                <article
                  data-current={candidate.is_current}
                  data-stale={candidate.is_stale}
                  key={candidate.candidate_id}
                >
                  <span>版本 {String(intake.candidates.length - index).padStart(2, "0")}</span>
                  <div>
                    <b>{candidateOriginLabels[candidate.origin]}</b>
                    <p>{candidate.content.concept}</p>
                  </div>
                  <small>
                    {candidate.is_current
                      ? "当前"
                      : candidate.is_stale
                        ? "输入已变化"
                        : candidate.is_saved
                          ? "已保存"
                          : "历史"}
                  </small>
                  {!candidate.is_current && candidate.can_activate ? (
                    <button
                      disabled={busy}
                      onClick={() => onActivateCandidate(candidate.candidate_id)}
                      type="button"
                    >
                      恢复此版
                    </button>
                  ) : null}
                </article>
              ))}
            </div>
          ) : null}
        </section>

        {error ? (
          <p className={styles.inlineError} role="alert">
            {error}
          </p>
        ) : null}
      </div>

      <footer className={styles.confirmationActions}>
        <button className={styles.secondaryAction} onClick={onBack} type="button">
          ← 返回追问
        </button>
        <div>
          {missingHardFields.length ? (
            <span>采用前请补齐：{missingHardFields.join("、")}</span>
          ) : (
            <span>待决定事项不会阻止进入正式审阅。</span>
          )}
          <button
            className={styles.primaryAction}
            disabled={
              busy ||
              editing ||
              !currentCandidate ||
              currentCandidate.is_stale ||
              missingHardFields.length > 0
            }
            onClick={() =>
              currentCandidate && onAdoptCandidate(currentCandidate.candidate_id)
            }
            type="button"
          >
            <span>{busy ? "正在采用…" : "采用当前简报"}</span>
            <b aria-hidden="true">→</b>
          </button>
        </div>
      </footer>
    </section>
  );
}
