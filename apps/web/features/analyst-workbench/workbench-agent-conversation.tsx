"use client";

import { useState, type ReactNode } from "react";
import type {
  PublicAgentMessage,
  PublicAgentRun,
  PublicFinding,
} from "@casefile/contracts";

import styles from "./workbench-agent.module.css";

export function agentAuditFindingsFor(
  message: PublicAgentMessage,
): PublicFinding[] {
  return message.findings;
}

function formatRecordTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "记录时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

const activityLabels: Record<Exclude<PublicAgentRun["activity"], null>, string> = {
  understanding: "正在理解你的要求",
  reading: "正在阅读卷宗",
  checking: "正在检查前后一致性",
  preparing_changes: "正在整理修改建议",
  finalizing: "正在完成回复",
};

function runActivityLabel(run: PublicAgentRun | null): string {
  if (run === null || run.status === "queued") return "回复已排队";
  if (run.status === "cancelling") return "正在停止回复";
  if (run.activity !== null) return activityLabels[run.activity];
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
  liveRuns,
  busy,
  onReconnect,
  onReloadMessages,
  onRetryMessage,
  onLocateObject,
  onLocateEvent,
  onFocusPatch,
  onFocusFinding,
  renderRoutingFeedback,
}: {
  surface: "quick" | "desk";
  threadsLoading: boolean;
  threadsError: string | null;
  messagesLoading: boolean;
  messagesError: string | null;
  messages: PublicAgentMessage[];
  selectedThreadTitle: string | null;
  liveRuns: Record<number, PublicAgentRun>;
  busy: boolean;
  onReconnect: () => void;
  onReloadMessages: () => void;
  onRetryMessage: (message: PublicAgentMessage) => void;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onFocusPatch: (patchId: number) => void;
  onFocusFinding: (findingId: string) => void;
  renderRoutingFeedback: (message: PublicAgentMessage) => ReactNode;
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
        const run =
          message.run === null
            ? null
            : (liveRuns[message.run.run_id] ?? message.run);
        return (
          <article
            className={styles.agentTurn}
            data-role={message.role}
            key={message.message_id}
          >
            <header className={styles.agentTurnMeta}>
              <span>{message.role === "assistant" ? "卷宗统筹" : "你"}</span>
              <time dateTime={message.created_at}>
                {formatRecordTime(message.created_at)}
              </time>
            </header>
            {message.body !== null ? (
              <div className={styles.agentTurnContent}>
                {message.role === "assistant" ? <strong>结论</strong> : null}
                <p>{message.body}</p>
              </div>
            ) : null}
            {message.role === "assistant" && message.status === "completed" ? (
              <AssistantResultSummary
                message={message}
                onLocateEvent={onLocateEvent}
                onLocateObject={onLocateObject}
                onFocusPatch={onFocusPatch}
                onFocusFinding={onFocusFinding}
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
                  ? `卷宗统筹 · ${runActivityLabel(run)}`
                  : "正在整理回复…"}
              </p>
            ) : null}
            {message.role === "assistant" && message.status === "failed" ? (
              <div className={styles.agentFailure} role="status">
                <strong>回复未完成</strong>
                <span>
                  {run?.failure?.message ?? "这次回复未能完成，请稍后重试。"}
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
  onLocateObject,
  onLocateEvent,
  onFocusPatch,
  onFocusFinding,
}: {
  message: PublicAgentMessage;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onFocusPatch: (patchId: number) => void;
  onFocusFinding: (findingId: string) => void;
}) {
  const [referencesOpen, setReferencesOpen] = useState(false);
  const patchCount = message.patch?.changes.length ?? 0;

  if (
    message.references.length === 0 &&
    message.findings.length === 0 &&
    patchCount === 0
  ) {
    return null;
  }

  return (
    <div className={styles.agentResultSummary} aria-label="分析结果摘要">
      {message.references.length > 0 ? (
        <button
          aria-expanded={referencesOpen}
          onClick={() => setReferencesOpen((open) => !open)}
          type="button"
        >
          引用 {message.references.length}
        </button>
      ) : null}
      {message.findings.length > 0 ? (
        <button
          aria-expanded={false}
          onClick={() => onFocusFinding(message.findings[0]?.finding_id ?? "")}
          type="button"
        >
          验证发现 {message.findings.length}
        </button>
      ) : null}
      {patchCount > 0 && message.patch ? (
        <button
          aria-expanded={false}
          onClick={() => onFocusPatch(message.patch?.patch_id ?? 0)}
          type="button"
        >
          待审修改 {patchCount}
        </button>
      ) : null}
      {referencesOpen ? (
        <ReferenceList
          message={message}
          onLocateEvent={onLocateEvent}
          onLocateObject={onLocateObject}
          onFocusFinding={onFocusFinding}
        />
      ) : null}
      {patchCount > 0 && message.patch ? (
        <p className={styles.agentInspectorHint}>
          已将这组修改移至右侧对象上下文 Inspector 审阅。
        </p>
      ) : null}
    </div>
  );
}

function ReferenceList({
  message,
  onLocateObject,
  onLocateEvent,
  onFocusFinding,
}: {
  message: PublicAgentMessage;
  onLocateObject: (objectId: string) => void;
  onLocateEvent: (eventId: string) => void;
  onFocusFinding: (findingId: string) => void;
}) {
  return (
    <div className={styles.agentRefs} aria-label="回答引用">
      {message.references.map((reference, index) => (
        <button
          data-ref-kind={reference.kind}
          key={`${reference.kind}:${index}`}
          onClick={() => {
            if (reference.kind === "event") onLocateEvent(reference.target_id);
            else if (reference.kind === "finding") {
              onFocusFinding(reference.target_id);
            } else onLocateObject(reference.target_id);
          }}
          type="button"
        >
          {reference.kind === "event"
            ? "事件"
            : reference.kind === "finding"
              ? "发现"
              : "卷宗"}
          {` · ${reference.label}`}
        </button>
      ))}
    </div>
  );
}
