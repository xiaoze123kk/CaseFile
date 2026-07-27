"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import {
  CaseSpine,
  DocumentHeader,
  PanelHeader,
  StatusBadge,
} from "@/components/prototype-ui";
import type { DraftEvent } from "@/lib/prototype-model";
import { usePrototype } from "@/store/prototype-store";

import styles from "./workbench-page.module.css";

type TimelineMode = "clock" | "narrative" | "list";
type EditableEventField = keyof Pick<
  DraftEvent,
  | "time"
  | "title"
  | "description"
  | "location"
  | "phase"
  | "participants"
  | "visibility"
  | "importance"
>;

const objectGroups = [
  { code: "ALL", label: "全部对象", count: 15 },
  { code: "ENT", label: "核心实体", count: 2 },
  { code: "LOC", label: "地点", count: 2 },
  { code: "EVL", label: "事件", count: 4, active: true },
  { code: "INF", label: "信息单元", count: 3 },
  { code: "CLM", label: "事实断言", count: 1 },
  { code: "HYP", label: "假设", count: 1 },
  { code: "RPN", label: "推理路径", count: 1 },
  { code: "CST", label: "约束", count: 1 },
] as const;

const pinnedObjects = [
  { id: "INFO-2107", label: "第五人权限记录", kind: "信息" },
  { id: "AI-7712", label: "受限协议 v2.1", kind: "实体" },
  { id: "BR-1800", label: "主控室循环", kind: "地点" },
] as const;

const modeLabels: Record<TimelineMode, string> = {
  clock: "真实时间",
  narrative: "叙事阶段",
  list: "紧凑列表",
};

function eventMarker(event: DraftEvent, mode: TimelineMode) {
  if (mode === "narrative") {
    const phaseNumber = event.phase.match(/\d+/)?.[0] ?? "—";
    return `PH.${phaseNumber.padStart(2, "0")}`;
  }
  return event.time;
}

