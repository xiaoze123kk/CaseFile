import type { ReactNode } from "react";

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
  leading,
  trailing,
}: {
  title: string;
  code: string;
  leading?: ReactNode;
  trailing?: ReactNode;
}) {
  return (
    <header className="panel-header">
      {leading ? (
        <div className="panel-header__leading">
          {leading}
          <div>
            <span>{title}</span>
            <b>{code}</b>
          </div>
        </div>
      ) : (
        <div>
          <span>{title}</span>
          <b>{code}</b>
        </div>
      )}
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
