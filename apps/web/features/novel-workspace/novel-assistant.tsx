"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  NovelEditorAnchor,
  NovelEditorExchange,
} from "@casefile/contracts";
import { errorMessage } from "@/lib/api-client";
import { novelEditorApi as api } from "./novel-editor-api";
import type { useNovelEditor } from "./use-novel-editor";
import styles from "./novel-collaboration.module.css";
import desk from "./novel-workspace.module.css";
import { WorkbenchIcon as Icon } from "@/features/analyst-workbench/workbench-icon";

export type EditorMode = "discuss" | "rewrite" | "polish";
export type EditorSelection = NovelEditorAnchor & {
  localRevision: number;
  manuscriptKey: string;
};
type Editor = ReturnType<typeof useNovelEditor>;
export function NovelAssistant({
  project,
  editor,
  chapterId,
  mode,
  onMode,
  anchor,
  onAnchor,
  onReview,
  onLocate,
  localRevision,
  original = false,
  onChapter,
}: {
  project: number;
  editor: Editor;
  chapterId?: string;
  mode: EditorMode;
  onMode: (mode: EditorMode) => void;
  anchor: EditorSelection | null;
  onAnchor: (anchor: EditorSelection | null) => void;
  onReview: (id: number, edit?: number) => void;
  onLocate: (anchor: NovelEditorAnchor) => void;
  localRevision?: number;
  original?: boolean;
  onChapter?: (id: string) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [activity, setActivity] = useState<{task: number; text: string} | null>(null);
  const [streamed, setStreamed] = useState<{
    task: number;
    text: string;
  } | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const lock = useRef(false);
  const requestKey = useRef<{ text: string; key: string } | null>(null);
  const active = editor.view?.exchanges.find((e) =>
    ["queued", "running", "cancelling"].includes(e.status),
  );
  const refresh = editor.refresh;
  const activeTaskId = active?.task_id;
  useEffect(() => {
    if (!activeTaskId) return;
    const timer = setInterval(() => {
      void refresh().catch(() => {});
    }, 2500);
    return () => clearInterval(timer);
  }, [refresh, activeTaskId]);
  useEffect(() => {
    if (anchor) composer.current?.focus();
  }, [anchor, mode]);
  useEffect(() => {
    if (!activeTaskId) return;
    const controller = new AbortController();
    void api
      .stream(
        project,
        activeTaskId,
        (text) =>
          setStreamed((s) => ({
            task: activeTaskId,
            text: (s?.task === activeTaskId ? s.text : "") + text,
          })),
        controller.signal,
        (text) => setActivity({task: activeTaskId, text}),
      )
      .catch(() => {});
    return () => controller.abort();
  }, [project, activeTaskId]); // Task events replay after refresh; no model request is repeated.

  async function send(reply?: NovelEditorExchange) {
    const text = reply ? `请按以下建议改写：\n${reply.message}` : instruction.trim();
    const targetChapterId = reply?.chapter_id ?? chapterId;
    const requestMode = reply ? "rewrite" : mode;
    if (lock.current || active || editor.loading || editor.error || !text || !targetChapterId) return;
    lock.current = true;
    setSending(true);
    setError("");
    try {
      if (!reply && anchor && anchor.localRevision !== localRevision && !anchor.original)
        throw new Error("引用后正文已变化，请重新划选。");
      if ((original || (reply ? reply.anchor?.original : anchor?.original)) && requestMode !== "discuss")
        throw new Error("原始稿仅支持讨论，请切换到编辑稿后修改。");
      const view = await editor.flush();
      if (!view) return;
      const originalChapter = view.original_chapters.find(
        (c) => c.id === targetChapterId,
      );
      const target =
        (reply ? reply.anchor : anchor) ??
        (original && originalChapter
          ? {
              chapter_id: targetChapterId,
              start: 0,
              end: Array.from(originalChapter.text).length,
              text: originalChapter.text,
              original: true,
            }
          : null);
      const request = {
        expected_revision: view.revision,
        mode: requestMode,
        scope: target ? ("selection" as const) : ("chapter" as const),
        chapter_id: target?.chapter_id ?? targetChapterId,
        instruction: text,
        anchor: target
          ? {
              chapter_id: target.chapter_id,
              start: target.start,
              end: target.end,
              text: target.text,
              original: target.original,
            }
          : null,
      };
      const fingerprint = JSON.stringify(request);
      if (requestKey.current?.text !== fingerprint)
        requestKey.current = { text: fingerprint, key: crypto.randomUUID() };
      await api.submit(project, view.id, {
        ...request,
        request_key: requestKey.current.key,
      });
      requestKey.current = null;
      if (reply) {
        onMode("rewrite");
        onChapter?.(reply.chapter_id);
      } else {
        setInstruction("");
      }
      await refresh();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      lock.current = false;
      setSending(false);
    }
  }
  return (
    <div className={styles.deskAssistant}>
      <header className={desk.conversationHeader}>
        <div className={desk.modeTabs} aria-label="协作模式">
          {(
            [
              ["discuss", "讨论"],
              ["rewrite", "改写"],
              ["polish", "润色"],
            ] as const
          ).map(([id, label]) => (
            <button
              type="button"
              key={id}
              aria-pressed={mode === id}
              disabled={original && id !== "discuss"}
              onClick={() => onMode(id)}
            >
              <Icon
                name={
                  id === "discuss"
                    ? "chat"
                    : id === "rewrite"
                      ? "document"
                      : "lightbulb"
                }
              />
              {label}
            </button>
          ))}
        </div>
        <span className={desk.companionLabel}>创作搭档</span>
      </header>
      <div className={desk.modeNote}>
        <Icon name="lightbulb" />
        <span>
          {mode === "discuss"
            ? "一起推敲情节、人物和伏笔，保留你的创作判断。"
            : mode === "rewrite"
              ? "提出改写方向，查看修改建议后再决定是否采纳。"
              : "打磨语言与节奏，让人物说出属于自己的话。"}
        </span>
      </div>
      <div className={desk.messages} aria-live="polite">
        {!editor.view?.exchanges.length ? (
          <>
            <article className={desk.welcome}>
              <span className={desk.eyebrow}>从初稿，到你的作品</span>
              <h1>{"故事已经在纸上，\n接下来，一起打磨。"}</h1>
              <p>
                右侧是你的小说。可以直接修改正文，也可以选中一段，与 AI
                讨论它的下一种写法。
              </p>
            </article>
            <div className={desk.prompts}>
              <span>从一个具体的修改开始</span>
              {[
                "检查当前章节的伏笔与回收是否呼应",
                "让当前章节的节奏更紧凑",
                "调整人物对白，保留原有事实",
              ].map((text) => (
                <button
                  key={text}
                  type="button"
                  onClick={() => {
                    setInstruction(text);
                    composer.current?.focus();
                  }}
                >
                  {text}
                  <Icon name="chevron-right" />
                </button>
              ))}
            </div>
          </>
        ) : null}
        {editor.view?.exchanges.map((reply) => (
          <article className={styles.exchange} key={reply.id}>
            <div className={desk.message} data-role="user">
              <small>
                你 ·{" "}
                {reply.mode === "discuss"
                  ? "讨论"
                  : reply.mode === "rewrite"
                    ? "改写"
                    : "润色"}
              </small>
              {reply.anchor ? (
                <button
                  type="button"
                  className={styles.quote}
                  onClick={() => onLocate(reply.anchor!)}
                >
                  {reply.anchor.text}
                </button>
              ) : null}
              <p>{reply.instruction}</p>
            </div>
            <div className={desk.message} data-role="assistant">
              <small>创作搭档</small>
              {active?.id === reply.id ? <div className={styles.activity} role="status">{reply.status === "cancelling" ? "正在停止…" : activity?.task === reply.task_id ? activity.text : reply.status === "queued" ? "等待处理…" : "正在准备上下文…"}</div> : null}
              <div className={styles.markdown}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{img: () => null}}>
                {reply.message ||
                  (active?.id === reply.id
                    ? (streamed?.task === reply.task_id ? streamed.text : "") ||
                      "正在准备…"
                    : reply.status === "cancelled"
                      ? "已停止，本次未修改正文。"
                      : reply.status === "failed"
                        ? "本次协作失败，正文未修改。可检查设置后重新发送。"
                        : "等待结果")}
              </ReactMarkdown>
              </div>
              {reply.status === "succeeded" && reply.mode === "discuss" ? (
                <button type="button"
                  disabled={sending || !!active || editor.loading || !!editor.error || original || !!reply.anchor?.original}
                  onClick={() => void send(reply)}>
                  按建议改写
                </button>
              ) : null}
              {reply.edits.map((edit, index) => (
                <button
                  type="button"
                  className={styles.reason}
                  key={edit.id}
                  onClick={() => onReview(reply.id, edit.id)}
                >
                  <strong>修改 {index + 1}</strong> {edit.reason}
                  <small>
                    {edit.status === "accepted"
                      ? "已采纳"
                      : edit.status === "rejected"
                        ? "已拒绝"
                        : "查看差异"}
                  </small>
                </button>
              ))}
              {reply.status === "succeeded" &&
              reply.mode !== "discuss" &&
              !reply.edits.length ? (
                <small>没有正文修改</small>
              ) : null}
              {reply.usage.total_tokens ? (
                <small>
                  本次 {reply.usage.total_tokens.toLocaleString()} tokens ·{" "}
                  {reply.usage.requests ?? 1} 次调用
                </small>
              ) : null}
            </div>
          </article>
        ))}
      </div>
      <form
        className={desk.composerArea}
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {anchor ? (
          <div className={styles.reference}>
            <button type="button" onClick={() => onLocate(anchor)}>
              {anchor.original ? "原始稿引用" : "已引用选段"} ·{" "}
              {Array.from(anchor.text).length} 字<q>{anchor.text}</q>
            </button>
            <button
              type="button"
              aria-label="取消正文引用"
              onClick={() => onAnchor(null)}
            >
              ×
            </button>
          </div>
        ) : (
          <div className={desk.scopeRow}>
            <label>
              修改范围
              <select aria-label="修改范围" value="chapter" onChange={() => {}}>
                <option value="chapter">当前章节</option>
              </select>
            </label>
          </div>
        )}
        {error || editor.error ? (
          <p role="alert">{error || editor.error}</p>
        ) : null}
        {editor.error ? (
          <div>
            <button
              type="button"
              onClick={() =>
                void editor
                  .retry()
                  .then(() => setError(""))
                  .catch((e) => setError(errorMessage(e)))
              }
            >
              重试保存
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  window.confirm(
                    "请先导出未同步的本地修改。载入服务器稿将替换当前编辑显示，确认继续？",
                  )
                )
                  void editor.reload().catch((e) => setError(errorMessage(e)));
              }}
            >
              载入服务器稿
            </button>
          </div>
        ) : null}
        <div className={desk.composer}>
          <textarea
            ref={composer}
            aria-label="小说修改指令"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing &&
                event.nativeEvent.keyCode !== 229
              ) {
                event.preventDefault();
                if (!sending && !editor.loading && !editor.error && editor.view)
                  void send();
              }
            }}
            maxLength={6000}
            placeholder={
              mode === "discuss"
                ? "说说你想怎么修改这篇小说…"
                : "描述希望调整的地方…"
            }
          />
          {active ? (
            <button
              type="button"
              aria-label={active.status === "cancelling" ? "正在停止" : "停止生成"}
              title={active.status === "cancelling" ? "正在停止" : "停止生成"}
              disabled={active.status === "cancelling"}
              onClick={() =>
                void api
                  .cancel(project, active.task_id)
                  .then(refresh)
                  .catch((e) => setError(errorMessage(e)))
              }
            >
              <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button
              type="submit"
              aria-label="发送"
              title={sending ? "正在提交…" : "发送小说修改指令"}
              disabled={
                sending ||
                editor.loading ||
                !!editor.error ||
                !editor.view ||
                !instruction.trim()
              }
            >
              <Icon name="send" />
            </button>
          )}
        </div>
        <span className={desk.composerHint}>
          修改建议经你采纳后才会写入正文
        </span>
        <span className={desk.composerHint}>
          {editor.loading
            ? "正在恢复稿件…"
            : editor.saving
              ? "正在保存正文…"
              : editor.error
                ? "本地编辑已保留"
                : ""}
        </span>
      </form>
    </div>
  );
}
