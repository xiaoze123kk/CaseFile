"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { CaseSpine, DocumentHeader, PanelHeader, StatusBadge } from "@/components/archive-ui";
import { CompilerPanel } from "@/features/compiler/compiler-panel";
import {
  canCompilePrototype,
  hasBlockingIssue,
  type PrototypeIssue,
} from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import styles from "./quality-workspace.module.css";

type IssueFilter = "all" | "attention" | "S1" | "resolved";

const issueStatusLabel: Record<PrototypeIssue["status"], string> = {
  open: "待处理",
  "pending-revalidation": "待复验",
  resolved: "已通过",
};

function matchesFilter(issue: PrototypeIssue, filter: IssueFilter) {
  if (filter === "all") return true;
  if (filter === "attention") return issue.status !== "resolved";
  if (filter === "S1") return issue.severity === "S1";
  return issue.status === "resolved";
}

function getValidationLabel(
  status: "fresh" | "stale" | "running",
  blocked: boolean,
) {
  if (status === "running") return "验证运行中";
  if (status === "stale") return "报告已过期";
  return blocked ? "存在阻断项" : "可进入编译";
}

function IssueList({
  issues,
  selectedId,
  onSelect,
}: {
  issues: PrototypeIssue[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  if (issues.length === 0) {
    return (
      <div className={styles.emptyIssues}>
        <b>NO MATCHED RECORD</b>
        <span>当前筛选条件下没有质量问题。</span>
      </div>
    );
  }

  return (
    <div className={styles.issueList}>
      <div className={styles.issueColumns} aria-hidden="true">
        <span>等级</span>
        <span>问题记录</span>
        <span>对象</span>
        <span>状态</span>
      </div>
      {issues.map((issue) => (
        <button
          aria-pressed={issue.id === selectedId}
          className={`${styles.issueRow} ${
            issue.id === selectedId ? styles.issueRowSelected : ""
          }`}
          key={issue.id}
          onClick={() => onSelect(issue.id)}
          type="button"
        >
          <span className={`${styles.severity} ${styles[`severity${issue.severity}`]}`}>
            {issue.severity}
          </span>
          <span className={styles.issueTitle}>
            <strong>{issue.title}</strong>
            <small>
              {issue.ruleId} · {issue.detectionType === "deterministic" ? "确定性规则" : "关系图检查"}
            </small>
          </span>
          <span className={styles.objectId}>{issue.objectId}</span>
          <span
            className={`${styles.issueState} ${
              issue.status === "resolved" ? styles.issueStateResolved : ""
            }`}
          >
            {issueStatusLabel[issue.status]}
          </span>
        </button>
      ))}
    </div>
  );
}

function EvidenceTrace({
  issue,
  currentVisibility,
}: {
  issue: PrototypeIssue;
  currentVisibility: string;
}) {
  const isKnowledgeIssue = issue.id === "VAL-KNOW-001";

  return (
    <section className={styles.evidenceTrace} aria-label="质量问题证据链">
      <div className={styles.traceHeader}>
        <div>
          <span>{issue.severity} / EVIDENCE TRACE</span>
          <h2>{issue.title}</h2>
        </div>
        <StatusBadge tone={issue.status === "resolved" ? "dark" : "red"}>
          {issueStatusLabel[issue.status]}
        </StatusBadge>
      </div>

      <div className={styles.ruleStrip}>
        <span>规则</span>
        <code>{issue.ruleId}</code>
        <span>检测方式</span>
        <b>{issue.detectionType === "deterministic" ? "确定性 / 可复现" : "关系图 / 可追踪"}</b>
      </div>

      <p className={styles.issueExplanation}>{issue.explanation}</p>

      <div className={styles.traceRoute}>
        <article>
          <span>01 / 触发对象</span>
          <strong>{issue.objectId}</strong>
          <small>{isKnowledgeIssue ? "18:23 · AI 启动保护协议" : "当前草稿对象"}</small>
        </article>
        <i aria-hidden="true">→</i>
        <article>
          <span>02 / 证据记录</span>
          <strong>{issue.evidenceId}</strong>
          <small>{isKnowledgeIssue ? "第五人权限记录" : "关联证据节点"}</small>
        </article>
        <i aria-hidden="true">→</i>
        <article className={styles.traceConflict}>
          <span>03 / 冲突结论</span>
          <strong>{isKnowledgeIssue ? "提前可见" : "关系未闭合"}</strong>
          <small>{isKnowledgeIssue ? "林望尚未取得该知识" : "对象缺少必要锚点"}</small>
        </article>
      </div>

      <div className={styles.traceFacts}>
        <div>
          <span>事件可见范围</span>
          <b>{isKnowledgeIssue ? currentVisibility : issue.objectId}</b>
        </div>
        <div>
          <span>角色最早获得时间</span>
          <b>{isKnowledgeIssue ? "18:25 / EVL-1825" : "未登记"}</b>
        </div>
        <div>
          <span>验证快照</span>
          <b>不可变证据 / FIXED INPUT</b>
        </div>
      </div>
    </section>
  );
}

export function QualityWorkspace() {
  const { state, dispatch, ready } = usePrototype();
  const [filter, setFilter] = useState<IssueFilter>("all");
  const [selectedIssueId, setSelectedIssueId] = useState("VAL-KNOW-001");
  const [notice, setNotice] = useState<string | null>(null);
  const validationTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (validationTimer.current !== null) {
        window.clearTimeout(validationTimer.current);
      }
    },
    [],
  );

  const visibleIssues = useMemo(
    () => state.validation.issues.filter((issue) => matchesFilter(issue, filter)),
    [filter, state.validation.issues],
  );
  const selectedIssue =
    visibleIssues.find((issue) => issue.id === selectedIssueId) ?? visibleIssues[0];
  const blocking = hasBlockingIssue(state);
  const canCompile = canCompilePrototype(state);
  const attentionCount = state.validation.issues.filter(
    (issue) => issue.status !== "resolved",
  ).length;
  const resolvedCount = state.validation.issues.length - attentionCount;
  const readiness =
    state.validation.status === "stale"
      ? 82
      : canCompile
        ? 96
        : blocking
          ? 68
          : 88;

  function applyAgentPatch() {
    dispatch({ type: "apply-patch" });
    setNotice("补丁已由你批准并写入草稿；原验证报告随即失效，请显式重新验证。");
  }

  function runValidation() {
    if (state.validation.status === "running") return;
    dispatch({ type: "start-validation" });
    setNotice("验证器已锁定当前草稿修订，正在重建确定性检查报告…");
    if (validationTimer.current !== null) {
      window.clearTimeout(validationTimer.current);
    }
    validationTimer.current = window.setTimeout(() => {
      dispatch({ type: "complete-validation" });
      setNotice("重新验证完成：报告已绑定当前草稿修订。");
      validationTimer.current = null;
    }, 1350);
  }

  if (!ready) {
    return (
      <main className={`document ${styles.qualityDocument}`}>
        <div className={styles.loadingRecord}>
          <span>CASEFILE / LOCAL INDEX</span>
          <b>正在调取质量档案…</b>
        </div>
      </main>
    );
  }

  return (
    <main className={`document ${styles.qualityDocument}`}>
      <DocumentHeader
        action={
          <Link className="square-button" href="/workbench">
            ← 返回工作台
          </Link>
        }
        eyebrow="发布审查记录 / QUALITY DOSSIER"
        meta={[
          { label: "PROJECT", value: state.project.projectId },
          { label: "DRAFT", value: `REV.${state.draft.revision}` },
          { label: "REPORT", value: state.validation.runId },
          {
            label: "STATE",
            value: getValidationLabel(state.validation.status, blocking),
            tone: blocking || state.validation.status !== "fresh" ? "critical" : "default",
          },
        ]}
        title="质量中心 / 发布门禁"
      />

      <CaseSpine current="validated" stale={state.validation.status !== "fresh"} />

      <div className={styles.qualityBody}>
        <section className={styles.gateBoard} aria-label="发布门禁与指标">
          <div className={styles.readinessCard}>
            <span>发布准备度 / READINESS</span>
            <strong>{readiness}%</strong>
            <div aria-label={`发布准备度 ${readiness}%`} className={styles.progressTrack}>
              <i style={{ width: `${readiness}%` }} />
            </div>
            <small>
              {canCompile
                ? "所有硬门禁已通过，可固定快照并编译。"
                : state.validation.status === "stale"
                  ? "草稿已变更，旧报告不能作为发布依据。"
                  : "存在 S1 阻断项，编译器保持封存。"}
            </small>
          </div>

          <div className={styles.gateSequence}>
            <article className={styles.gatePassed}>
              <b>01</b>
              <span>
                <small>结构与引用</small>
                <strong>通过</strong>
              </span>
              <i>✓</i>
            </article>
            <article className={blocking ? styles.gateBlocked : styles.gatePassed}>
              <b>02</b>
              <span>
                <small>知识隔离</small>
                <strong>{blocking ? "阻断" : "通过"}</strong>
              </span>
              <i>{blocking ? "!" : "✓"}</i>
            </article>
            <article
              className={
                state.validation.status === "fresh"
                  ? styles.gatePassed
                  : styles.gateWaiting
              }
            >
              <b>03</b>
              <span>
                <small>报告时效</small>
                <strong>
                  {state.validation.status === "running"
                    ? "扫描中"
                    : state.validation.status === "stale"
                      ? "待复验"
                      : "有效"}
                </strong>
              </span>
              <i>{state.validation.status === "fresh" ? "✓" : "…"}</i>
            </article>
          </div>

          <dl className={styles.metricStrip}>
            <div>
              <dt>结构完整性</dt>
              <dd>100%</dd>
            </div>
            <div>
              <dt>引用闭合率</dt>
              <dd>91%</dd>
            </div>
            <div>
              <dt>知识隔离</dt>
              <dd>{blocking ? "74%" : "96%"}</dd>
            </div>
            <div>
              <dt>待处理</dt>
              <dd className={attentionCount ? styles.metricCritical : ""}>
                {attentionCount}
              </dd>
            </div>
            <div>
              <dt>已通过</dt>
              <dd>{resolvedCount}</dd>
            </div>
          </dl>
        </section>

        <div className={styles.reviewGrid}>
          <section className={`paper-panel ${styles.issuesPanel}`}>
            <PanelHeader
              code={`${state.validation.runId} · SNAPSHOT REV.${state.validation.snapshotRevision}`}
              title="问题清单 / ISSUE REGISTER"
              trailing={
                <button
                  className={`square-button ${
                    state.validation.status === "running" ? styles.runningButton : ""
                  }`}
                  disabled={state.validation.status === "running"}
                  onClick={runValidation}
                  type="button"
                >
                  {state.validation.status === "running" ? "验证运行中…" : "↻ 重新验证"}
                </button>
              }
            />

            <div className={styles.filterBar} role="group" aria-label="问题筛选">
              {(
                [
                  ["all", "全部", state.validation.issues.length],
                  ["attention", "待关注", attentionCount],
                  [
                    "S1",
                    "S1 阻断",
                    state.validation.issues.filter((issue) => issue.severity === "S1").length,
                  ],
                  ["resolved", "已通过", resolvedCount],
                ] as const
              ).map(([id, label, count]) => (
                <button
                  aria-pressed={filter === id}
                  className={filter === id ? styles.filterActive : ""}
                  key={id}
                  onClick={() => setFilter(id)}
                  type="button"
                >
                  {label} <b>{String(count).padStart(2, "0")}</b>
                </button>
              ))}
              <span className={styles.reportAge}>
                {state.validation.status === "stale"
                  ? `旧快照 REV.${state.validation.snapshotRevision}`
                  : `更新于 ${state.validation.lastRunAt}`}
              </span>
            </div>

            <div className={styles.issueReview}>
              <IssueList
                issues={visibleIssues}
                onSelect={setSelectedIssueId}
                selectedId={selectedIssue?.id}
              />

              {selectedIssue ? (
                <div className={styles.issueDetail}>
                  <EvidenceTrace
                    currentVisibility={
                      state.draft.events.find(
                        (event) => event.id === selectedIssue.objectId,
                      )?.visibility ?? "未登记"
                    }
                    issue={selectedIssue}
                  />

                  <section className={styles.patchReview} aria-label="Agent 补丁审阅">
                    <div className={styles.patchHeading}>
                      <div>
                        <span>AGENT PATCH / 仅为建议</span>
                        <strong>
                          {selectedIssue.id === "VAL-KNOW-001"
                            ? "缩小事件可见范围"
                            : "此问题需要人工建立关系"}
                        </strong>
                      </div>
                      <StatusBadge tone="warning">人工批准</StatusBadge>
                    </div>

                    {selectedIssue.id === "VAL-KNOW-001" ? (
                      <>
                        <div className={styles.diffBlock}>
                          <code>
                            <i>−</i> visibility: &quot;AI 核心 + 全部角色&quot;
                          </code>
                          <code>
                            <b>+</b> visibility: &quot;AI 核心 + 秦彻&quot;
                          </code>
                        </div>
                        <p>
                          Agent 不会直接改写卷宗。批准后只写入草稿，确定性验证器仍需重新运行。
                        </p>
                        <button
                          className="square-button square-button--red"
                          disabled={
                            state.validation.patchDecision === "approved" ||
                            selectedIssue.status === "resolved"
                          }
                          onClick={applyAgentPatch}
                          type="button"
                        >
                          {state.validation.patchDecision === "approved"
                            ? "补丁已应用 · 等待复验"
                            : selectedIssue.status === "resolved"
                              ? "问题已通过"
                              : "审阅并应用补丁"}
                        </button>
                      </>
                    ) : (
                      <div className={styles.manualFix}>
                        <p>{selectedIssue.fixHint}</p>
                        <Link className="square-button" href="/workbench">
                          在工作台定位 {selectedIssue.objectId} →
                        </Link>
                      </div>
                    )}
                  </section>
                </div>
              ) : null}
            </div>
          </section>

          <CompilerPanel />
        </div>
      </div>

      {notice ? (
        <div className={styles.localNotice} role="status">
          <b>QUALITY / EVENT</b>
          <span>{notice}</span>
          <button aria-label="关闭通知" onClick={() => setNotice(null)} type="button">
            ×
          </button>
        </div>
      ) : null}
    </main>
  );
}
