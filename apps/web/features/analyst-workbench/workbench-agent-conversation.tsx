"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import type {
  PublicAgentMessage,
  PublicAgentRun,
  PublicFinding,
} from "@casefile/contracts";

import styles from "./workbench-agent.module.css";
import { AgentProgress } from "./workbench-agent-progress";
import type { RunFeedback } from "./workbench-agent-feedback";
import { AgentPatchCard } from "./workbench-agent-patch-card";

export function AgentAnswer({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let items: string[] = [];
  let ordered = false;
  let start = 1;
  const flushParagraph = () => {
    if (paragraph.length) blocks.push(<p key={blocks.length}>{paragraph.join("\n")}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (items.length) {
      const children = items.map((item, index) => <li key={index}>{item}</li>);
      blocks.push(ordered
        ? <ol key={blocks.length} start={start}>{children}</ol>
        : <ul key={blocks.length}>{children}</ul>);
    }
    items = [];
  };
  for (const line of text.split(/\r?\n/u)) {
    const match = /^\s*(?:(\d+)[.)、]|([-*•]))\s+(.+)$/u.exec(line);
    if (match) {
      flushParagraph();
      const nextOrdered = Boolean(match[1]);
      if (items.length && ordered !== nextOrdered) flushList();
      if (!items.length) start = Number(match[1] ?? 1);
      ordered = nextOrdered;
      items.push(match[3]);
    } else if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (items.length && /^\s+/u.test(line)) {
      items[items.length - 1] += `\n${line.trim()}`;
    } else {
      flushList();
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return <div className={styles.agentAnswer}>{blocks}</div>;
}

export function agentAuditFindingsFor(
  message: PublicAgentMessage,
): PublicFinding[] {
  return message.findings;
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
  onFocusPatch,
  onFocusFinding,
  feedback = {},
  sendingText = null,
  sendError = null,
  taskControls = null,
  renderPatch,
  patchError = null,
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
  onFocusPatch?: (id: number) => void;
  onFocusFinding?: (id: string) => void;
  feedback?: Record<number, RunFeedback>;
  sendingText?: string | null;
  sendError?: string | null;
  taskControls?: ReactNode;
  renderPatch?: (message: PublicAgentMessage) => ReactNode;
  patchError?: string | null;
}) {
  const latestAssistantId = messages.filter((message) => message.role === "assistant").at(-1)?.message_id;
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  useEffect(() => {
    const element = scroller.current;
    if (element && follow.current) element.scrollTop = element.scrollHeight;
  }, [messages, feedback, sendingText]);
  return (
    <div className={styles.agentMessages} ref={scroller} onScroll={() => {
      const element = scroller.current;
      if (!element) return;
      follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 64;
      setShowLatest(!follow.current);
    }}>
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
        const progress = run ? feedback[run.run_id] : undefined;
        const preview = progress?.preview;
        const previewText = preview && !preview.discarded && !progress?.gap ? preview.text : "";
        const unfinished = preview?.invalidated || (run && ["cancelled", "failed"].includes(run.status));
        return (
          <article
            className={styles.agentTurn}
            data-role={message.role}
            data-has-patch={message.patch ? true : undefined}
            key={message.message_id}
          >
            {message.role === "assistant" ? <AgentProgress run={run} feedback={progress}
              controls={message.message_id === latestAssistantId ? taskControls : null} /> : null}
            {message.body !== null && !(run?.status === "failed" && message.body === run.failure?.message) ? (
              <div className={styles.agentTurnContent}>
                {message.role === "assistant" ? <AgentAnswer text={message.body} /> : <p>{message.body}</p>}
              </div>
            ) : null}
            {message.body === null && previewText ? unfinished ? <details className={styles.agentPreview}>
              <summary>未形成正式结论 · 查看未完成预览</summary><AgentAnswer text={previewText} />
            </details> : <div className={styles.agentPreview} aria-live="off">
              <small>{run?.status === "succeeded" ? "正在同步结果" : preview?.ready ? "正在确认结果，尚未完成校验" : "生成中，尚未完成校验"}</small><AgentAnswer text={previewText} />
            </div> : null}
            {message.patch ? renderPatch ? renderPatch(message) : <AgentPatchCard patchSet={message.patch}
              onDetails={onFocusPatch ? () => onFocusPatch(message.patch!.patch_id) : undefined} /> : null}
            {onFocusFinding ? message.findings.map((finding) => <button type="button" key={finding.finding_id} onClick={() => onFocusFinding(finding.finding_id)}>查看验证：{finding.title}</button>) : null}
            {message.role === "assistant" && (message.status === "failed" || run?.status === "failed") ? (
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
      {latestAssistantId === undefined && taskControls ? <section className={styles.agentProgress} aria-label="工作记录">{taskControls}</section> : null}
      {sendingText ? <article className={styles.agentTurn} data-role="user"><p>{sendingText}</p><small role="status">正在发送…</small></article> : null}
      {sendError ? <div className={styles.agentFailure} role="status"><strong>发送失败</strong><span>{sendError}</span><small>输入内容已保留，可修改后重新发送。</small></div> : null}
      {patchError ? <div className={styles.agentFailure} role="alert"><strong>修改操作未完成</strong><span>{patchError}</span><small>请按当前卷宗重新审阅后再试。</small></div> : null}
      {showLatest ? <button type="button" className={styles.agentBackLatest} onClick={() => {
        follow.current = true; setShowLatest(false);
        if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
      }}>回到最新</button> : null}
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
