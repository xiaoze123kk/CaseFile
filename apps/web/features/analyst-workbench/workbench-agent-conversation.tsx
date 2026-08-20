"use client";

import { useState, type ReactNode } from "react";

import type {
  AgentAuditFindingView,
  AgentMessageView,
  AgentPatchSetView,
  AgentSuggestedView,
  TaskView,
} from "@/lib/api-client";

import styles from "./workbench-agent.module.css";

export const agentViewLabels: Record<AgentSuggestedView, string> = {
  timeline: "时间线",
  relations: "关系图",
  reasoning: "推理分析",
  map: "地图",
  export: "导出预览",
  compile: "编译中心",
  evidence: "证据对比",
};

const auditFindingKindLabels = {
  dangling_ref: "断链",
  contradiction: "矛盾",
  temporal: "时序错误",
  motivation_gap: "动机缺口",
  scope_gap: "范围缺口",
} as const;

const auditFindingSeverityLabels = {
  S1: "致命",
  S2: "主要",
  S3: "次要",
} as const;

export function agentAuditFindingsFor(
  message: AgentMessageView,
): AgentAuditFindingView[] {
  const result = message.task?.result;
  if (result === null || result === undefined) return [];
  if (typeof result !== "object" || !("audit_findings" in result)) return [];
  const findings = (result as { audit_findings?: unknown }).audit_findings;
  if (!Array.isArray(findings)) return [];
  return findings.filter(
    (finding): finding is AgentAuditFindingView =>
      typeof finding === "object" &&
      finding !== null &&
      typeof (finding as AgentAuditFindingView).finding_id === "string" &&
      typeof (finding as AgentAuditFindingView).kind === "string" &&
      typeof (finding as AgentAuditFindingView).severity === "string" &&
      typeof (finding as AgentAuditFindingView).title === "string",
  );
}

function formatRecordTime(value: string | null): string {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "记录时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function taskStageLabel(task: TaskView | null): string {
  if (task === null) return "任务已排队";
  if (task.status === "queued") return "任务已排队";
  if (task.status === "running") return task.stage || "正在分析卷宗";
  if (task.status === "cancelling") return "正在取消";
  return "正在整理回复";
}

export function WorkbenchAgentConversation({
  surface,
  threadsLoading,
  threadsError,
  messagesLoading,
  messagesError,
  messages,
  selectedThreadTitle,
  liveTasks,
  referenceLabels,
  busy,
  onReconnect,
  onReloadMessages,
  onRetryMessage,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
  onLocateView,
  renderRoutingFeedback,
  renderPatchReview,
}: {
  surface: "quick" | "desk";
  threadsLoading: boolean;
  threadsError: string | null;
  messagesLoading: boolean;
  messagesError: string | null;
  messages: AgentMessageView[];
  selectedThreadTitle: string | null;
  liveTasks: Record<number, TaskView>;
  referenceLabels: {
    objects: Record<string, string>;
    events: Record<string, string>;
    issues: Record<string, string>;
  };
  busy: boolean;
  onReconnect: () => void;
  onReloadMessages: () => void;
  onRetryMessage: (message: AgentMessageView) => void;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onLocateIssue: (issueId: string) => void;
  onLocateView: (view: AgentSuggestedView) => void;
  renderRoutingFeedback: (message: AgentMessageView) => ReactNode;
  renderPatchReview: (message: AgentMessageView, patchSet: AgentPatchSetView) => ReactNode;
}) {
  return (
    <div aria-live="polite" className={styles.agentMessages}>
      {threadsLoading ? (
        <p className={styles.agentThinking}>正在读取 Agent 对话…</p>
      ) : null}
      {!threadsLoading && threadsError ? (
        <div className={styles.agentFailure} role="status">
          <strong>无法连接 Agent</strong>
          <span>{threadsError}</span>
          <button onClick={onReconnect} type="button">
            重新连接
          </button>
        </div>
      ) : null}
      {messages.map((message) => {
        if (message.role === "system") return null;
        const liveTask =
          message.task === null
            ? null
            : (liveTasks[message.task.task_run_id] ?? message.task);
        return (
          <article
            className={styles.agentTurn}
            data-role={message.role}
            key={message.message_id}
          >
            <header className={styles.agentTurnMeta}>
              <span>{message.role === "assistant" ? "卷宗统筹" : "你"}</span>
              <time dateTime={message.created_at ?? undefined}>
                {formatRecordTime(message.created_at)}
              </time>
            </header>
            {message.content !== null ? (
              <div className={styles.agentTurnContent}>
                {message.role === "assistant" ? <strong>结论</strong> : null}
                <p>{message.content}</p>
              </div>
            ) : null}
            {message.role === "assistant" && message.status === "completed" ? (
              <AssistantResultSummary
                findings={agentAuditFindingsFor(message)}
                message={message}
                onLocateEvent={onLocateEvent}
                onLocateIssue={onLocateIssue}
                onLocateObject={onLocateObject}
                onLocateView={onLocateView}
                referenceLabels={referenceLabels}
                renderPatchReview={renderPatchReview}
              />
            ) : null}
            {message.role === "assistant" && message.status === "completed"
              ? renderRoutingFeedback(message)
              : null}
            {surface === "quick" &&
            message.role === "assistant" &&
            message.status === "pending" ? (
              <p className={styles.agentThinking} role="status">
                {busy
                  ? `Agent 正在回复 · ${taskStageLabel(liveTask)}`
                  : "Agent 正在整理回复…"}
              </p>
            ) : null}
            {message.role === "assistant" && message.status === "failed" ? (
              <div className={styles.agentFailure} role="status">
                <strong>回复失败</strong>
                <span>
                  {message.task?.failure?.message ?? "Agent 未能完成这次回复。"}
                </span>
                <button onClick={() => onRetryMessage(message)} type="button">
                  重试
                </button>
              </div>
            ) : null}
          </article>
        );
      })}
      {!threadsLoading && !threadsError && !messagesLoading && messagesError ? (
        <div className={styles.agentFailure} role="status">
          <strong>读取失败</strong>
          <span>{messagesError}</span>
          <button onClick={onReloadMessages} type="button">
            重试
          </button>
        </div>
      ) : null}
      {!threadsLoading &&
      !threadsError &&
      !messagesLoading &&
      !messagesError &&
      messages.length === 0 ? (
        <p className={styles.agentEmpty}>
          {selectedThreadTitle === null
            ? "先创建一个 Agent 对话。"
            : "从上方预设指令或输入框开始布置卷宗任务。"}
        </p>
      ) : null}
    </div>
  );
}

