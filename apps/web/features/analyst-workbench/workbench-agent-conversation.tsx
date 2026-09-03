"use client";

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

export function WorkbenchAgentConversation({
  threadsLoading,
  threadsError,
  messagesLoading,
  messagesError,
  messages,
  selectedThreadTitle,
  liveRuns,
  onReconnect,
  onReloadMessages,
  onRetryMessage,
}: {
  threadsLoading: boolean;
  threadsError: string | null;
  messagesLoading: boolean;
  messagesError: string | null;
  messages: PublicAgentMessage[];
  selectedThreadTitle: string | null;
  liveRuns: Record<number, PublicAgentRun>;
  onReconnect: () => void;
  onReloadMessages: () => void;
  onRetryMessage: (message: PublicAgentMessage) => void;
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
                <p>{message.body}</p>
              </div>
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
            : "从下方输入框开始布置卷宗任务。"}
        </p>
      ) : null}
    </div>
  );
}
