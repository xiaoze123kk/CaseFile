"use client";

import { useEffect, useRef } from "react";

import styles from "./workbench-agent.module.css";

export function WorkbenchAgentComposer({
  draft,
  onDraftChange,
  onSend,
  onContinueInDesk,
  onCancel,
  contextChips,
  disabled,
  busy,
  surface,
  focusRequest = 0,
}: {
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onContinueInDesk?: () => void;
  onCancel?: () => void;
  contextChips: string[];
  disabled: boolean;
  busy: boolean;
  surface: "quick" | "desk";
  focusRequest?: number;
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 64), 120)}px`;
  }, [draft]);

  useEffect(() => {
    if (surface === "quick") inputRef.current?.focus();
  }, [focusRequest, surface]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    if (event.ctrlKey && event.shiftKey && surface === "quick") {
      event.preventDefault();
      onContinueInDesk?.();
      return;
    }
    if (!event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  }

  return (
    <form
      className={styles.agentComposer}
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
    >
      <div className={styles.agentContextRow} aria-label="当前上下文">
        <span className={styles.agentContextLabel}>当前上下文</span>
        {contextChips.map((chip) => (
          <span className={styles.agentContextChip} key={chip}>
            {chip}
          </span>
        ))}
        {contextChips.length === 0 ? (
          <span className={styles.agentContextEmpty}>未选择对象</span>
        ) : null}
      </div>
      <div className={styles.agentComposerRow}>
        <textarea
          aria-label="给卷宗统筹 Agent 的指令"
          autoComplete="off"
          disabled={disabled}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={busy ? "Agent 正在回复，请稍候…" : "追问当前卷宗…"}
          ref={inputRef}
          rows={2}
          value={draft}
        />
        <button disabled={disabled || !draft.trim()} type="submit">
          {busy ? "回复中" : "发送"}
        </button>
        {surface === "quick" && onContinueInDesk ? (
          <button
            className={styles.agentDeskButton}
            disabled={disabled}
            onClick={onContinueInDesk}
            type="button"
          >
            在统筹台继续
          </button>
        ) : null}
        {busy && onCancel ? (
          <button
            className={styles.agentCancel}
            onClick={onCancel}
            type="button"
          >
            取消
          </button>
        ) : null}
      </div>
      <small className={styles.agentComposerHint}>
        Enter 发送 · Shift+Enter 换行
        {surface === "quick" ? " · Ctrl+Shift+Enter 进入统筹台" : ""}
      </small>
    </form>
  );
}
