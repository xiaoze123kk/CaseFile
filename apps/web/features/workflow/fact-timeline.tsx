"use client";

import { Fragment, useMemo } from "react";

import styles from "./real-workbench.module.css";
import {
  objectDescription,
  objectHeadline,
  timelineEntries,
  type WorkbenchObject,
  type WorkbenchSelection,
} from "./workbench-model";

export function FactTimeline({
  events,
  selectedObjectId,
  onSelect,
  onDiscuss,
}: {
  events: WorkbenchObject[];
  selectedObjectId?: string | null;
  onSelect: (selection: WorkbenchSelection) => void;
  onDiscuss: (event: WorkbenchObject) => void;
}) {
  const entries = useMemo(() => timelineEntries(events), [events]);

  if (!entries.length) {
    return (
      <div className={styles.timelineEmpty}>
        <span aria-hidden="true">◷</span>
        <strong>还没有可编排的事件</strong>
        <p>事件写入卷宗后，会按事实发生时间自动排列在这里。</p>
      </div>
    );
  }

  return (
    <div className={styles.timelineScroll}>
      <header className={styles.timelineIntroduction}>
        <span>事实记录</span>
        <strong>按发生时间排列</strong>
        <p>约略时间会明确标注；时间未知的事件统一收在末尾。</p>
      </header>
      <div className={styles.timelineLine}>
        {entries.map((entry, index) => {
          const showDay =
            index === 0 ||
            entry.dayLabel !== entries[index - 1]?.dayLabel;
          const active = entry.event.id === selectedObjectId;
          return (
            <Fragment key={entry.event.id}>
              {showDay ? (
                <h3
                  className={`${styles.timelineDay} ${
                    entry.unknown ? styles.timelineUnknownDay : ""
                  }`}
                >
                  {entry.dayLabel}
                </h3>
              ) : null}
              <article
                className={`${styles.timelineEntry} ${
                  active ? styles.activeTimelineEntry : ""
                }`}
              >
                <button
                  aria-pressed={active}
                  className={styles.timelineEntryMain}
                  onClick={() =>
                    onSelect({
                      collection: "events",
                      objectId: entry.event.id,
                    })
                  }
                  type="button"
                >
                  <span className={styles.timelineMarker} aria-hidden="true" />
                  <time>{entry.timeLabel}</time>
                  <strong>{objectHeadline(entry.event)}</strong>
                  <p>{objectDescription(entry.event)}</p>
                </button>
                <button
                  className={styles.timelineDiscuss}
                  onClick={() => onDiscuss(entry.event)}
                  type="button"
                >
                  与 Agent 讨论
                  <span aria-hidden="true">↗</span>
                </button>
              </article>
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}
