"use client";

import { useCallback, useEffect, useState } from "react";

import {
  archiveProject,
  clearArchivedProjects,
  listProjects,
  unarchiveProject,
  type BriefIntakeView,
  type ProjectView,
} from "@/lib/api-client";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";
import { fetchCaseIntake } from "@/features/case-session/case-session-api";

import styles from "./case-history-drawer.module.css";

/** 一份可调出的历史卷宗：项目元数据 + 建案进度推导。 */
export interface CaseHistoryEntry extends ProjectView {
  progress: number;
  stageLabel: string;
  frozen: boolean;
  adopted: boolean;
  touchedLabel: string;
}

const STAGE_LABELS: Record<string, [number, string]> = {
  idea: [1, "记录信号"],
  questions: [2, "验证方向"],
  confirmation: [3, "冻结简报"],
  brief_review: [4, "简报审阅"],
};

function formatTouched(iso: string | null): string {
  if (!iso) return "尚未开始";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "尚未开始";
  const minutes = Math.floor((Date.now() - then) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  const months = Math.floor(days / 30);
  return `${months} 个月前`;
}

function toEntry(project: ProjectView, intake: BriefIntakeView | null): CaseHistoryEntry {
  if (!intake) {
    return {
      ...project,
      progress: project.status === "archived" ? 0 : 1,
      stageLabel: "记录信号",
      frozen: false,
      adopted: false,
      touchedLabel: formatTouched(project.updated_at),
    };
  }
  const [progress, stageLabel] = STAGE_LABELS[intake.stage] ?? [1, "记录信号"];
  const frozen = intake.brief.current_version_id !== null;
  const adopted = intake.adopted_candidate_id !== null;
  return {
    ...project,
    progress: adopted ? 5 : frozen ? 4 : progress,
    stageLabel: adopted ? "已采用工作稿" : frozen ? "候选稿已冻结" : stageLabel,
    frozen,
    adopted,
    touchedLabel: formatTouched(intake.updated_at),
  };
}

export function CaseHistoryDrawer({
  open,
  onClose,
  currentProjectId,
  onRestore,
  onNotice,
}: {
  open: boolean;
  onClose: () => void;
  currentProjectId: number | null;
  onRestore: (projectId: number) => Promise<void>;
  onNotice: (message: string) => void;
}) {
  const [entries, setEntries] = useState<CaseHistoryEntry[] | null>(null);
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<CaseHistoryEntry[]> => {
    const projects = await listProjects(LOCAL_ACTOR_ID);
    return Promise.all(
      projects.map(async (project) => {
        try {
          return toEntry(project, await fetchCaseIntake(project.id));
        } catch {
          return toEntry(project, null);
        }
      }),
    );
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await refresh();
        if (!cancelled) setEntries(loaded);
      } catch (caught) {
        if (!cancelled) {
          setEntries(null);
          setError(caught instanceof Error ? caught.message : "档案读取失败。");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, refresh]);

  async function handleRetry() {
    setEntries(null);
    setError(null);
    try {
      setEntries(await refresh());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "档案读取失败。");
    }
  }

  const activeEntries = entries?.filter((entry) => entry.status !== "archived") ?? [];
  const archivedEntries = entries?.filter((entry) => entry.status === "archived") ?? [];

  async function handleRestore(projectId: number) {
    setPendingId(projectId);
    try {
      await onRestore(projectId);
      onNotice("已调出卷宗，可以继续建案。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "调卷失败。");
    } finally {
      setPendingId(null);
    }
  }

  async function handleArchive(projectId: number) {
    setBusyId(projectId);
    setError(null);
    try {
      const updated = await archiveProject(LOCAL_ACTOR_ID, projectId);
      setEntries((current) =>
        current?.map((entry) =>
          entry.id === projectId
            ? { ...entry, status: updated.status, archived_at: updated.archived_at }
            : entry,
        ) ?? null,
      );
      onNotice("卷宗已封存归档。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "归档失败。");
    } finally {
      setBusyId(null);
    }
  }

  async function handleUnarchive(projectId: number) {
    setBusyId(projectId);
    setError(null);
    try {
      const updated = await unarchiveProject(LOCAL_ACTOR_ID, projectId);
      setEntries((current) =>
        current?.map((entry) =>
          entry.id === projectId
            ? { ...entry, status: updated.status, archived_at: updated.archived_at }
            : entry,
        ) ?? null,
      );
      onNotice("卷宗已移出归档，可以继续调出。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "移出归档失败。");
    } finally {
      setBusyId(null);
    }
  }

  function requestClearArchived() {
    setConfirmClear(true);
    setError(null);
  }

  async function handleClearArchived() {
    setClearing(true);
    setError(null);
    try {
      const result = await clearArchivedProjects(LOCAL_ACTOR_ID);
      setConfirmClear(false);
      setEntries(await refresh());
      onNotice(`已清空 ${result.cleared} 份封存卷宗。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "清空归档失败。");
    } finally {
      setClearing(false);
    }
  }

  return (
    <div
      aria-hidden={!open}
      className={styles.drawerLayer}
      data-open={open}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) onClose();
      }}
    >
      <button
        aria-label="关闭建案历史"
        className={styles.scrim}
        onClick={onClose}
        tabIndex={open ? 0 : -1}
        type="button"
      />
      <aside
        aria-label="建案历史档案"
        aria-hidden={!open}
        className={styles.drawer}
        data-open={open}
        role="dialog"
      >
        <header className={styles.drawerHeader}>
          <div>
            <span>ARCHIVE / 档案柜</span>
            <h2>建案历史</h2>
          </div>
          <button aria-label="关闭" onClick={onClose} type="button">
            ✕
          </button>
        </header>

        {error ? (
          <p className={styles.drawerError} role="alert">
            {error}
            <button onClick={() => void handleRetry()} type="button">
              重试
            </button>
          </p>
        ) : null}

        {entries === null ? (
          <p aria-live="polite" className={styles.drawerLoading}>
            正在翻阅档案柜…
          </p>
        ) : (
          <div className={styles.drawerBody}>
            <section className={styles.traySection}>
              <header className={styles.trayHeader}>
                <span>进行中</span>
                <b>{activeEntries.length}</b>
              </header>
              {activeEntries.length === 0 ? (
                <p className={styles.emptyTray}>
                  档案柜还是空的。写下最初想法，第一份卷宗会在这里落成。
                </p>
              ) : (
                <div className={styles.trayList}>
                  {activeEntries.map((entry) => (
                    <CaseCard
                      busy={busyId === entry.id}
                      current={entry.id === currentProjectId}
                      entry={entry}
                      key={entry.id}
                      onArchive={() => void handleArchive(entry.id)}
                      onRestore={() => void handleRestore(entry.id)}
                      pending={pendingId === entry.id}
                    />
                  ))}
                </div>
              )}
            </section>

            <section className={styles.traySection} data-archived>
              <div className={styles.archivedRow}>
                <button
                  aria-expanded={archivedOpen}
                  className={styles.archivedToggle}
                  onClick={() => setArchivedOpen((value) => !value)}
                  type="button"
                >
                  <span>
                    已归档
                    <b>{archivedEntries.length}</b>
                  </span>
                  <em>{archivedOpen ? "收起" : "展开"}</em>
                </button>
                {archivedEntries.length > 0 && !confirmClear ? (
                  <button
                    className={styles.clearArchiveBtn}
                    onClick={requestClearArchived}
                    type="button"
                  >
                    清空归档
                  </button>
                ) : null}
              </div>
              {confirmClear ? (
                <div className={styles.clearConfirm} role="alert">
                  <span>
                    将清空 {archivedEntries.length} 份封存卷宗，且不可恢复。
                  </span>
                  <div>
                    <button
                      className={styles.clearConfirmDo}
                      disabled={clearing}
                      onClick={() => void handleClearArchived()}
                      type="button"
                    >
                      {clearing ? "清空中…" : "确认清空"}
                    </button>
                    <button
                      disabled={clearing}
                      onClick={() => setConfirmClear(false)}
                      type="button"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : null}
              {archivedOpen ? (
                archivedEntries.length === 0 ? (
                  <p className={styles.emptyTray}>没有封存的卷宗。</p>
                ) : (
                  <div className={styles.trayList}>
                    {archivedEntries.map((entry) => (
                      <CaseCard
                        busy={busyId === entry.id}
                        current={false}
                        entry={entry}
                        key={entry.id}
                        onArchive={() => void handleUnarchive(entry.id)}
                        onRestore={() => void handleRestore(entry.id)}
                        pending={pendingId === entry.id}
                      />
                    ))}
                  </div>
                )
              ) : null}
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

function CaseCard({
  entry,
  current,
  pending,
  busy,
  onRestore,
  onArchive,
}: {
  entry: CaseHistoryEntry;
  current: boolean;
  pending: boolean;
  busy: boolean;
  onRestore: () => void;
  onArchive: () => void;
}) {
  const archived = entry.status === "archived";
  return (
    <article className={styles.caseCard} data-archived={archived} data-current={current}>
      <header>
        <span className={styles.caseNumber}>
          CF-{String(entry.id).padStart(4, "0")}
        </span>
        {current ? <em>当前卷宗</em> : null}
        {archived ? <em data-sealed>封存</em> : null}
      </header>
      <h3>{entry.title}</h3>
      <div className={styles.caseMeta}>
        <small>{entry.touchedLabel}</small>
        <small>{entry.stageLabel}</small>
      </div>
      <div aria-label={"建案进度 " + entry.progress + "/5"} className={styles.caseProgress}>
        {[1, 2, 3, 4, 5].map((step) => (
          <i data-on={step <= entry.progress} key={step} />
        ))}
      </div>
      <footer>
        {current ? (
          <span className={styles.currentHint}>正在调阅，关闭档案柜返回</span>
        ) : (
          <button
            className={styles.primaryAction}
            disabled={pending}
            onClick={onRestore}
            type="button"
          >
            {pending ? "调出中…" : "调出此卷"}
          </button>
        )}
        {!current ? (
          <button
            className={styles.secondaryAction}
            disabled={busy}
            onClick={onArchive}
            type="button"
          >
            {archived ? (busy ? "移出中…" : "移出归档") : busy ? "封存中…" : "归档"}
          </button>
        ) : null}
      </footer>
    </article>
  );
}