function AssistantResultSummary({
  message,
  findings,
  referenceLabels,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
  onLocateView,
  renderPatchReview,
}: {
  message: AgentMessageView;
  findings: AgentAuditFindingView[];
  referenceLabels: {
    objects: Record<string, string>;
    events: Record<string, string>;
    issues: Record<string, string>;
  };
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onLocateIssue: (issueId: string) => void;
  onLocateView: (view: AgentSuggestedView) => void;
  renderPatchReview: (message: AgentMessageView, patchSet: AgentPatchSetView) => ReactNode;
}) {
  // A finished turn stays compact until the creator asks to inspect one
  // structured result. This keeps the conversation readable while preserving
  // the existing Patch review actions inside their explicit summary entry.
  const [referencesOpen, setReferencesOpen] = useState(false);
  const [findingsOpen, setFindingsOpen] = useState(false);
  const [patchOpen, setPatchOpen] = useState(false);
  const referenceCount =
    message.referenced_object_ids.length +
    message.referenced_event_ids.length +
    message.referenced_validation_issue_ids.length +
    (message.suggested_view === null ? 0 : 1);
  const patchCount = message.patch_set?.operations.length ?? 0;

  if (referenceCount === 0 && findings.length === 0 && patchCount === 0) {
    return null;
  }

  return (
    <div className={styles.agentResultSummary} aria-label="分析结果摘要">
      {referenceCount > 0 ? (
        <button
          aria-expanded={referencesOpen}
          onClick={() => setReferencesOpen((open) => !open)}
          type="button"
        >
          引用 {referenceCount}
        </button>
      ) : null}
      {findings.length > 0 ? (
        <button
          aria-expanded={findingsOpen}
          onClick={() => setFindingsOpen((open) => !open)}
          type="button"
        >
          验证发现 {findings.length}
        </button>
      ) : null}
      {patchCount > 0 && message.patch_set ? (
        <button
          aria-expanded={patchOpen}
          onClick={() => setPatchOpen((open) => !open)}
          type="button"
        >
          待审修改 {patchCount}
        </button>
      ) : null}
      {referencesOpen ? (
        <ReferenceList
          message={message}
          onLocateEvent={onLocateEvent}
          onLocateIssue={onLocateIssue}
          onLocateObject={onLocateObject}
          onLocateView={onLocateView}
          referenceLabels={referenceLabels}
        />
      ) : null}
      {findingsOpen ? (
        <AgentAuditFindings
          findings={findings}
          issueLabels={referenceLabels.issues}
          objectLabels={referenceLabels.objects}
          eventLabels={referenceLabels.events}
          onLocateEvent={onLocateEvent}
          onLocateIssue={onLocateIssue}
          onLocateObject={onLocateObject}
        />
      ) : null}
      {patchOpen && message.patch_set
        ? renderPatchReview(message, message.patch_set)
        : null}
    </div>
  );
}

