"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { NovelEditorView } from "@casefile/contracts";
import { errorMessage } from "@/lib/api-client";
import type { NovelCompileScope } from "./novel-compiler-api";
import type { NovelDraft } from "./novel-document";
import { novelEditorApi as api } from "./novel-editor-api";
function nonEmptyChapters(
  chapters: NovelDraft["chapters"],
): [NovelDraft["chapters"][number], ...NovelDraft["chapters"]] {
  if (!chapters[0]) throw new Error("小说没有章节。");
  return [chapters[0], ...chapters.slice(1)];
}

export const draftContent = (draft: NovelDraft) =>
  JSON.stringify([draft.original.title, draft.chapters]);
const remoteContent = (view: NovelEditorView) =>
  JSON.stringify([view.title, view.chapters]);
export function remoteDraft(
  view: NovelEditorView,
  prior?: NovelDraft | null,
): NovelDraft {
  return {
    original: {
      id: view.source_key,
      title: view.title,
      sourceLabel: view.source_label,
      chapters: view.original_chapters,
    },
    chapters: view.chapters,
    revision: (prior?.revision ?? 0) + 1,
    selectedChapterId: prior?.selectedChapterId ?? view.chapters[0].id,
    remote: {
      id: view.id,
      revision: view.revision,
      syncedContent: remoteContent(view),
    },
  };
}

