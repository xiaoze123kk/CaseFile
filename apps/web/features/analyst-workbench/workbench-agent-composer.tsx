"use client";

import { useEffect, useRef, type ReactNode } from "react";

import styles from "./workbench-agent.module.css";
import { WorkbenchIcon } from "./workbench-icon";

export function WorkbenchAgentComposer({
  draft,
  onDraftChange,
  onSend,
  onCancel,
  disabled,
  submitDisabled = false,
  busy,
  surface,
  focusRequest = 0,
  deliveryControl,
}: {
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onCancel?: () => void;
  disabled: boolean;
  submitDisabled?: boolean;
  busy: boolean;
  surface: "dock" | "desk";
  focusRequest?: number;
  deliveryControl?: ReactNode;
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
    if (focusRequest > 0) inputRef.current?.focus();
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
      {deliveryControl ? <div className={styles.agentDeliveryControl}>{deliveryControl}</div> : null}
      <div className={styles.agentComposerRow}>
        <textarea
          aria-label="给卷宗统筹 Agent 的指令"
          autoComplete="off"
          disabled={disabled}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            busy
              ? "可继续起草下一条消息…"
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
          {surface === "dock" ? <WorkbenchIcon name="send" /> : "发送"}
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
