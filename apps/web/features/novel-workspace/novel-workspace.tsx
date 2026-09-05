"use client";

import { useRef, useState, type CSSProperties } from "react";
import type { WorkbenchSeed } from "@/features/analyst-workbench/analyst-fixture";
import { WorkbenchIcon as Icon } from "@/features/analyst-workbench/workbench-icon";
import {
  applyNovelChanges,
  createNovelDraft,
  exportNovel,
  manuscriptFromText,
  parseSavedDraft,
  wordCount,
  type NovelCollaborator,
  type NovelDraft,
  type NovelManuscript,
  type NovelReply,
} from "./novel-document";
import {
  NovelImportDialog,
  NovelReview,
  NovelHistory,
} from "./novel-workspace-panels";
import styles from "./novel-workspace.module.css";
import { NovelCompilerPanel } from "./novel-compiler-panel";
import type { NovelCompileScope } from "./novel-compiler-api";

const modes = [
  { id: "discuss", label: "讨论", icon: "chat" },
  { id: "rewrite", label: "改写", icon: "document" },
  { id: "polish", label: "润色", icon: "lightbulb" },
] as const;
type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  revision: number;
  reply?: NovelReply;
  applied?: boolean;
};

export function NovelWorkspace({
  seed,
  scopeKey,
  onBack,
  manuscript,
  collaborate,
  compileScope,
}: {
  seed: WorkbenchSeed;
  scopeKey: string;
  onBack: () => void;
  manuscript?: NovelManuscript;
  collaborate?: NovelCollaborator;
  compileScope?: NovelCompileScope;
}) {
  const storageKey = `casefile:novel:v1:${scopeKey}`;
  const [loaded] = useState(() => {
    try {
      const raw =
        typeof window === "undefined" ? null : localStorage.getItem(storageKey);
      return {
        draft: raw
          ? parseSavedDraft(raw)
          : manuscript
            ? createNovelDraft(manuscript)
            : null,
        error: "",
        saved: Boolean(raw),
      };
    } catch {
      return {
        draft: manuscript ? createNovelDraft(manuscript) : null,
        error: "本地编辑稿读取失败。请先备份本地数据，避免覆盖旧稿。",
        saved: false,
      };
    }
  });
  const [draft, setDraft] = useState<NovelDraft | null>(loaded.draft);
  const [status, setStatus] = useState(
    loaded.error ||
      (loaded.saved
        ? "编辑稿保存在此浏览器"
        : loaded.draft
          ? "初稿已载入"
          : "等待整篇初稿"),
  );
  const [storageBlocked] = useState(Boolean(loaded.error));
  const [sidebar, setSidebar] = useState(true);
  const [navTab, setNavTab] = useState<"chapters" | "sources">("chapters");
  const [focus, setFocus] = useState(false);
  const [ratio, setRatio] = useState(46);
  const [importOpen, setImportOpen] = useState(false);
  const [compilerOpen, setCompilerOpen] = useState(false);
  const [history, setHistory] = useState<NovelDraft[] | null>(null);
  const [original, setOriginal] = useState(false);
  const [wholeBook, setWholeBook] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [mode, setMode] = useState<"discuss" | "rewrite" | "polish">("discuss");
  const [scope, setScope] = useState<"chapter" | "book" | "selection">(
    "chapter",
  );
  const [selection, setSelection] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState<string | null>(null);
  const splitRef = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const draftRef = useRef(draft);
  const chapter =
    draft?.chapters.find((item) => item.id === draft.selectedChapterId) ??
    draft?.chapters[0];
  const shownChapters = original ? draft?.original.chapters : draft?.chapters;
  const shownChapter = shownChapters?.find((item) => item.id === chapter?.id);
  const total =
    draft?.chapters.reduce((sum, item) => sum + wordCount(item.text), 0) ?? 0;
  const reviewMessage = messages.find((message) => message.id === review);

  function save(next: NovelDraft) {
    draftRef.current = next;
    setDraft(next);
    if (storageBlocked) {
      setStatus("未保存：本地旧稿读取失败，请导出当前稿备份");
      return;
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
      setStatus("已保存到此浏览器");
    } catch {
      setStatus("本地保存失败，请导出当前稿备份");
    }
  }

  function selectChapter(id: string) {
    if (draft) save({ ...draft, selectedChapterId: id });
    setSelection("");
    if (scope === "selection") setScope("chapter");
  }

  function replaceDraft(next: NovelDraft) {
    if (draft) {
      try {
        localStorage.setItem(
          `${storageKey}:backup:${draft.original.id}:${draft.revision}:${crypto.randomUUID()}`,
          JSON.stringify(draft),
        );
      } catch {
        setError("旧稿备份失败，请先导出旧稿，再导入新初稿。");
        return false;
      }
    }
    save(next);
    setMessages([]);
    setReview(null);
    setSelection("");
    setScope("chapter");
    setOriginal(false);
    setImportOpen(false);
    setHistory(null);
    setError("");
    return true;
  }

  function openHistory() {
    try {
      const drafts: NovelDraft[] = [];
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (key?.startsWith(`${storageKey}:backup:`)) {
          const raw = localStorage.getItem(key);
          if (raw) drafts.push(parseSavedDraft(raw));
        }
      }
      setHistory(drafts.reverse());
      setError("");
    } catch {
      setError("历史稿读取失败，请先导出当前稿备份。");
    }
  }

  async function send() {
    if (!draft || !chapter || !collaborate || busy || !instruction.trim())
      return;
    const text = instruction.trim();
    const revision = draft.revision;
    const manuscriptId = draft.original.id;
    setMessages((items) => [
      ...items,
      { id: crypto.randomUUID(), role: "user", text, revision },
    ]);
    setInstruction("");
    setBusy(true);
    setError("");
    try {
      const reply = await collaborate({
        instruction: text,
        mode,
        scope,
        selection: scope === "selection" ? selection : "",
        chapterId: chapter.id,
        manuscript: {
          ...draft.original,
          chapters: draft.chapters.map((item) => ({ ...item })),
        },
        revision,
      });
      if (draftRef.current?.original.id !== manuscriptId) return;
      setMessages((items) => [
        ...items,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: reply.message,
          revision,
          reply,
        },
      ]);
    } catch {
      setInstruction((current) => current || text);
      setError("协作请求失败，你的指令已保留，请重试。");
    } finally {
      setBusy(false);
    }
  }

  function download() {
    if (!draft) return;
    const blob = new Blob([exportNovel(draft.original.title, draft.chapters)], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${draft.original.title.replace(/[<>:"/\\|?*]/gu, "_")}.md`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className={styles.workspace} aria-label="小说协作工作台">
      <div className={styles.entrance} aria-hidden="true">
        <div className={styles.entranceGlow} />
        <div className={styles.entranceBook}>
          <div className={styles.bookBack} />
          <div className={styles.bookEdges} />
          <div className={styles.bookPage} />
          <div className={styles.bookPage} />
          <div className={styles.bookPage} />
          <div className={styles.bookCover}>
            <span className={styles.bookRibbon} />
            <span className={styles.bookCategory}>小说 · 创作手稿</span>
            <strong>{seed.caseMeta.title}</strong>
            <svg className={styles.bookEmblem} viewBox="0 0 64 64" fill="none">
              <path d="M32 8 48 32 32 56 16 32Z" />
              <path d="M8 32h48M32 8v48M24 24l16 16m0-16L24 40" />
              <circle cx="32" cy="32" r="7" />
            </svg>
          </div>
        </div>
      </div>
      <header className={styles.topbar}>
        <button className={styles.back} onClick={onBack} type="button">
          <Icon name="chevron-left" />
          编译中心
        </button>
        <span className={styles.divider} />
        <div className={styles.bookIdentity}>
          <span>小说工作台</span>
          <strong>{draft?.original.title ?? seed.caseMeta.title}</strong>
        </div>
        <span className={styles.saveStatus} role="status">
          {status}
        </span>
        <div className={styles.topActions}>
          {compileScope ? <button disabled={busy || storageBlocked} onClick={() => setCompilerOpen(true)} type="button">
            <Icon name="document" />小说编译
          </button> : null}
          <button
            disabled={busy || storageBlocked}
            onClick={openHistory}
            type="button"
          >
            <Icon name="clock" />
            历史稿
          </button>
          <button
            aria-pressed={focus}
            onClick={() => setFocus(!focus)}
            type="button"
          >
            <Icon name="focus" />
            {focus ? "退出专注" : "专注阅读"}
          </button>
          <button
            disabled={busy || storageBlocked}
            onClick={() => setImportOpen(true)}
            type="button"
          >
            <Icon name="document" />
            导入初稿
          </button>
          <button
            className={styles.export}
            disabled={!draft}
            onClick={download}
            type="button"
          >
            <Icon name="export" />
            导出小说
          </button>
        </div>
      </header>
      <div className={styles.bookbar}>
        <button
          aria-label={sidebar ? "收起章节与资料" : "展开章节与资料"}
          aria-expanded={sidebar}
          onClick={() => setSidebar(!sidebar)}
          type="button"
        >
          <Icon name={sidebar ? "panel-collapse-left" : "panel-expand-right"} />
        </button>
        <span>{draft ? `${draft.chapters.length} 章` : "尚无正文"}</span>
        <span className={styles.dot}>·</span>
        <span>{total.toLocaleString()} 字</span>
        <span className={styles.bookbarNote}>
          {draft
            ? `${draft.original.sourceLabel} / 编辑稿`
            : "整篇生成后，在这里继续打磨"}
        </span>
      </div>
      <div className={styles.body}>
        {sidebar && !focus ? (
          <aside className={styles.sidebar} aria-label="章节与卷宗资料">
            <div className={styles.tabs} role="tablist" aria-label="小说导航">
              <button
                role="tab"
                aria-selected={navTab === "chapters"}
                onClick={() => setNavTab("chapters")}
                type="button"
              >
                章节目录
              </button>
              <button
                role="tab"
                aria-selected={navTab === "sources"}
                onClick={() => setNavTab("sources")}
                type="button"
              >
                卷宗资料
              </button>
            </div>
            <div className={styles.navContent}>
              {navTab === "chapters" ? (
                <>
                  <div className={styles.navHeading}>
                    <span>全书章节</span>
                    <small>{draft?.chapters.length ?? 0}</small>
                  </div>
                  {draft ? (
                    <ol className={styles.chapterList}>
                      {draft.chapters.map((item, index) => (
                        <li key={item.id}>
                          <button
                            aria-current={
                              chapter?.id === item.id ? "true" : undefined
                            }
                            onClick={() => selectChapter(item.id)}
                            type="button"
                          >
                            <span className={styles.chapterNumber}>
                              {String(index + 1).padStart(2, "0")}
                            </span>
                            <span>
                              <strong>{item.title}</strong>
                              <small>
                                {wordCount(item.text).toLocaleString()} 字
                                {item.text !==
                                draft.original.chapters.find(
                                  (entry) => entry.id === item.id,
                                )?.text
                                  ? " · 已修改"
                                  : " · 初稿"}
                              </small>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className={styles.navEmpty}>
                      收到整篇初稿后，章节会出现在这里。
                    </p>
                  )}
                </>
              ) : (
                <>
                  <p className={styles.sourceNote}>
                    来自当前卷宗 · {seed.caseMeta.revision}
                  </p>
                  {[
                    { label: "人物与实体", kinds: ["person", "entity"], icon: "entity" as const, tone: "people" },
                    { label: "线索与信息", kinds: ["evidence", "information"], icon: "search" as const, tone: "clues" },
                    { label: "地点", kinds: ["location"], icon: "location" as const, tone: "places" },
                  ].map((group) => (
                    <details
                      className={styles.sourceGroup}
                      key={group.label}
                      open
                    >
                      <summary>
                        <Icon name="chevron-right" className={styles.sourceChevron} />
                        <span className={styles.sourceIcon} data-tone={group.tone}>
                          <Icon name={group.icon} />
                        </span>
                        <span>{group.label}</span>
                        <small>
                          {
                            seed.caseObjects.filter((item) =>
                              group.kinds.includes(item.kind),
                            ).length
                          }
                        </small>
                      </summary>
                      {seed.caseObjects
                        .filter((item) => group.kinds.includes(item.kind))
                        .map((item) => (
                          <details className={styles.sourceItem} key={item.id}>
                            <summary>{item.label}</summary>
                            <p>
                              {("description" in item &&
                              typeof item.description === "string"
                                ? item.description
                                : "") ||
                                item.meta ||
                                "暂无补充说明"}
                            </p>
                          </details>
                        ))}
                    </details>
                  ))}
                  <details className={styles.sourceGroup}>
                    <summary>
                      <Icon name="chevron-right" className={styles.sourceChevron} />
                      <span className={styles.sourceIcon} data-tone="timeline">
                        <Icon name="clock" />
                      </span>
                      <span>事实时间线</span>
                      <small>{seed.timelineEvents.length}</small>
                    </summary>
                    {seed.timelineEvents.map((item) => (
                      <div className={styles.sourceItem} key={item.id}>
                        <strong>{item.label}</strong>
                        <p>{item.summary}</p>
                      </div>
                    ))}
                  </details>
                </>
              )}
            </div>
            <footer className={styles.sidebarFooter}>
              <Icon name="archive" />
              <span>
                卷宗提供事实
                <br />
                <small>小说保留独立编辑稿</small>
              </span>
            </footer>
          </aside>
        ) : null}
        <div
          ref={splitRef}
          className={styles.split}
          data-focus={focus}
          style={{ "--conversation-width": `${ratio}%` } as CSSProperties}
        >
          {!focus ? (
            <section className={styles.conversation} aria-label="小说 AI 协作">
              <header className={styles.conversationHeader}>
                <div className={styles.modeTabs} aria-label="协作模式">
                  {modes.map((item) => (
                    <button
                      aria-pressed={mode === item.id}
                      key={item.id}
                      onClick={() => setMode(item.id)}
                      type="button"
                    >
                      <Icon name={item.icon} />
                      {item.label}
                    </button>
                  ))}
                </div>
                <span className={styles.companionLabel}>创作搭档</span>
              </header>
              <div className={styles.modeNote}>
                <Icon name="lightbulb" />
                <span>
                  {mode === "discuss"
                    ? "一起推敲情节、人物和伏笔，保留你的创作判断。"
                    : mode === "rewrite"
                      ? "提出改写方向，查看修改建议后再决定是否采纳。"
                      : "打磨语言与节奏，让人物说出属于自己的话。"}
                </span>
              </div>
              <div className={styles.messages} aria-live="polite">
                <article className={styles.welcome}>
                  <span className={styles.eyebrow}>从初稿，到你的作品</span>
                  <h1>
                    {draft
                      ? "故事已经在纸上，\n接下来，一起打磨。"
                      : "给完整的故事，\n留一张修改的书桌。"}
                  </h1>
                  <p>
                    {draft
                      ? "右侧是你的小说。可以直接修改正文，也可以选中一段，与 AI 讨论它的下一种写法。"
                      : "小说编译器完成整篇初稿后，就从这里开始协作。已有生成的全文，也可以直接导入。"}
                  </p>
                  {!draft ? (
                    <button
                      className={styles.importCta}
                      disabled={storageBlocked}
                      onClick={() => setImportOpen(true)}
                      type="button"
                    >
                      导入已有初稿 <Icon name="chevron-right" />
                    </button>
                  ) : null}
                </article>
                {draft && messages.length === 0 ? (
                  <div className={styles.prompts}>
                    <span>从一个具体的修改开始</span>
                    {[
                      "检查全篇的伏笔与回收是否呼应",
                      "让当前章节的节奏更紧凑",
                      "调整人物对白，保留原有事实",
                    ].map((text, index) => (
                      <button
                        key={text}
                        onClick={() => {
                          setInstruction(text);
                          setScope(index === 0 ? "book" : "chapter");
                          composer.current?.focus();
                        }}
                        type="button"
                      >
                        {text}
                        <Icon name="chevron-right" />
                      </button>
                    ))}
                  </div>
                ) : null}
                {messages.map((message) => (
                  <article
                    className={styles.message}
                    data-role={message.role}
                    key={message.id}
                  >
                    <small>{message.role === "user" ? "你" : "创作搭档"}</small>
                    <p>{message.text}</p>
                    {message.reply?.changes?.length ? (
                      <button
                        disabled={message.applied}
                        onClick={() => setReview(message.id)}
                        type="button"
                      >
                        {message.applied
                          ? "已采纳修改"
                          : `查看 ${message.reply.changes.length} 章修改`}
                      </button>
                    ) : null}
                  </article>
                ))}
                {busy ? <p role="status">正在通读与整理修改建议…</p> : null}
              </div>
              <div className={styles.composerArea}>
                {!collaborate ? (
                  <p className={styles.connectionNote}>
                    AI 正文协作尚未接入，修改想法可先写在下方。
                  </p>
                ) : null}
                {error ? (
                  <p role="alert" className={styles.error}>
                    {error}
                  </p>
                ) : null}
                <div className={styles.scopeRow}>
                  <label>
                    修改范围
                    <select
                      aria-label="修改范围"
                      value={scope}
                      onChange={(event) =>
                        setScope(event.target.value as typeof scope)
                      }
                    >
                      <option value="chapter">当前章节</option>
                      <option value="book">整篇小说</option>
                      <option value="selection" disabled={!selection}>
                        选中段落
                      </option>
                    </select>
                  </label>
                  {selection ? (
                    <button
                      onClick={() => {
                        setSelection("");
                        setScope("chapter");
                      }}
                      type="button"
                    >
                      已选 {wordCount(selection)} 字 ×
                    </button>
                  ) : null}
                </div>
                <form
                  className={styles.composer}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void send();
                  }}
                >
                  <textarea
                    ref={composer}
                    aria-label="小说修改指令"
                    placeholder="说说你想怎么修改这篇小说…"
                    value={instruction}
                    onChange={(event) => setInstruction(event.target.value)}
                    onKeyDown={(event) => {
                      if (
                        event.key === "Enter" &&
                        !event.shiftKey &&
                        !event.nativeEvent.isComposing
                      ) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <button
                    aria-label="发送小说修改指令"
                    title={!collaborate ? "等待接入 AI 正文协作" : "发送"}
                    disabled={
                      !collaborate || !draft || busy || !instruction.trim()
                    }
                    type="submit"
                  >
                    <Icon name="send" />
                  </button>
                </form>
                <span className={styles.composerHint}>
                  修改建议经你采纳后才会写入正文
                </span>
              </div>
            </section>
          ) : null}
          {!focus ? (
            <div
              className={styles.resizeHandle}
              role="separator"
              aria-label="调整对话与正文宽度"
              aria-orientation="vertical"
              aria-valuemin={30}
              aria-valuemax={60}
              aria-valuenow={ratio}
              tabIndex={0}
              onKeyDown={(event) => {
                if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
                  event.preventDefault();
                  setRatio((value) =>
                    Math.max(
                      30,
                      Math.min(
                        60,
                        value + (event.key === "ArrowLeft" ? -2 : 2),
                      ),
                    ),
                  );
                }
              }}
              onPointerDown={(event) =>
                event.currentTarget.setPointerCapture(event.pointerId)
              }
              onPointerMove={(event) => {
                if (!event.currentTarget.hasPointerCapture(event.pointerId))
                  return;
                const rect = splitRef.current?.getBoundingClientRect();
                if (rect)
                  setRatio(
                    Math.round(
                      Math.max(
                        30,
                        Math.min(
                          60,
                          ((event.clientX - rect.left) / rect.width) * 100,
                        ),
                      ),
                    ),
                  );
              }}
              onPointerUp={(event) =>
                event.currentTarget.releasePointerCapture(event.pointerId)
              }
            >
              <span>⋮</span>
            </div>
          ) : null}
          <section className={styles.manuscript} aria-label="小说正文">
            <header className={styles.editorHeader}>
              <select
                aria-label="当前章节"
                disabled={!draft}
                value={chapter?.id ?? ""}
                onChange={(event) => selectChapter(event.target.value)}
              >
                {draft ? (
                  draft.chapters.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title}
                    </option>
                  ))
                ) : (
                  <option value="">章节正文</option>
                )}
              </select>
              <button
                aria-pressed={wholeBook}
                disabled={!draft}
                onClick={() => setWholeBook(!wholeBook)}
                type="button"
              >
                <Icon name="document" />
                {wholeBook ? "返回单章" : "通读全文"}
              </button>
            </header>
            <div className={styles.editorTabs}>
              <div role="tablist" aria-label="正文版本">
                <button
                  role="tab"
                  aria-selected={!original}
                  onClick={() => setOriginal(false)}
                  type="button"
                >
                  编辑稿
                </button>
                <button
                  role="tab"
                  aria-selected={original}
                  onClick={() => setOriginal(true)}
                  type="button"
                >
                  原始初稿
                </button>
              </div>
              <span>{original ? "只读" : "可直接编辑"}</span>
            </div>
            {!draft ? (
              <div className={styles.emptyManuscript}>
                <Icon name="document" />
                <h2>等待故事落笔</h2>
                <p>
                  整篇初稿将在这里展开。
                  <br />
                  导入后即可按章节阅读和修改。
                </p>
                <button
                  disabled={storageBlocked}
                  onClick={() => setImportOpen(true)}
                  type="button"
                >
                  导入全文
                </button>
              </div>
            ) : (
              <div className={styles.pages}>
                {(wholeBook
                  ? shownChapters
                  : shownChapter
                    ? [shownChapter]
                    : []
                )?.map((item) => (
                  <article
                    className={styles.page}
                    key={`${original}:${item.id}`}
                  >
                    <span className={styles.pageEyebrow}>
                      {original ? "原始初稿" : "小说 · 编辑稿"}
                    </span>
                    <h2>{item.title}</h2>
                    {original ? (
                      <div className={styles.readText}>
                        {item.text || "本章暂无正文。"}
                      </div>
                    ) : (
                      <textarea
                        aria-label={`${item.title}正文`}
                        value={item.text}
                        placeholder="在这里编辑章节正文…"
                        onChange={(event) => {
                          const next = event.target.value;
                          save({
                            ...draft,
                            revision: draft.revision + 1,
                            chapters: draft.chapters.map((entry) =>
                              entry.id === item.id
                                ? { ...entry, text: next }
                                : entry,
                            ),
                          });
                          setSelection("");
                          if (scope === "selection") setScope("chapter");
                        }}
                        onSelect={(event) => {
                          const target = event.currentTarget;
                          if (target.selectionEnd > target.selectionStart) {
                            if (chapter?.id !== item.id) selectChapter(item.id);
                            setSelection(
                              target.value.slice(
                                target.selectionStart,
                                target.selectionEnd,
                              ),
                            );
                            setScope("selection");
                          }
                        }}
                        style={{
                          minHeight: `${Math.max(340, item.text.split("\n").length * 34 + Math.ceil(item.text.length / 28) * 18)}px`,
                        }}
                      />
                    )}
                  </article>
                ))}
              </div>
            )}
            <footer className={styles.editorFooter}>
              <span>{original ? "初稿保持原样" : "编辑稿独立于卷宗"}</span>
              <span>
                {chapter
                  ? `${wordCount(shownChapter?.text ?? "").toLocaleString()} 字 · 当前章`
                  : "0 字"}
              </span>
            </footer>
          </section>
        </div>
      </div>
      {compilerOpen && compileScope ? (
        <NovelCompilerPanel scope={compileScope} title={seed.caseMeta.title} hasDraft={Boolean(draft)}
          onClose={() => setCompilerOpen(false)}
          onLoad={(manuscript) => replaceDraft(createNovelDraft(manuscript))} />
      ) : null}
      {importOpen ? (
        <NovelImportDialog
          hasDraft={Boolean(draft)}
          errorMessage={error}
          onClose={() => setImportOpen(false)}
          onImport={(text) =>
            replaceDraft(
              createNovelDraft(manuscriptFromText(text, seed.caseMeta.title)),
            )
          }
        />
      ) : null}
      {history ? (
        <NovelHistory
          drafts={history}
          errorMessage={error}
          onClose={() => setHistory(null)}
          onRestore={replaceDraft}
        />
      ) : null}
      {reviewMessage?.reply?.changes ? (
        <NovelReview
          changes={reviewMessage.reply.changes}
          chapters={draft?.chapters ?? []}
          onClose={() => setReview(null)}
          onApply={() => {
            if (!draft) return;
            const next = applyNovelChanges(
              draft,
              reviewMessage.reply?.changes ?? [],
              reviewMessage.revision,
            );
            if (!next) {
              setError(
                "正文已发生变化，这份建议不能直接采纳，请重新发起修改。",
              );
              setReview(null);
              return;
            }
            save(next);
            setMessages((items) =>
              items.map((item) =>
                item.id === reviewMessage.id
                  ? { ...item, applied: true }
                  : item,
              ),
            );
            setReview(null);
          }}
        />
      ) : null}
    </main>
  );
}
