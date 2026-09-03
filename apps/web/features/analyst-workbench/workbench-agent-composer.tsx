"use client";

import { useEffect, useRef } from "react";

import styles from "./workbench-agent.module.css";
import { WorkbenchIcon } from "./workbench-icon";

export function WorkbenchAgentComposer({
  draft,
  onDraftChange,
  onSend,
  onCancel,
  contextChips,
  disabled,
  submitDisabled = false,
  busy,
  surface,
  focusRequest = 0,
}: {
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onCancel?: () => void;
  contextChips: string[];
  disabled: boolean;
  submitDisabled?: boolean;
  busy: boolean;
  surface: "dock" | "desk";
  focusRequest?: number;
}) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    if (surface === "dock") {
      input.style.height = "44px";
      return;
    }
    input.style.height = "auto";
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 64), 120)}px`;
  }, [draft, surface]);

  useEffect(() => {
    if (surface === "dock") inputRef.current?.focus();
  }, [focusRequest, surface]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    if (!event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  }

  return (
    <form
      className={styles.agentComposer}
      data-surface={surface}
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
    >
      {surface === "desk" ? (
        <div className={styles.agentContextRow} aria-label="当前上下文">
          <span className={styles.agentContextLabel}>当前上下文</span>
          {contextChips.map((chip, index) => (
            <span className={styles.agentContextChip} key={`${chip}:${index}`}>
              {chip}
            </span>
          ))}
          {contextChips.length === 0 ? (
            <span className={styles.agentContextEmpty}>未选择对象</span>
          ) : null}
        </div>
      ) : null}
      <div className={styles.agentComposerRow}>
        <textarea
          aria-label="给卷宗统筹 Agent 的指令"
          autoComplete="off"
          disabled={disabled}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            busy
              ? "卷宗正在梳理线索，请稍候……"
              : surface === "dock"
                ? "写下你的疑问，让卷宗循着线索回答……"
                : "追问当前卷宗…"
          }
          ref={inputRef}
          rows={surface === "dock" ? 1 : 2}
          value={draft}
        />
        <button
          aria-label="发送"
          disabled={disabled || submitDisabled || !draft.trim()}
          type="submit"
        >
          {surface === "dock" ? <WorkbenchIcon name="send" /> : busy ? "回复中" : "发送"}
        </button>
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
      {surface === "desk" ? (
        <small className={styles.agentComposerHint}>Enter 发送 · Shift+Enter 换行</small>
      ) : null}
    </form>
  );
}
