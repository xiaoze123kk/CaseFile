"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchExposurePlan,
  putExposurePlan,
} from "@/features/case-session/case-session-api";
import {
  ApiError,
  type ExposurePlanEntryView,
  type ExposurePlanView,
} from "@/lib/api-client";

import type { TimelineDisplayEvent } from "./timeline-lanes";
import styles from "./exposure-plan.module.css";

type WorkingEntry = Omit<ExposurePlanEntryView, "sequence_no">;
type MoveDirection = -1 | 1;

function eventEntry(event: TimelineDisplayEvent): WorkingEntry {
  return {
    entry_key: `exposure_${event.id}`,
    title: event.label,
    note: null,
    refs: [{ object_type: "event", object_id: event.id }],
  };
}

function editableEntry(entry: ExposurePlanEntryView): WorkingEntry {
  return {
    entry_key: entry.entry_key,
    title: entry.title,
    note: entry.note,
    refs: entry.refs,
  };
}

export function workingExposureEntries(
  plan: ExposurePlanView,
  events: TimelineDisplayEvent[],
): WorkingEntry[] {
  if (plan.revision === 0 && plan.entries.length === 0) {
    return events.map(eventEntry);
  }
  return plan.entries.map(editableEntry);
}

export function moveExposureEntry(
  entries: WorkingEntry[],
  index: number,
  direction: MoveDirection,
) {
  const target = index + direction;
  if (index < 0 || index >= entries.length || target < 0 || target >= entries.length) {
    return entries;
  }
  const next = [...entries];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

function persistedEntries(plan: ExposurePlanView): WorkingEntry[] {
  return plan.entries.map(editableEntry);
}

function referencedEventId(entry: WorkingEntry) {
  return entry.refs.find((reference) => reference.object_type === "event")?.object_id ?? null;
}

export function ExposurePlanEditor({
  projectId,
  draftId,
  events,
  selectedEventId,
  editable,
  onSelectEvent,
}: {
  projectId: number;
  draftId: number;
  events: TimelineDisplayEvent[];
  selectedEventId: string | null;
  editable: boolean;
  onSelectEvent: (eventId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [plan, setPlan] = useState<ExposurePlanView | null>(null);
  const [workingEntries, setWorkingEntries] = useState<WorkingEntry[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "saving" | "error">(
    "loading",
  );
  const [message, setMessage] = useState<string | null>(null);

  const loadPlan = useCallback(async () => {
    setStatus("loading");
    setMessage(null);
    try {
      const loaded = await fetchExposurePlan(projectId);
      if (loaded.draft_id !== draftId) {
        throw new Error("当前工作稿已切换，请重新载入工作台。");
      }
      setPlan(loaded);
      setWorkingEntries(workingExposureEntries(loaded, events));
      setStatus("ready");
    } catch (caught) {
      setStatus("error");
      setMessage(caught instanceof Error ? caught.message : "披露计划读取失败。");
    }
  }, [draftId, events, projectId]);

  useEffect(() => {
    let cancelled = false;

    async function initializePlan() {
      try {
        const loaded = await fetchExposurePlan(projectId);
        if (cancelled) return;
        if (loaded.draft_id !== draftId) {
          throw new Error("当前工作稿已切换，请重新载入工作台。");
        }
        setPlan(loaded);
        setWorkingEntries(workingExposureEntries(loaded, events));
        setStatus("ready");
      } catch (caught) {
        if (cancelled) return;
        setStatus("error");
        setMessage(caught instanceof Error ? caught.message : "披露计划读取失败。");
      }
    }

    void initializePlan();
    return () => {
      cancelled = true;
    };
  }, [draftId, events, projectId]);

  const eventById = useMemo(
    () => new Map(events.map((event) => [event.id, event])),
    [events],
  );
  const plannedEventIds = useMemo(
    () => new Set(workingEntries.map(referencedEventId).filter(Boolean)),
    [workingEntries],
  );
  const unplannedEvents = events.filter((event) => !plannedEventIds.has(event.id));
  const dirty = Boolean(
    plan &&
      JSON.stringify(workingEntries) !== JSON.stringify(persistedEntries(plan)),
  );

  function resetWorkingEntries() {
    if (!plan) return;
    setWorkingEntries(workingExposureEntries(plan, events));
    setMessage(null);
  }

  function updateNote(index: number, note: string) {
    setWorkingEntries((current) =>
      current.map((entry, entryIndex) =>
        entryIndex === index ? { ...entry, note: note || null } : entry,
      ),
    );
  }

  async function save() {
    if (!plan || !editable) return;
    setStatus("saving");
    setMessage("正在保存独立披露顺序；事实时间不会改变。");
    try {
      const saved = await putExposurePlan(
        projectId,
        draftId,
        plan.revision,
        workingEntries,
      );
      setPlan(saved);
      setWorkingEntries(workingExposureEntries(saved, events));
      setStatus("ready");
      setMessage(`披露计划已保存为 R${saved.revision}；Draft revision 保持不变。`);
    } catch (caught) {
      const conflict =
        caught instanceof ApiError &&
        ["current_draft_changed", "exposure_plan_revision_conflict"].includes(
          caught.body.code,
        );
      if (conflict) {
        await loadPlan();
        setMessage("披露计划或当前工作稿已更新，已载入最新版。请重新排序。");
        return;
      }
      setStatus("error");
      setMessage(caught instanceof Error ? caught.message : "披露计划保存失败。");
    }
  }

  return (
    <>
      <button
        className={styles.trigger}
        onClick={() => setOpen(true)}
        type="button"
      >
        披露计划
        <span>{plan ? `R${plan.revision}` : "…"}</span>
      </button>
      {open ? (
        <section aria-label="编辑披露计划" className={styles.sheet} role="dialog">
          <header>
            <div>
              <span>EXPOSURE PLAN · DRAFT #{draftId}</span>
              <strong>线性披露顺序</strong>
            </div>
            <button onClick={() => setOpen(false)} type="button">
              关闭
            </button>
          </header>

          <div className={styles.boundaryNote}>
            <b>独立版本链</b>
            <p>这里只安排读者先看到什么；不会改写发生时间、当前工作稿或正式版本。</p>
          </div>

          {status === "loading" ? (
            <p className={styles.state}>正在读取这份 Draft 的披露计划…</p>
          ) : null}
          {status === "error" && !plan ? (
            <div className={styles.state} data-error="true">
              <p>{message}</p>
              <button onClick={() => void loadPlan()} type="button">
                重新读取
              </button>
            </div>
          ) : null}

          {plan ? (
            <>
              <div className={styles.planMeta}>
                <span>PLAN R{plan.revision}</span>
                <span>{workingEntries.length} 个披露节点</span>
                {dirty ? <b>未保存</b> : <i>已同步</i>}
              </div>
              <ol className={styles.entryList}>
                {workingEntries.map((entry, index) => {
                  const eventId = referencedEventId(entry);
                  const event = eventId ? eventById.get(eventId) : null;
                  return (
                    <li
                      data-selected={Boolean(eventId && eventId === selectedEventId)}
                      key={entry.entry_key}
                    >
                      <span className={styles.sequence}>{String(index + 1).padStart(2, "0")}</span>
                      <div className={styles.entryBody}>
                        <button
                          className={styles.entrySelect}
                          disabled={!eventId}
                          onClick={() => eventId && onSelectEvent(eventId)}
                          type="button"
                        >
                          <strong>{entry.title}</strong>
                          <small>
                            {event ? `事实 ${event.time}` : `${entry.refs.length} 个对象引用`}
                          </small>
                        </button>
                        <input
                          aria-label={`${entry.title}的披露说明`}
                          disabled={!editable}
                          maxLength={4000}
                          onChange={(changeEvent) =>
                            updateNote(index, changeEvent.target.value)
                          }
                          placeholder="披露说明（可空）"
                          value={entry.note ?? ""}
                        />
                      </div>
                      <div className={styles.moveActions}>
                        <button
                          aria-label={`上移 ${entry.title}`}
                          disabled={!editable || index === 0}
                          onClick={() =>
                            setWorkingEntries((current) =>
                              moveExposureEntry(current, index, -1),
                            )
                          }
                          type="button"
                        >
                          上移
                        </button>
                        <button
                          aria-label={`下移 ${entry.title}`}
                          disabled={!editable || index === workingEntries.length - 1}
                          onClick={() =>
                            setWorkingEntries((current) =>
                              moveExposureEntry(current, index, 1),
                            )
                          }
                          type="button"
                        >
                          下移
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ol>
              {unplannedEvents.length ? (
                <button
                  className={styles.appendMissing}
                  disabled={!editable}
                  onClick={() =>
                    setWorkingEntries((current) => [
                      ...current,
                      ...unplannedEvents.map(eventEntry),
                    ])
                  }
                  type="button"
                >
                  补入 {unplannedEvents.length} 个未编事件
                </button>
              ) : null}
              {message ? <p className={styles.message} role="status">{message}</p> : null}
              <footer>
                <button disabled={!dirty || status === "saving"} onClick={resetWorkingEntries} type="button">
                  恢复已保存顺序
                </button>
                <button
                  disabled={!editable || !dirty || status === "saving"}
                  onClick={() => void save()}
                  type="button"
                >
                  {status === "saving" ? "正在保存…" : "保存披露顺序"}
                </button>
              </footer>
            </>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
