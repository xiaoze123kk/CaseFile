import { useState } from "react";

import {
  getEvent,
  type IssueStatus,
  type WorkbenchSeed,
} from "./analyst-fixture";
import styles from "./analyst-workbench.module.css";
import { reasoningOutcomeLabels } from "./workbench-presenters";
import type { WorkbenchModel } from "./workbench-real-data";

function timelineClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function TimelineOverview({
  seed,
  selectedEventId,
  issueStatuses,
  onSelectEvent,
}: {
  seed: WorkbenchSeed;
  selectedEventId: string | null;
  issueStatuses: Record<string, IssueStatus>;
  onSelectEvent: (eventId: string) => void;
}) {
  const selectedEvent = getEvent(seed, selectedEventId) ?? seed.timelineEvents[0];

  if (!selectedEvent) {
    return null;
  }

  return (
    <section
      className={styles.timelinePanel}
      aria-labelledby="timeline-heading"
    >
      <header className={styles.sectionHeader}>
        <div>
          <span>事件序列</span>
          <h2 id="timeline-heading">{seed.caseMeta.timelineTitle}</h2>
        </div>
        <small>{seed.caseMeta.timelineMeta}</small>
      </header>
      <ol className={styles.timelineList}>
        {seed.timelineEvents.map((event) => {
          const selected = event.id === selectedEventId;
          const issue = seed.validationIssues.find((item) =>
            event.issueIds.includes(item.id),
          );
          const issueStatus = issue ? issueStatuses[issue.id] : undefined;
          return (
            <li key={event.id}>
              <button
                aria-pressed={selected}
                data-selected={selected}
                onClick={() => onSelectEvent(event.id)}
                type="button"
              >
                <time
                  aria-label={event.time}
                  className={styles.eventTime}
                  dateTime={event.time}
                  title={event.time}
                >
                  {timelineClock(event.time)}
                </time>
                <span className={styles.eventMarker} aria-hidden="true" />
                <span className={styles.eventCopy}>
                  <strong>{event.label}</strong>
                  <small>{event.location}</small>
                  {selected ? <em>{event.summary}</em> : null}
                </span>
                {issue ? (
                  <span
                    className={styles.eventIssue}
                    data-status={issueStatus}
                  >
                    {issue.severity}
                  </span>
                ) : (
                  <span className={styles.eventClear}>通过</span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function DossierView({
  seed,
  selectedEventId,
}: {
  seed: WorkbenchSeed;
  selectedEventId: string | null;
}) {
  const event = getEvent(seed, selectedEventId) ?? seed.timelineEvents[0];
  if (!event) return null;
  const realData = (seed as WorkbenchSeed & Partial<WorkbenchModel>).origin === "contract";
  const objectById = new Map(
    seed.caseObjects.map((object) => [object.id, object]),
  );
  const relatedObjects = event.relatedObjectIds
    .map((id) => objectById.get(id))
    .filter((object) => object !== undefined);
  const people = relatedObjects
    .filter((object) => object.kind === "person" || object.kind === "entity")
    .map((object) => object.label);
  const locations = relatedObjects
    .filter((object) => object.kind === "location")
    .map((object) => object.label);
  const evidence = relatedObjects
    .filter((object) => object.kind === "evidence" || object.kind === "information")
    .map((object) => object.label);
  const hypotheses = relatedObjects
    .filter((object) => object.kind === "hypothesis")
    .map((object) => object.label);
  const sources = seed.sourceItems.filter(
    (source) => source.eventId === event.id,
  );
  const issues = seed.validationIssues.filter((issue) =>
    event.issueIds.includes(issue.id),
  );

  return (
    <section className={styles.dossierView} aria-labelledby="dossier-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>卷宗编辑器</span>
          <h2 id="dossier-heading">{event.label}</h2>
        </div>
        <small>结构化字段 · {seed.caseMeta.revision}</small>
      </header>
      <div className={styles.dossierSheet}>
        <div className={styles.sheetIndex}>
          <span>EV</span>
          <strong>{event.id.replace("EV-", "")}</strong>
        </div>
        <div className={styles.sheetFields}>
          <label>
            <span>发生时间</span>
            <input defaultValue={event.time} readOnly={realData} />
          </label>
          <label>
            <span>发生地点</span>
            <input defaultValue={event.location} readOnly={realData} />
          </label>
          <label className={styles.sheetWide}>
            <span>事件摘要</span>
            <textarea defaultValue={event.summary} readOnly={realData} rows={5} />
          </label>
          <label>
            <span>{realData ? "参与实体" : "参与人物"}</span>
            <input defaultValue={people.join("、")} readOnly={realData} />
          </label>
          <label>
            <span>关联地点</span>
            <input defaultValue={locations.join("、")} readOnly={realData} />
          </label>
          <label>
            <span>{realData ? "关联信息" : "关联证据"}</span>
            <input defaultValue={evidence.join("、")} readOnly={realData} />
          </label>
          <label>
            <span>候选假设</span>
            <input defaultValue={hypotheses.join("、")} readOnly={realData} />
          </label>
          <label className={styles.sheetWide}>
            <span>引用来源</span>
            <input
              defaultValue={sources.map((source) => source.label).join("、")}
              readOnly={realData}
            />
          </label>
        </div>
        <aside className={styles.marginNotes}>
          <span>引用 {String(sources.length).padStart(2, "0")}</span>
          {issues.map((issue) => (
            <p key={issue.id}>
              {issue.severity} · {issue.title}
            </p>
          ))}
          {realData ? <p>编辑请使用右侧“对象详情”</p> : <p>知识状态存在冲突</p>}
        </aside>
      </div>
    </section>
  );
}

export function ExportView({
  seed,
  unresolvedCount,
}: {
  seed: WorkbenchSeed;
  unresolvedCount: number;
}) {
  const realData = (seed as WorkbenchSeed & Partial<WorkbenchModel>).origin === "contract";
  const ready = !realData && unresolvedCount === 0;

  return (
    <section className={styles.exportView} aria-labelledby="export-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>导出预览</span>
          <h2 id="export-heading">{seed.caseMeta.exportTitle}</h2>
        </div>
        <small>{realData ? "DEVELOPMENT PREVIEW" : ready ? "READY" : "GATE BLOCKED"}</small>
      </header>
      <div className={styles.exportSheet}>
        <div className={styles.exportCover}>
          <span>{seed.caseMeta.exportCode}</span>
          <h3>{seed.caseMeta.title}</h3>
          <p>{seed.caseMeta.exportSubtitle}</p>
          <strong>{seed.caseMeta.revision}</strong>
        </div>
        <div className={styles.exportChecks}>
          <h3>发布门禁</h3>
          <ul>
            <li data-state="pass">
              <span>{realData ? "真实对象投影" : "结构完整性"}</span>
              <b>{realData ? "已生成" : "通过"}</b>
            </li>
            <li data-state="pass">
              <span>{realData ? "真实关系投影" : "引用可追溯"}</span>
              <b>{realData ? "已生成" : "通过"}</b>
            </li>
            <li data-state={ready ? "pass" : "blocked"}>
              <span>语义验证</span>
              <b>{ready ? "通过" : `${unresolvedCount} 个问题`}</b>
            </li>
            <li data-state="pending">
              <span>作者批准</span>
              <b>待确认</b>
            </li>
          </ul>
          <button disabled={!ready} type="button">
            {realData ? "正式导出尚未接入" : "生成导出包"}
          </button>
          {realData ? <p>当前页面仅从真实 Draft 派生开发预览。</p> : !ready ? <p>先处理右侧检查器中的 S0/S1 问题。</p> : null}
        </div>
      </div>
    </section>
  );
}

type CompileTargetId =
  | "novel"
  | "script"
  | "interactive"
  | "dossier"
  | "test";

const compileTargets: Array<{
  id: CompileTargetId;
  label: string;
  caption: string;
  description: string;
}> = [
  {
    id: "novel",
    label: "小说",
    caption: "章节叙事",
    description: "把事件序列编排成可读的章节化叙事。",
  },
  {
    id: "script",
    label: "剧本",
    caption: "剧本杀手册",
    description: "角色、场景、幕次与线索卡，供线下开本。",
  },
  {
    id: "interactive",
    label: "互动脚本",
    caption: "任务与对话树",
    description: "分支对话与任务数据，供互动游戏引擎使用。",
  },
  {
    id: "dossier",
    label: "作者卷宗",
    caption: "文档包",
    description: "面向作者的对象清单、时间线与编辑笔记。",
  },
  {
    id: "test",
    label: "测试材料",
    caption: "QA 用例",
    description: "验证问题与门禁检查，供测试与验收。",
  },
];

function composeCompilePreview(
  targetId: CompileTargetId,
  seed: WorkbenchSeed,
  unresolvedCount: number,
): string {
  const realData = (seed as WorkbenchSeed & Partial<WorkbenchModel>).origin === "contract";
  const people = seed.caseObjects
    .filter((object) => object.kind === "person" || object.kind === "entity")
    .map((object) => object.label);
  const evidence = seed.caseObjects
    .filter((object) => object.kind === "evidence" || object.kind === "information")
    .map((object) => object.label);
  const events = seed.timelineEvents;

  switch (targetId) {
    case "novel":
      return [
        `《${seed.caseMeta.title}》`,
        seed.caseMeta.subtitle,
        "",
        ...events.map(
          (event, index) =>
            `第${"一二三四五六七八九十"[index] ?? index + 1}章 · ${event.label}\n${event.time}，${event.location}。${event.summary}`,
        ),
      ].join("\n");
    case "script":
      return [
        `剧本杀手册 · ${seed.caseMeta.title}`,
        `角色：${people.join("、")}`,
        `场景：${[...new Set(events.map((event) => event.location))].join("、")}`,
        "",
        ...events.map(
          (event) => `第 ${event.time} 幕 · ${event.label}\n${event.summary}`,
        ),
        `线索卡：${evidence.join("、")}`,
      ].join("\n");
    case "interactive":
      return [
        `互动脚本 · ${seed.caseMeta.title}`,
        "",
        ...events.map(
          (event) => `节点 ${event.id} · ${event.label}\n可停留：${event.summary}`,
        ),
        "",
        ...seed.reasoningPaths.map(
          (path) =>
            `分支 · ${path.question}\n→ ${path.conclusion}（${reasoningOutcomeLabels[path.outcome]}）`,
        ),
      ].join("\n");
    case "dossier":
      return [
        `作者卷宗 · ${seed.caseMeta.title}`,
        `修订 ${seed.caseMeta.revision}`,
        "",
        `对象 ${seed.caseObjects.length} 个（${realData ? "实体" : "人物"} ${people.length} · ${realData ? "信息" : "证据"} ${evidence.length}）`,
        `事件 ${events.length} 个 · 推理路径 ${seed.reasoningPaths.length} 条`,
        `待处理问题 ${unresolvedCount} 个`,
        "",
        "编译产物为开发样例，正式版本由 Compiler 生成。",
      ].join("\n");
    case "test":
      return [
        `测试材料 · ${seed.caseMeta.title}`,
        "",
        ...seed.validationIssues.map(
          (issue) =>
            `用例 ${issue.id} · ${issue.severity} ${issue.title}\n规则 ${issue.rule} · 依据 ${issue.evidenceIds.join("、")}`,
        ),
        "",
        `门禁：${unresolvedCount > 0 ? `语义验证阻断（${unresolvedCount} 个问题）` : "全部通过"}`,
      ].join("\n");
  }
}

export function CompileCenterView({
  seed,
  unresolvedCount,
}: {
  seed: WorkbenchSeed;
  unresolvedCount: number;
}) {
  const [targetId, setTargetId] = useState<CompileTargetId>("novel");
  const [compiled, setCompiled] = useState(false);
  const target =
    compileTargets.find((item) => item.id === targetId) ?? compileTargets[0];
  const realData = (seed as WorkbenchSeed & Partial<WorkbenchModel>).origin === "contract";
  const blocked = !realData && unresolvedCount > 0;

  return (
    <section className={styles.compileView} aria-labelledby="compile-heading">
      <header className={styles.sectionHeader}>
        <div>
          <span>编译中心</span>
          <h2 id="compile-heading">同一份卷宗，多种形式</h2>
        </div>
        <small>{compileTargets.length} FORMATS</small>
      </header>
      <div className={styles.compileTargets} aria-label="编译目标">
        {compileTargets.map((item) => (
          <button
            aria-pressed={targetId === item.id}
            data-selected={targetId === item.id}
            key={item.id}
            onClick={() => {
              setTargetId(item.id);
              setCompiled(false);
            }}
            type="button"
          >
            <span>{item.caption}</span>
            <strong>{item.label}</strong>
            <small>{item.description}</small>
          </button>
        ))}
      </div>
      <div className={styles.compileWorkspace}>
        <section aria-label="编译预览" className={styles.compilePreview}>
          <header>
            <span>编译预览</span>
            <strong>
              {target.label} · {seed.caseMeta.title}
            </strong>
          </header>
          <pre>{composeCompilePreview(targetId, seed, unresolvedCount)}</pre>
          {compiled ? (
            <p className={styles.compileDone}>
              已生成 {target.label} 产物（开发样例，正式版本由 Compiler 生成）。
            </p>
          ) : null}
        </section>
        <aside className={styles.compilePanel}>
          <span>编译选项</span>
          <label>
            <span>产物标题</span>
            <input defaultValue={`${seed.caseMeta.title} · ${target.label}`} />
          </label>
          <label>
            <span>来源修订</span>
            <input defaultValue={seed.caseMeta.revision} />
          </label>
          <div className={styles.compileGate}>
            <span>发布门禁</span>
            <ul>
              <li data-state="pass">
                <span>结构完整性</span>
                <b>通过</b>
              </li>
              <li data-state="pass">
                <span>引用可追溯</span>
                <b>通过</b>
              </li>
              <li data-state={blocked ? "blocked" : "pass"}>
                <span>语义验证</span>
                <b>{blocked ? `${unresolvedCount} 个问题` : "通过"}</b>
              </li>
            </ul>
          </div>
          <button
            data-primary="true"
            disabled={blocked}
            onClick={() => setCompiled(true)}
            type="button"
          >
            {blocked ? "先处理验证问题" : realData ? `生成${target.label}开发预览` : `编译为${target.label}`}
          </button>
          {blocked ? <p>存在未解决验证问题，编译产物可能携带矛盾。</p> : null}
        </aside>
      </div>
    </section>
  );
}
