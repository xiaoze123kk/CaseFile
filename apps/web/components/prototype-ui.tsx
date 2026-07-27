import type { ReactNode } from "react";

import type { CaseStage } from "@/lib/prototype-model";

const stages: Array<{
  id: CaseStage;
  no: string;
  label: string;
  completeLabel: string;
}> = [
  { id: "idea", no: "01", label: "创意", completeLabel: "已整理" },
  { id: "brief", no: "02", label: "Brief", completeLabel: "已确认" },
  { id: "draft", no: "03", label: "编辑草稿", completeLabel: "正在编辑" },
  { id: "validated", no: "04", label: "已验证", completeLabel: "报告有效" },
  { id: "compiled", no: "05", label: "已编译", completeLabel: "产物就绪" },
];

export function CaseSpine({
  current,
  stale = false,
}: {
  current: CaseStage;
  stale?: boolean;
}) {
  const currentIndex = stages.findIndex((stage) => stage.id === current);

  return (
    <section className="case-spine" aria-label="卷宗工作流">
      <span className="spine-caption">卷宗脊线 / CASE SPINE</span>
      <ol>
        {stages.map((stage, index) => {
          const done = index < currentIndex;
          const active = index === currentIndex;
          const detail =
            stage.id === "validated" && stale
              ? "结果过期"
              : done || active
                ? stage.completeLabel
                : "未开始";
          return (
            <li
              className={done ? "is-done" : active ? "is-current" : undefined}
              key={stage.id}
            >
              <b>{done ? "✓" : stage.no}</b>
              <span>
                <strong>{stage.label}</strong>
                <small>{detail}</small>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export interface HeaderMeta {
  label: string;
  value: string;
  tone?: "default" | "critical";
}

export function DocumentHeader({
  eyebrow,
  title,
  meta,
  action,
}: {
  eyebrow: string;
  title: string;
  meta: HeaderMeta[];
  action?: ReactNode;
}) {
  return (
    <header className="document-head">
      <div>
        <span className="record-status">
          <i />
          {eyebrow}
        </span>
        <h1>{title}</h1>
      </div>
      <div className="document-head__right">
        <div className="record-meta">
          {meta.map((item) => (
            <div className={item.tone === "critical" ? "is-critical" : ""} key={item.label}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
        {action}
      </div>
    </header>
  );
}

export function PanelHeader({
  title,
  code,
  trailing,
}: {
  title: string;
  code: string;
  trailing?: ReactNode;
}) {
  return (
    <header className="panel-header">
      <div>
        <span>{title}</span>
        <b>{code}</b>
      </div>
      {trailing}
    </header>
  );
}

export function StatusBadge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "red" | "warning" | "dark";
}) {
  return <span className={`status-badge status-badge--${tone}`}>{children}</span>;
}
