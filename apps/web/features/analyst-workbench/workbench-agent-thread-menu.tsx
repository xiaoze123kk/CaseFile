"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { AgentThreadView } from "@/lib/api-client";

import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-agent-thread-menu.module.css";

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
  const popoverRef = useRef<HTMLElement>(null);
  const [position, setPosition] = useState({ left: 16, top: 64, maxHeight: 560 });
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
  const pinned = visibleThreads.filter((thread) => thread.is_pinned && thread.status === "active");
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

  useLayoutEffect(() => {
    if (!open) return;
    function positionMenu() {
      const anchor = triggerRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const width = Math.min(384, window.innerWidth - 32);
      const below = window.innerHeight - anchor.bottom - 24;
      const above = anchor.top - 24;
      const upwards = below < 360 && above > below;
      const maxHeight = Math.min(560, Math.max(0, upwards ? above : below));
      const height = Math.min(popoverRef.current?.offsetHeight ?? maxHeight, maxHeight);
      setPosition({
        left: Math.max(16, Math.min(anchor.left, window.innerWidth - width - 16)),
        top: upwards ? Math.max(16, anchor.top - height - 8) : anchor.bottom + 8,
        maxHeight,
      });
    }
    function dismiss(event: Event) {
      if (event.target instanceof Node && !popoverRef.current?.contains(event.target) && !triggerRef.current?.contains(event.target)) {
        setOpen(false);
        setRenaming(false);
      }
    }
    positionMenu();
    const observer = new ResizeObserver(positionMenu);
    if (triggerRef.current) observer.observe(triggerRef.current);
    if (popoverRef.current) observer.observe(popoverRef.current);
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("focusin", dismiss);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", positionMenu);
      window.removeEventListener("scroll", positionMenu, true);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("focusin", dismiss);
    };
  }, [open]);

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
      event.stopPropagation();
      close();
    }
  }

  function handleSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
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
      const created = await onCreate();
      await onSearch(query, showArchived);
      if (created) close();
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
        aria-controls={open ? "agent-thread-dialog" : undefined}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={styles.agentThreadTrigger}
        disabled={disabled}
        onClick={() => (open ? close() : openMenu())}
        ref={triggerRef}
        type="button"
      >
        <WorkbenchIcon name="chat" />
        <strong>{activeThread?.title ?? "选择对话"}</strong>
        <span>切换对话</span>
        <WorkbenchIcon name="chevron" />
      </button>
      {open ? createPortal(
        <section
          aria-label="管理 Agent 对话"
          className={styles.agentThreadPopover}
          id="agent-thread-dialog"
          ref={popoverRef}
          role="dialog"
          style={position}
        >
          <header className={styles.menuHeader}>
            <div><h2>对话记录</h2><p>继续调查，或开启新的讨论</p></div>
            <button aria-label="关闭对话菜单" className={styles.closeButton} onClick={close} type="button"><WorkbenchIcon name="close" /></button>
          </header>
          <button className={styles.newThread} disabled={busy} onClick={() => void create()} type="button">
            <span aria-hidden="true">＋</span> 新对话
          </button>
          <div className={styles.agentThreadSearch}>
            <WorkbenchIcon name="search" />
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
            {activeThread ? (
              <>
                <div className={styles.currentThread}><span>当前对话</span><strong title={activeThread.title}>{activeThread.title}</strong></div>
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
                  className={styles.archiveButton}
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
        </section>, document.body,
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
          <WorkbenchIcon name={thread.status === "archived" ? "archive" : "chat"} />
          <strong title={thread.title}>{thread.title}</strong>
          <span>{thread.thread_id === selectedThreadId ? "当前" : thread.status === "archived" ? "已归档" : "打开"}</span>
        </button>
      ))}
    </div>
  );
}
