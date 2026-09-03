"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { AgentThreadView } from "@/lib/api-client";

import styles from "./workbench-agent.module.css";

const SEARCH_DEBOUNCE_MS = 200;

/**
 * A combobox/listbox manager for persisted Agent threads. It receives only
 * small controller callbacks: persistence and Draft revision checks remain in
 * AgentLivePanel and the API.
 */
export function WorkbenchAgentThreadMenu({
  threads,
  selectedThread,
  selectedThreadId,
  disabled = false,
  onSelect,
  onCreate,
  onRename,
  onSetPinned,
  onSetArchived,
  onSearch,
  placement = "panel",
}: {
  threads: AgentThreadView[];
  selectedThread?: AgentThreadView | null;
  selectedThreadId: number | null;
  disabled?: boolean;
  onSelect: (thread: AgentThreadView) => void;
  onCreate: () => Promise<AgentThreadView | null>;
  onRename: (thread: AgentThreadView, title: string) => Promise<void>;
  onSetPinned: (thread: AgentThreadView, isPinned: boolean) => Promise<void>;
  onSetArchived: (thread: AgentThreadView, archived: boolean) => Promise<void>;
  onSearch: (query: string, includeArchived: boolean) => Promise<void>;
  placement?: "panel" | "toolbar";
}) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [busy, setBusy] = useState(false);

  const orderedThreads = useMemo(
    () =>
      [...threads].sort((left, right) => {
        if (left.is_pinned !== right.is_pinned) return left.is_pinned ? -1 : 1;
        return (right.last_message_at ?? right.updated_at ?? "").localeCompare(
          left.last_message_at ?? left.updated_at ?? "",
        );
      }),
    [threads],
  );
  const visibleThreads = showArchived
    ? orderedThreads
    : orderedThreads.filter((thread) => thread.status === "active");
  const pinned = visibleThreads.filter((thread) => thread.is_pinned);
  const recent = visibleThreads.filter(
    (thread) => !thread.is_pinned && thread.status === "active",
  );
  const archived = visibleThreads.filter((thread) => thread.status === "archived");
  // The listbox is rendered in these groups, so its keyboard index must use
  // the same order rather than the API's interleaved active/archived order.
  const renderedThreads = [...pinned, ...recent, ...archived];
  const activeThread =
    renderedThreads.find((thread) => thread.thread_id === selectedThreadId) ??
    selectedThread ??
    null;
  const selectedIndex = Math.max(
    0,
    renderedThreads.findIndex((thread) => thread.thread_id === selectedThreadId),
  );
  const safeActiveIndex =
    renderedThreads.length === 0
      ? 0
      : Math.min(activeIndex, renderedThreads.length - 1);

  useEffect(() => {
    if (!open) return;
    const timeout = window.setTimeout(() => {
      void onSearch(query, showArchived);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timeout);
  }, [onSearch, open, query, showArchived]);

  function close() {
    setOpen(false);
    setRenaming(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  }

  function openMenu() {
    if (disabled) return;
    setOpen(true);
    setActiveIndex(selectedIndex);
    window.requestAnimationFrame(() => inputRef.current?.focus());
  }

  function select(thread: AgentThreadView) {
    onSelect(thread);
    close();
  }

  function handleEscape(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  function handleSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (renderedThreads.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const offset = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex(
        (index) =>
          (index + offset + renderedThreads.length) % renderedThreads.length,
      );
      return;
    }
    if (event.key === "Enter" && !renaming) {
      const thread = renderedThreads[safeActiveIndex];
      if (!thread) return;
      event.preventDefault();
      select(thread);
    }
  }

  async function create() {
    if (busy) return;
    setBusy(true);
    try {
      await onCreate();
      await onSearch(query, showArchived);
    } finally {
      setBusy(false);
    }
  }

  async function saveRename() {
    if (!activeThread || !renameValue.trim() || busy) return;
    setBusy(true);
    try {
      await onRename(activeThread, renameValue.trim());
      setRenaming(false);
      await onSearch(query, showArchived);
    } finally {
      setBusy(false);
    }
  }

  async function updateCurrent(
    operation: (thread: AgentThreadView) => Promise<void>,
  ) {
    if (!activeThread || busy) return;
    setBusy(true);
    try {
      await operation(activeThread);
      await onSearch(query, showArchived);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={styles.agentThreadManager}
      data-placement={placement}
      onKeyDown={handleEscape}
    >
      <button
        aria-controls="agent-thread-listbox"
        aria-expanded={open}
        aria-haspopup="dialog"
        className={styles.agentThreadTrigger}
        disabled={disabled}
        onClick={() => (open ? close() : openMenu())}
        ref={triggerRef}
        type="button"
      >
        <strong>{activeThread?.title ?? "选择对话"}</strong>
        <span>{activeThread?.is_pinned ? "已置顶" : "对话"} ▾</span>
      </button>
      {open ? (
        <section
          aria-label="管理 Agent 对话"
          className={styles.agentThreadPopover}
          role="dialog"
        >
          <div className={styles.agentThreadSearch}>
            <label htmlFor="agent-thread-search">THREADS</label>
            <input
              aria-activedescendant={
                renderedThreads[safeActiveIndex]
                  ? `agent-thread-option-${renderedThreads[safeActiveIndex]?.thread_id}`
                  : undefined
              }
              aria-autocomplete="list"
              aria-controls="agent-thread-listbox"
              aria-expanded="true"
              aria-label="选择 Agent 对话"
              id="agent-thread-search"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder="搜索对话…"
              ref={inputRef}
              role="combobox"
              value={query}
            />
          </div>
          <div
            aria-label="Agent 对话列表"
            className={styles.agentThreadList}
            id="agent-thread-listbox"
            role="listbox"
          >
            {pinned.length > 0 ? (
              <ThreadGroup
                activeThreadId={renderedThreads[safeActiveIndex]?.thread_id ?? null}
                label="置顶"
                onSelect={select}
                selectedThreadId={selectedThreadId}
                threads={pinned}
              />
            ) : null}
            {recent.length > 0 ? (
              <ThreadGroup
                activeThreadId={renderedThreads[safeActiveIndex]?.thread_id ?? null}
                label="最近"
                onSelect={select}
                selectedThreadId={selectedThreadId}
                threads={recent}
              />
            ) : null}
            {archived.length > 0 ? (
              <ThreadGroup
                activeThreadId={renderedThreads[safeActiveIndex]?.thread_id ?? null}
                label="已归档"
                onSelect={select}
                selectedThreadId={selectedThreadId}
                threads={archived}
              />
            ) : null}
            {visibleThreads.length === 0 ? (
              <p className={styles.agentThreadEmpty}>没有匹配的对话。</p>
            ) : null}
          </div>
          <label className={styles.agentArchivedToggle}>
            <input
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
              type="checkbox"
            />
            显示已归档
          </label>
          {renaming && activeThread ? (
            <form
              className={styles.agentRenameForm}
              onSubmit={(event) => {
                event.preventDefault();
                void saveRename();
              }}
            >
              <label htmlFor="agent-thread-rename">对话标题</label>
              <input
                autoFocus
                disabled={busy}
                id="agent-thread-rename"
                onChange={(event) => setRenameValue(event.target.value)}
                value={renameValue}
              />
              <button disabled={busy || !renameValue.trim()} type="submit">
                保存
              </button>
              <button
                disabled={busy}
                onClick={() => setRenaming(false)}
                type="button"
              >
                取消
              </button>
            </form>
          ) : null}
          <div aria-label="当前对话操作" className={styles.agentThreadActions}>
            <button disabled={busy} onClick={() => void create()} type="button">
              ＋ 新对话
            </button>
            {activeThread ? (
              <>
                <button
                  disabled={busy}
                  onClick={() => {
                    setRenameValue(activeThread.title);
                    setRenaming(true);
                  }}
                  type="button"
                >
                  重命名
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void updateCurrent((thread) =>
                      onSetPinned(thread, !thread.is_pinned),
                    )
                  }
                  type="button"
                >
                  {activeThread.is_pinned ? "取消置顶" : "置顶"}
                </button>
                <button
                  disabled={busy}
                  onClick={() =>
                    void updateCurrent((thread) =>
                      onSetArchived(thread, thread.status !== "archived"),
                    )
                  }
                  type="button"
                >
                  {activeThread.status === "archived" ? "恢复" : "归档"}
                </button>
              </>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function ThreadGroup({
  label,
  threads,
  selectedThreadId,
  activeThreadId,
  onSelect,
}: {
  label: string;
  threads: AgentThreadView[];
  selectedThreadId: number | null;
  activeThreadId: number | null;
  onSelect: (thread: AgentThreadView) => void;
}) {
  return (
    <div aria-label={label} role="group">
      <span className={styles.agentThreadGroupLabel}>{label}</span>
      {threads.map((thread) => (
        <button
          aria-selected={thread.thread_id === selectedThreadId}
          className={styles.agentThreadOption}
          data-active={activeThreadId === thread.thread_id || undefined}
          id={`agent-thread-option-${thread.thread_id}`}
          key={thread.thread_id}
          onClick={() => onSelect(thread)}
          role="option"
          type="button"
        >
          <strong>{thread.title}</strong>
          <span>{thread.status === "archived" ? "已归档" : "打开"}</span>
        </button>
      ))}
    </div>
  );
}