export function useNovelEditor(
  scope: NovelCompileScope | undefined,
  draft: NovelDraft | null,
  onDraft: (draft: NovelDraft) => void,
) {
  const [view, setView] = useState<NovelEditorView | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(Boolean(scope));
  const [loadAttempt, setLoadAttempt] = useState(0);
  const latest = useRef({ draft, onDraft });
  const remote = useRef<NovelEditorView | null>(null);
  const pending = useRef<Promise<NovelEditorView | null> | null>(null);
  useEffect(() => {
    latest.current = { draft, onDraft };
  }, [draft, onDraft]);
  const deliver = useCallback((next: NovelDraft) => {
    const callback = latest.current.onDraft;
    latest.current = { ...latest.current, draft: next };
    callback(next);
  }, []);
  const project = scope?.projectId,
    draftId = scope?.draftId,
    source = draft?.original.id;

  useEffect(() => {
    if (!project || !draftId) return;
    let disposed = false;
    remote.current = null;
    async function load() {
      setLoading(true);
      setError("");
      setView(null);
      try {
        const local = latest.current.draft;
        let next: NovelEditorView | null = null;
        if (local) {
          next = local.remote
            ? await api.get(project!, local.remote.id)
            : await api.create(project!, {
                draft_id: draftId!,
                source_key: local.original.id,
                source_label: local.original.sourceLabel,
                title: local.original.title,
                chapters: nonEmptyChapters(local.chapters),
                original_chapters: nonEmptyChapters(local.original.chapters),
              });
        } else {
          const list = await api.list(project!, draftId!);
          if (list[0]) next = await api.get(project!, list[0].id);
        }
        if (disposed || !next || latest.current.draft?.original.id !== source)
          return;
        remote.current = next;
        setView(next);
        const current = latest.current.draft;
        if (
          !current ||
          (current.remote &&
            draftContent(current) === current.remote.syncedContent)
        ) {
          deliver(remoteDraft(next, current));
        } else if (draftContent(current) === remoteContent(next)) {
          deliver({
            ...current,
            remote: {
              id: next.id,
              revision: next.revision,
              syncedContent: remoteContent(next),
            },
          });
        } else if (current.remote?.revision === next.revision) {
          setError("已恢复本地未同步编辑，请点击重试保存。");
        } else {
          setError(
            "本地稿与服务器版本不同。本地内容已保留，请先导出备份，再载入服务器稿。",
          );
        }
      } catch (cause) {
        if (!disposed) setError(errorMessage(cause));
      } finally {
        if (!disposed) setLoading(false);
      }
    }
    void load();
    return () => {
      disposed = true;
    };
  }, [project, draftId, source, deliver, loadAttempt]);

  const flush = useCallback(async (): Promise<NovelEditorView | null> => {
    if (pending.current) {
      await pending.current;
    }
    const local = latest.current.draft,
      base = remote.current;
    if (!project || !local || !base || base.source_key !== local.original.id)
      throw new Error("正文尚未同步，请稍后重试。");
    if (draftContent(local) === remoteContent(base)) return base;
    if (local.remote?.revision !== base.revision)
      throw new Error("正文版本冲突，请先导出本地稿并核对服务器版本。");
    const content = draftContent(local);
    setSaving(true);
    const work = api
      .save(project, base.id, {
        expected_revision: base.revision,
        title: local.original.title,
        chapters: nonEmptyChapters(local.chapters),
      })
      .then((next) => {
        if (latest.current.draft?.original.id !== local.original.id)
          return null;
        remote.current = next;
        setView(next);
        setError("");
        const current = latest.current.draft;
        deliver({
          ...current,
          remote: {
            id: next.id,
            revision: next.revision,
            syncedContent: content,
          },
        });
        return next;
      })
      .catch((cause) => {
        setError(errorMessage(cause));
        throw cause;
      })
      .finally(() => {
        pending.current = null;
        setSaving(false);
      });
    pending.current = work;
    return work;
  }, [project, deliver]);

  useEffect(() => {
    if (
      !scope ||
      !draft?.remote ||
      !view ||
      error ||
      loading ||
      draftContent(draft) === remoteContent(view)
    )
      return;
    const timer = setTimeout(() => {
      void flush().catch(() => {});
    }, 1000);
    return () => clearTimeout(timer);
  }, [scope, draft, view, error, loading, flush]);

  const refresh = useCallback(async () => {
    const base = remote.current;
    if (!project || !base) return;
    const next = await api.get(project, base.id);
    if (latest.current.draft?.original.id !== next.source_key) return;
    // Poll collaboration state without replacing unsaved editor text.
    if (next.revision === base.revision) {
      remote.current = next;
      setView(next);
    } else setError("服务器正文已更新，请先保留本地内容，再载入服务器稿。");
  }, [project]);

  const acceptView = useCallback(
    (next: NovelEditorView, expectedContent?: string) => {
      if (latest.current.draft?.original.id !== next.source_key) return;
      remote.current = next;
      setView(next);
      setError("");
      if (
        expectedContent &&
        draftContent(latest.current.draft) !== expectedContent
      ) {
        setError(
          "操作期间有新的本地编辑，已保留。请导出备份后核对服务器版本。",
        );
        return;
      }
      deliver(remoteDraft(next, latest.current.draft));
    },
    [deliver],
  );
  const reload = useCallback(async () => {
    if (project && remote.current)
      acceptView(await api.get(project, remote.current.id));
  }, [project, acceptView]);
  const decide = useCallback(
    async (exchange: number, edits: number[], action: "accept" | "reject") => {
      const base = await flush();
      if (!project || !base || !latest.current.draft) return;
      const content = draftContent(latest.current.draft);
      acceptView(
        await api.decide(
          project,
          base.id,
          exchange,
          base.revision,
          edits,
          action,
        ),
        content,
      );
    },
    [project, flush, acceptView],
  );
  const restore = useCallback(
    async (revision: number) => {
      const base = await flush();
      if (!project || !base || !latest.current.draft) return;
      const content = draftContent(latest.current.draft);
      acceptView(
        await api.restore(project, base.id, base.revision, revision),
        content,
      );
    },
    [project, flush, acceptView],
  );
  const checkpoint = useCallback(async () => {
    const base = await flush();
    if (!project || !base || !latest.current.draft) throw new Error("稿件尚未就绪。");
    const content = draftContent(latest.current.draft);
    acceptView(await api.checkpoint(project, base.id, {
      expected_revision: base.revision, title: base.title, chapters: base.chapters,
    }), content);
  }, [project, flush, acceptView]);
  const switchNovel = useCallback(
    async (id: number) => {
      if (!project) return;
      if (latest.current.draft) await flush();
      deliver(remoteDraft(await api.get(project, id)));
    },
    [project, flush, deliver],
  );
  const retry = useCallback(async () => {
    if (!remote.current) {
      setLoadAttempt((n) => n + 1);
      return;
    }
    await flush();
  }, [flush]);
  return {
    view,
    loading,
    saving,
    error,
    flush,
    refresh,
    acceptView,
    reload,
    decide,
    restore,
    checkpoint,
    switchNovel,
    retry,
  };
}
