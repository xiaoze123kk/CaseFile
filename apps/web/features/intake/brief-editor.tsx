"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  CaseSpine,
  DocumentHeader,
  PanelHeader,
  StatusBadge,
} from "@/components/prototype-ui";
import type { BriefTextField } from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import styles from "./intake.module.css";

const briefFields: Array<{
  field: BriefTextField;
  number: string;
  label: string;
  code: string;
  source: string;
  rows: number;
}> = [
  {
    field: "oneLineConcept",
    number: "01",
    label: "一句话概念",
    code: "LOGLINE",
    source: "Agent 提炼 · 人工可改",
    rows: 3,
  },
  {
    field: "coreMystery",
    number: "02",
    label: "核心谜题",
    code: "CORE MYSTERY",
    source: "人工定义",
    rows: 3,
  },
  {
    field: "playerGoal",
    number: "03",
    label: "玩家目标",
    code: "PLAYER GOAL",
    source: "Brief 候选",
    rows: 3,
  },
  {
    field: "gameplayLoop",
    number: "04",
    label: "体验循环",
    code: "GAMEPLAY LOOP",
    source: "模板建议 · 人工可改",
    rows: 3,
  },
];

const decisionNotes: Record<string, string> = {
  "D-01": "角色数量将进入后续对象结构与玩家材料。",
  "D-02": "质量门禁会要求唯一、可验证的事实根因。",
  "D-03": "两个结局共享同一事实层，只改变最终选择。",
};