export function WorkbenchPage() {
  const { state, dispatch, ready } = usePrototype();
  const [mode, setMode] = useState<TimelineMode>("clock");

  const selectedEvent =
    state.draft.events.find(
      (event) => event.id === state.draft.selectedEventId,
    ) ?? state.draft.events[0];

  const selectedIssue = useMemo(
    () =>
      state.validation.issues.find(
        (issue) =>
          issue.objectId === selectedEvent?.id && issue.status !== "resolved",
      ),
    [selectedEvent?.id, state.validation.issues],
  );

  const orderedEvents = useMemo(() => {
    if (mode === "narrative") return state.draft.events;
    return [...state.draft.events].sort((left, right) =>
      left.time.localeCompare(right.time),
    );
  }, [mode, state.draft.events]);

  function updateEvent(field: EditableEventField, value: string) {
    if (!selectedEvent) return;
    dispatch({ type: "update-event", id: selectedEvent.id, field, value });
  }

  function saveEvent() {
    dispatch({ type: "save-event" });
  }

  if (!ready || !selectedEvent) {
    return (
      <main className={`document ${styles.loading}`} aria-live="polite">
        <span>CASEFILE / LOADING DRAFT</span>
        <strong>正在展开卷宗索引…</strong>
      </main>
    );
  }

  const validationStale = state.validation.status !== "fresh";

  return (
    <main className={`document ${styles.document}`}>
      <DocumentHeader
        action={
          <button
            className="square-button square-button--dark"
            onClick={saveEvent}
            type="button"
          >
            保存事件
          </button>
        }
        eyebrow="ACTIVE DRAFT / EVENT DESK"
        meta={[
          { label: "SCHEMA", value: "0.1.0" },
          { label: "REVISION", value: `REV.${state.draft.revision}` },
          {
            label: "VALIDATION",
            value: validationStale ? "结果过期" : state.validation.runId,
            tone: validationStale ? "critical" : "default",
          },
          { label: "AUTOSAVE", value: state.draft.lastSavedAt },
        ]}
        title={`${state.project.projectId} : ${state.project.casefileTitle}`}
      />

      <CaseSpine current="draft" stale={validationStale} />

      <section className={styles.workspace} aria-label="CaseFile 事件编辑工作台">
        <aside className={`paper-panel ${styles.objectPanel}`}>
          <PanelHeader
            code="OBJECT REGISTER / READ INDEX"
            title="对象索引"
            trailing={<StatusBadge tone="neutral">15 OBJECTS</StatusBadge>}
          />
          <div className={styles.objectScroll}>
            <section className={styles.indexSection} aria-labelledby="object-types">
              <div className={styles.sectionCaption} id="object-types">
                <span>对象类型</span>
                <small>TYPE / COUNT</small>
              </div>
              <ul className={styles.objectGroups}>
                {objectGroups.map((group) => (
                  <li
                    className={
                      group.code === "EVL" ? styles.activeGroup : undefined
                    }
                    key={group.code}
                  >
                    <span className={styles.groupCode}>{group.code}</span>
                    <strong>{group.label}</strong>
                    <span className={styles.groupCount}>{group.count}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className={styles.indexSection} aria-labelledby="event-register">
              <div className={styles.sectionCaption} id="event-register">
                <span>事件登记簿</span>
                <small>EVL / CURRENT</small>
              </div>
              <div className={styles.registerList}>
                {state.draft.events.map((event) => {
                  const active = event.id === selectedEvent.id;
                  return (
                    <button
                      aria-pressed={active}
                      className={active ? styles.activeRegister : undefined}
                      key={event.id}
                      onClick={() =>
                        dispatch({ type: "select-event", id: event.id })
                      }
                      type="button"
                    >
                      <span>{event.id}</span>
                      <strong>{event.title}</strong>
                      <small>{event.time}</small>
                    </button>
                  );
                })}
              </div>
            </section>

            <section className={styles.indexSection} aria-labelledby="pinned-objects">
              <div className={styles.sectionCaption} id="pinned-objects">
                <span>收藏夹</span>
                <small>PINNED / 03</small>
              </div>
              <ul className={styles.pinnedList}>
                {pinnedObjects.map((object) => (
                  <li key={object.id}>
                    <span>{object.id}</span>
                    <strong>{object.label}</strong>
                    <small>{object.kind}</small>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </aside>

        <section className={`paper-panel ${styles.timelinePanel}`}>
          <PanelHeader
            code={`${modeLabels[mode]} / ${orderedEvents.length} RECORDS`}
            title="事件 / 时间线"
            trailing={
              <div className={styles.modeSwitch} aria-label="时间线视图">
                {(
                  [
                    ["clock", "真实"],
                    ["narrative", "叙事"],
                    ["list", "列表"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    aria-pressed={mode === value}
                    className={mode === value ? styles.activeMode : undefined}
                    key={value}
                    onClick={() => setMode(value)}
                    type="button"
                  >
                    {label}
                  </button>
                ))}
              </div>
            }
          />

          <div className={styles.timelineToolbar}>
            <div>
              <span className={styles.redDot} />
              <strong>{modeLabels[mode]}</strong>
              <small>
                {mode === "clock"
                  ? "按事实发生时间排序"
                  : mode === "narrative"
                    ? "按玩家获知阶段编排"
                    : "隐藏摘要，快速巡检对象"}
              </small>
            </div>
            <span className="mono">18:00—18:25 / LOOP 07</span>
          </div>

          {validationStale ? (
            <div className={styles.staleNotice} role="status">
              <span>!</span>
              <div>
                <strong>质量报告需要重新验证</strong>
                <small>
                  当前草稿 REV.{state.draft.revision} 已晚于验证快照 REV.
                  {state.validation.snapshotRevision}
                </small>
              </div>
              <Link href="/quality">前往验证 →</Link>
            </div>
          ) : null}

          <div
            className={`${styles.timeline} ${mode === "list" ? styles.compactTimeline : ""}`}
            role="list"
          >
            {orderedEvents.map((event, index) => {
              const active = event.id === selectedEvent.id;
              const severity = state.validation.issues.find(
                (issue) =>
                  issue.objectId === event.id && issue.status !== "resolved",
              )?.severity;
              return (
                <button
                  aria-current={active ? "true" : undefined}
                  className={`${styles.eventCard} ${active ? styles.activeEvent : ""}`}
                  key={event.id}
                  onClick={() =>
                    dispatch({ type: "select-event", id: event.id })
                  }
                  role="listitem"
                  type="button"
                >
                  <span className={styles.timelineRail} aria-hidden="true">
                    <i />
                    {index < orderedEvents.length - 1 ? <b /> : null}
                  </span>
                  <span className={styles.eventMarker}>
                    {eventMarker(event, mode)}
                  </span>
                  <span className={styles.eventBody}>
                    <span className={styles.eventHeading}>
                      <strong>{event.title}</strong>
                      <span className="mono">{event.id}</span>
                      {severity ? (
                        <StatusBadge tone={severity === "S1" ? "red" : "warning"}>
                          {severity}
                        </StatusBadge>
                      ) : null}
                    </span>
                    {mode !== "list" ? <p>{event.description}</p> : null}
                    <span className={styles.tagRow}>
                      {event.tags.map((tag) => (
                        <i key={tag}>{tag}</i>
                      ))}
                    </span>
                  </span>
                  <span className={styles.referenceCount}>
                    <b>{event.refCount}</b>
                    <small>REFS</small>
                  </span>
                </button>
              );
            })}
          </div>

          <section className={styles.linkedRecords} aria-labelledby="linked-records">
            <header id="linked-records">
              <span>关联信息单元</span>
              <small>SEMANTIC REFERENCES / 03</small>
            </header>
            <div>
              {pinnedObjects.map((record) => (
                <article key={record.id}>
                  <span>{record.id}</span>
                  <strong>{record.label}</strong>
                  <small>被 {selectedEvent.id} 引用</small>
                </article>
              ))}
            </div>
          </section>
        </section>

        <aside className={`paper-panel ${styles.inspectorPanel}`}>
          <PanelHeader
            code={`${selectedEvent.id} / LIVE OBJECT`}
            title="检查器"
            trailing={
              <StatusBadge tone={selectedIssue ? "red" : "dark"}>
                {selectedIssue ? selectedIssue.severity : "CLEAR"}
              </StatusBadge>
            }
          />

          <form
            className={styles.inspectorForm}
            onSubmit={(event) => {
              event.preventDefault();
              saveEvent();
            }}
          >
            <div className={styles.inspectorIdentity}>
              <span>SELECTED EVENT</span>
              <strong>{selectedEvent.id}</strong>
              <small>修改字段会创建新修订并使验证报告过期</small>
            </div>

            <label className={styles.wideField}>
              <span>事件标题</span>
              <input
                onChange={(event) => updateEvent("title", event.target.value)}
                value={selectedEvent.title}
              />
            </label>

            <div className={styles.fieldGrid}>
              <label>
                <span>真实时间</span>
                <input
                  aria-label="真实时间"
                  onChange={(event) => updateEvent("time", event.target.value)}
                  type="time"
                  value={selectedEvent.time}
                />
              </label>
              <label>
                <span>重要级别</span>
                <select
                  onChange={(event) =>
                    updateEvent("importance", event.target.value)
                  }
                  value={selectedEvent.importance}
                >
                  <option>普通事件</option>
                  <option>重要事件</option>
                  <option>关键事件</option>
                </select>
              </label>
            </div>

            <label className={styles.wideField}>
              <span>叙事阶段</span>
              <input
                onChange={(event) => updateEvent("phase", event.target.value)}
                value={selectedEvent.phase}
              />
            </label>

            <label className={styles.wideField}>
              <span>事件描述</span>
              <textarea
                onChange={(event) =>
                  updateEvent("description", event.target.value)
                }
                rows={3}
                value={selectedEvent.description}
              />
            </label>

            <label className={styles.wideField}>
              <span>发生地点</span>
              <input
                onChange={(event) => updateEvent("location", event.target.value)}
                value={selectedEvent.location}
              />
            </label>

            <label className={styles.wideField}>
              <span>参与对象</span>
              <input
                onChange={(event) =>
                  updateEvent("participants", event.target.value)
                }
                value={selectedEvent.participants}
              />
            </label>

            <label className={styles.wideField}>
              <span>可见范围 / KNOWLEDGE SCOPE</span>
              <select
                className={selectedIssue ? styles.issueField : undefined}
                onChange={(event) =>
                  updateEvent("visibility", event.target.value)
                }
                value={selectedEvent.visibility}
              >
                <option>全部角色</option>
                <option>AI 核心 + 全部角色</option>
                <option>AI 核心 + 秦彻</option>
                <option>林望 + 秦彻</option>
                <option>仅主持人</option>
              </select>
            </label>

            {selectedIssue ? (
              <Link className={styles.issueCard} href="/quality">
                <span>
                  <b>{selectedIssue.severity}</b>
                  {selectedIssue.ruleId}
                </span>
                <strong>{selectedIssue.title}</strong>
                <p>{selectedIssue.explanation}</p>
                <small>打开质量中心查看证据链与修复候选 →</small>
              </Link>
            ) : (
              <div className={styles.clearCard} role="status">
                <span>✓</span>
                <div>
                  <strong>当前对象没有开放问题</strong>
                  <small>仍需重新验证后才能更新发布门禁。</small>
                </div>
              </div>
            )}

            <footer className={styles.formFooter}>
              <span>
                LAST SAVE
                <strong>{state.draft.lastSavedAt}</strong>
              </span>
              <button className="square-button square-button--red" type="submit">
                保存 REV.{state.draft.revision}
              </button>
            </footer>
          </form>
        </aside>
      </section>
    </main>
  );
}
