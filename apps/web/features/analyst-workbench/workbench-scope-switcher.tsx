"use client";

import Link from "next/link";
import {
  type RefObject,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  activateDraft,
  ApiError,
  errorMessage,
  listDrafts,
  listProjects,
  type DraftSummaryView,
  type DraftView,
  type ProjectView,
} from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";

import styles from "./workbench-scope-switcher.module.css";

function useDismissibleMenu(
  open: boolean,
  containerRef: RefObject<HTMLDivElement | null>,
  triggerRef: RefObject<HTMLButtonElement | null>,
  close: () => void,
) {
  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) close();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      close();
      triggerRef.current?.focus();
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [close, containerRef, open, triggerRef]);
}

export function ProjectSwitcher({
  currentProjectId,
  fallbackTitle,
  onBeforeSwitch,
}: {
  currentProjectId: number | null;
  fallbackTitle: string;
  onBeforeSwitch?: (project: ProjectView) => boolean;
}) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<ProjectView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismissibleMenu(open, containerRef, triggerRef, close);

  const load = useCallback(async () => {
    setError(null);
    try {
      setProjects(await listProjects(LOCAL_ACTOR_ID));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => {
    if (currentProjectId === null) return;
    let active = true;
    void listProjects(LOCAL_ACTOR_ID)
      .then((items) => {
        if (active) {
          setProjects(items);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      });
    return () => {
      active = false;
    };
  }, [currentProjectId]);

  const current = projects?.find((project) => project.id === currentProjectId);
  const switchable = projects?.filter(
    (project) => project.status !== "archived" || project.id === currentProjectId,
  );

  return (
    <div className={styles.projectScope} ref={containerRef}>
      <button
        aria-controls="workbench-project-menu"
        aria-expanded={open}
        aria-haspopup="menu"
        className={styles.scopeTrigger}
        data-kind="project"
        onClick={() => setOpen((value) => !value)}
        ref={triggerRef}
        type="button"
      >
        <span>项目</span>
        <strong>{current?.title ?? fallbackTitle}</strong>
        <i aria-hidden="true">▾</i>
      </button>
      {open ? (
        <div
          aria-label="切换项目"
          className={styles.scopeMenu}
          data-kind="project"
          id="workbench-project-menu"
          role="menu"
        >
          <header>
            <span>项目</span>
            <small>切换后打开该项目的 Current Draft</small>
          </header>
          {projects === null && error === null ? (
            <div className={styles.menuState} role="status">正在读取项目列表…</div>
          ) : error ? (
            <div className={styles.menuState}>
              <strong>项目列表读取失败</strong>
              <small>{error}</small>
              <button onClick={() => void load()} type="button">重新读取</button>
            </div>
          ) : switchable?.length ? (
            switchable.map((project) => (
              <Link
                aria-current={project.id === currentProjectId ? "page" : undefined}
                className={styles.menuItem}
                data-current={project.id === currentProjectId}
                href={`/workbench?project=${project.id}`}
                key={project.id}
                onClick={(event) => {
                  if (project.id === currentProjectId) {
                    event.preventDefault();
                    close();
                    window.setTimeout(() => triggerRef.current?.focus(), 0);
                    return;
                  }
                  if (onBeforeSwitch && !onBeforeSwitch(project)) {
                    event.preventDefault();
                    close();
                    window.setTimeout(() => triggerRef.current?.focus(), 0);
                    return;
                  }
                  close();
                }}
                role="menuitem"
              >
                <span>
                  <strong>{project.title}</strong>
                  <small>
                    项目 #{project.id} · 当前工作稿 #{project.current_draft_id}
                  </small>
                </span>
                {project.id === currentProjectId ? <b>当前</b> : null}
              </Link>
            ))
          ) : (
            <div className={styles.menuState}>暂无可切换的项目。</div>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function DraftSwitcher({
  projectId,
  currentDraft,
  onActivated,
  onBeforeSwitch,
  onCurrentDraftChanged,
}: {
  projectId: number;
  currentDraft: DraftView;
  onActivated: (draft: DraftView) => Promise<void> | void;
  onBeforeSwitch?: (draft: DraftSummaryView) => boolean;
  onCurrentDraftChanged?: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<DraftSummaryView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [activatingId, setActivatingId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismissibleMenu(open, containerRef, triggerRef, close);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDrafts(await listDrafts(LOCAL_ACTOR_ID, projectId));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const orderedDrafts = [...(drafts ?? [])]
    .filter((draft) => draft.has_content)
    .sort((left, right) => {
      if (left.draft_id === currentDraft.draft_id) return -1;
      if (right.draft_id === currentDraft.draft_id) return 1;
      return Date.parse(right.updated_at) - Date.parse(left.updated_at);
    });

  async function selectDraft(draft: DraftSummaryView) {
    if (draft.draft_id === currentDraft.draft_id) {
      close();
      triggerRef.current?.focus();
      return;
    }
    if (onBeforeSwitch && !onBeforeSwitch(draft)) {
      close();
      window.setTimeout(() => triggerRef.current?.focus(), 0);
      return;
    }
    setActivatingId(draft.draft_id);
    setError(null);
    try {
      const activated = await activateDraft(
        LOCAL_ACTOR_ID,
        projectId,
        draft.draft_id,
        currentDraft.draft_id,
      );
      await onActivated(activated);
      setDrafts((items) =>
        items?.map((item) => ({
          ...item,
          is_current: item.draft_id === activated.draft_id,
        })) ?? null,
      );
      close();
      window.setTimeout(() => triggerRef.current?.focus(), 0);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.body.code === "current_draft_changed" &&
        onCurrentDraftChanged
      ) {
        try {
          await onCurrentDraftChanged();
          return;
        } catch {
          setError(
            "当前工作稿已切换，但最新内容读取失败。请刷新页面后重试。",
          );
          return;
        }
      }
      setError(errorMessage(caught));
    } finally {
      setActivatingId(null);
    }
  }

  return (
    <div className={styles.draftScope} ref={containerRef}>
      <button
        aria-controls="workbench-draft-menu"
        aria-expanded={open}
        aria-haspopup="menu"
        className={styles.scopeTrigger}
        data-kind="draft"
        disabled={activatingId !== null}
        onClick={() => {
          const nextOpen = !open;
          setOpen(nextOpen);
          if (nextOpen && drafts === null && error === null && !loading) {
            void load();
          }
        }}
        ref={triggerRef}
        type="button"
      >
        <span>{activatingId === null ? "当前工作稿" : "正在切换"}</span>
        <strong>{currentDraft.title}</strong>
        <i aria-hidden="true">▾</i>
      </button>
      {open ? (
        <div
          aria-label="切换工作稿"
          className={styles.scopeMenu}
          data-kind="draft"
          id="workbench-draft-menu"
          role="menu"
        >
          <header>
            <span>工作稿</span>
            <small>选择后立即设为服务端 Current Draft</small>
          </header>
          {drafts === null && error === null ? (
            <div className={styles.menuState} role="status">正在读取工作稿…</div>
          ) : error ? (
            <div className={styles.menuState} role="alert">
              <strong>工作稿操作未完成</strong>
              <small>{error}</small>
              <button onClick={() => void load()} type="button">重新读取</button>
            </div>
          ) : orderedDrafts.length ? (
            orderedDrafts.map((draft) => {
              const locked = draft.status !== "active";
              return (
                <button
                  aria-current={draft.draft_id === currentDraft.draft_id ? "page" : undefined}
                  className={styles.menuItem}
                  data-current={draft.draft_id === currentDraft.draft_id}
                  data-locked={locked}
                  disabled={activatingId !== null || locked}
                  key={draft.draft_id}
                  onClick={() => void selectDraft(draft)}
                  role="menuitem"
                  type="button"
                >
                  <span>
                    <strong>{draft.title}</strong>
                    <small>
                      工作稿 #{draft.draft_id} · Brief V{draft.brief_version_no ?? "—"} · R
                      {draft.revision}
                    </small>
                  </span>
                  {locked ? (
                    <b>
                      {draft.draft_id === currentDraft.draft_id
                        ? "当前 · 已锁定"
                        : "已锁定"}
                    </b>
                  ) : activatingId === draft.draft_id ? (
                    <b>切换中…</b>
                  ) : draft.draft_id === currentDraft.draft_id ? (
                    <b>当前</b>
                  ) : null}
                </button>
              );
            })
          ) : (
            <div className={styles.menuState}>
              <strong>还没有已生成的工作稿</strong>
              <small>返回建案中心选择策略并采用一份候选。</small>
            </div>
          )}
          <Link
            className={styles.createDraftLink}
            href={`/?project=${projectId}`}
            onClick={close}
            role="menuitem"
          >
            <span>＋</span>
            <strong>生成新工作稿</strong>
          </Link>
        </div>
      ) : null}
    </div>
  );
}