export function BriefEditor() {
  const router = useRouter();
  const { state, dispatch, ready } = usePrototype();
  const decisionsComplete = state.brief.decisions.every(
    (decision) => decision.checked,
  );

  function approveBrief() {
    if (!decisionsComplete) return;
    dispatch({ type: "approve-brief" });
    router.push("/workbench");
  }

  return (
    <main
      aria-busy={!ready}
      className={`document ${styles.briefDocument}`}
    >
      <DocumentHeader
        action={
          <Link
            aria-label="返回建案中心修改原始创意"
            className="square-button"
            href="/"
          >
            ← 返回原始创意
          </Link>
        }
        eyebrow={
          state.brief.approved
            ? "已确认 / BRIEF APPROVED"
            : "待确认 / BRIEF CANDIDATE"
        }
        meta={[
          { label: "候选版本", value: "V0.1" },
          { label: "字段来源", value: "04 TRACKED" },
          { label: "生成方式", value: "人机协作" },
        ]}
        title={`${state.project.projectId} : BRIEF_01`}
      />

      <CaseSpine current="brief" />

      <div className={styles.briefSpread}>
        <section
          aria-label="可编辑的项目 Brief"
          className={`paper-panel ${styles.briefSheet}`}
        >
          <span className={styles.briefWatermark} aria-hidden="true">
            BRIEF
          </span>
          <header className={styles.briefSheetHead}>
            <div>
              <span>项目简报 / FORMAL OBJECT</span>
              <strong>{state.project.displayName}</strong>
            </div>
            <div
              className={`${styles.candidateStamp} ${
                state.brief.approved ? styles.candidateStampApproved : ""
              }`}
            >
              <span>{state.brief.approved ? "已批准" : "待采纳"}</span>
              <b>CANDIDATE 01</b>
            </div>
          </header>

          <div className={styles.fieldGrid}>
            {briefFields.map((config) => (
              <label
                className={styles.briefField}
                htmlFor={`brief-${config.field}`}
                key={config.field}
              >
                <span className={styles.briefFieldHead}>
                  <i>{config.number}</i>
                  <span>
                    <strong>{config.label}</strong>
                    <small>{config.code}</small>
                  </span>
                  <em>来源：{config.source}</em>
                </span>
                <textarea
                  aria-label={`编辑${config.label}，来源为${config.source}`}
                  id={`brief-${config.field}`}
                  onChange={(event) =>
                    dispatch({
                      type: "update-brief",
                      field: config.field,
                      value: event.target.value,
                    })
                  }
                  rows={config.rows}
                  value={state.brief[config.field]}
                />
                <span className={styles.fieldFoot}>
                  <b>可编辑字段</b>
                  <small>{state.brief[config.field].length} 字符</small>
                </span>
              </label>
            ))}
          </div>

          <section className={styles.sourceLedger}>
            <PanelHeader
              code="PROVENANCE / 04"
              title="来源账本"
              trailing={<StatusBadge tone="red">全量追踪</StatusBadge>}
            />
            <div>
              <span>
                <b>作者原稿</b>
                <small>原始创意被保留，不随本页修改</small>
              </span>
              <span>
                <b>Agent 候选</b>
                <small>
                  {state.idea.suggestionStatus === "adopted"
                    ? "已采纳到工作稿"
                    : "独立候选，尚未覆盖任何原文"}
                </small>
              </span>
              <span>
                <b>人工编辑</b>
                <small>本页四个字段的最新输入</small>
              </span>
            </div>
          </section>
        </section>

        <aside
          aria-label="Brief 人工确认"
          className={`paper-panel ${styles.decisionSheet}`}
        >
          <header className={styles.decisionHead}>
            <div>
              <span>HUMAN GATE / REQUIRED</span>
              <h2>批准前的人工决策</h2>
            </div>
            <strong>
              {
                state.brief.decisions.filter((decision) => decision.checked)
                  .length
              }
              /{state.brief.decisions.length}
            </strong>
          </header>

          <section className={styles.decisionSection}>
            <PanelHeader code="DECISIONS" title="待确认事项" />
            <div className={styles.decisionList}>
              {state.brief.decisions.map((decision) => (
                <label
                  className={decision.checked ? styles.decisionChecked : ""}
                  key={decision.id}
                >
                  <input
                    aria-label={`确认决策：${decision.label}`}
                    checked={decision.checked}
                    onChange={() =>
                      dispatch({
                        type: "toggle-decision",
                        id: decision.id,
                      })
                    }
                    type="checkbox"
                  />
                  <span>
                    <strong>{decision.label}</strong>
                    <small>{decisionNotes[decision.id]}</small>
                  </span>
                  <b>{decision.id}</b>
                </label>
              ))}
            </div>
          </section>

          <section className={styles.constraintsSection}>
            <PanelHeader code="FIXED / 03" title="结构约束" />
            <ol>
              {state.brief.constraints.map((constraint, index) => (
                <li key={constraint}>
                  <span>C-{String(index + 1).padStart(2, "0")}</span>
                  <strong>{constraint}</strong>
                  <i>保留</i>
                </li>
              ))}
            </ol>
          </section>

          <section className={styles.questionsSection}>
            <PanelHeader code="OPEN / 03" title="带入工作台的问题" />
            <ol>
              {state.brief.openQuestions.map((question, index) => (
                <li key={question}>
                  <b>Q{index + 1}</b>
                  <span>{question}</span>
                </li>
              ))}
            </ol>
          </section>

          <section className={styles.provenanceSummary}>
            <div>
              <span>作者输入</span>
              <strong>01 原始记录</strong>
            </div>
            <div>
              <span>Agent 提议</span>
              <strong>01 独立候选</strong>
            </div>
            <div>
              <span>人工决策</span>
              <strong>
                {decisionsComplete ? "已齐备" : "仍需确认"}
              </strong>
            </div>
          </section>
        </aside>
      </div>

      <footer className={styles.briefActions}>
        <div>
          <span>
            {decisionsComplete
              ? "人工门禁已满足"
              : "请完成全部人工决策，再批准 Brief"}
          </span>
          <b>
            {decisionsComplete
              ? "批准后建立 Draft，并进入事件工作台"
              : "Agent 无权代替作者确认产品约束"}
          </b>
        </div>
        <Link aria-label="返回建案中心" href="/">
          返回修改
        </Link>
        <button
          aria-label="批准 Brief 并进入 CaseFile 工作台"
          disabled={!decisionsComplete}
          onClick={approveBrief}
          type="button"
        >
          <span>
            {state.brief.approved ? "打开工作台" : "批准并建立 Draft"}
          </span>
          <b>→</b>
        </button>
      </footer>
    </main>
  );
}