function ReferenceList({
  message,
  referenceLabels,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
  onLocateView,
}: {
  message: AgentMessageView;
  referenceLabels: {
    objects: Record<string, string>;
    events: Record<string, string>;
    issues: Record<string, string>;
  };
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onLocateIssue: (issueId: string) => void;
  onLocateView: (view: AgentSuggestedView) => void;
}) {
  return (
    <div className={styles.agentRefs} aria-label="回答引用">
      {message.referenced_object_ids.map((objectId) => (
        <button
          data-ref-kind="object"
          key={`object:${objectId}`}
          onClick={() => onLocateObject(objectId)}
          type="button"
        >
          对象 · {referenceLabels.objects[objectId] ?? objectId}
        </button>
      ))}
      {message.referenced_event_ids.map((eventId) => (
        <button
          data-ref-kind="event"
          key={`event:${eventId}`}
          onClick={() => onLocateEvent(eventId)}
          type="button"
        >
          事件 · {referenceLabels.events[eventId] ?? eventId}
        </button>
      ))}
      {message.referenced_validation_issue_ids.map((issueId) => (
        <button
          data-ref-kind="issue"
          key={`issue:${issueId}`}
          onClick={() => onLocateIssue(issueId)}
          type="button"
        >
          验证 · {referenceLabels.issues[issueId] ?? issueId}
        </button>
      ))}
      {message.suggested_view !== null ? (
        <button
          data-ref-kind="view"
          onClick={() => onLocateView(message.suggested_view ?? "timeline")}
          type="button"
        >
          视图 · {agentViewLabels[message.suggested_view]}
        </button>
      ) : null}
    </div>
  );
}

function AgentAuditFindings({
  findings,
  objectLabels,
  eventLabels,
  issueLabels,
  onLocateObject,
  onLocateEvent,
  onLocateIssue,
}: {
  findings: AgentAuditFindingView[];
  objectLabels: Record<string, string>;
  eventLabels: Record<string, string>;
  issueLabels: Record<string, string>;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onLocateIssue: (issueId: string) => void;
}) {
  return (
    <article className={styles.agentAuditCard} aria-label="逻辑漏洞复查发现">
      <header className={styles.agentAuditHeader}>
        <strong>逻辑漏洞复查发现</strong>
        <span>{findings.length} 项</span>
      </header>
      <ol className={styles.agentAuditList}>
        {findings.map((finding) => (
          <li
            className={styles.agentAuditFinding}
            data-kind={finding.kind}
            data-severity={finding.severity}
            key={finding.finding_id}
          >
            <div className={styles.agentAuditFindingMeta}>
              <b>{finding.finding_id}</b>
              <span>{auditFindingKindLabels[finding.kind] ?? finding.kind}</span>
              <span>
                {auditFindingSeverityLabels[finding.severity] ?? finding.severity}
              </span>
              {finding.needs_manual_review ? <span>待人工确认</span> : null}
            </div>
            <strong>{finding.title}</strong>
            <p>{finding.statement}</p>
            {finding.evidence_object_ids.length > 0 ||
            finding.evidence_event_ids.length > 0 ||
            finding.evidence_validation_issue_ids.length > 0 ? (
              <div className={styles.agentRefs}>
                {finding.evidence_object_ids.map((objectId) => (
                  <button
                    data-ref-kind="object"
                    key={`object:${objectId}`}
                    onClick={() => onLocateObject(objectId)}
                    type="button"
                  >
                    对象 · {objectLabels[objectId] ?? objectId}
                  </button>
                ))}
                {finding.evidence_event_ids.map((eventId) => (
                  <button
                    data-ref-kind="event"
                    key={`event:${eventId}`}
                    onClick={() => onLocateEvent(eventId)}
                    type="button"
                  >
                    事件 · {eventLabels[eventId] ?? eventId}
                  </button>
                ))}
                {finding.evidence_validation_issue_ids.map((issueId) => (
                  <button
                    data-ref-kind="issue"
                    key={`issue:${issueId}`}
                    onClick={() => onLocateIssue(issueId)}
                    type="button"
                  >
                    验证 · {issueLabels[issueId] ?? issueId}
                  </button>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
    </article>
  );
}
