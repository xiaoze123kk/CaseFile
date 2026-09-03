import type { ReactNode } from "react";

import {
  fieldSourceLabels,
  type FieldSource,
} from "./intake-model";
import { Glyph, type IntakeGlyphName } from "./intake-glyph";
import styles from "./intake-early-stages.module.css";

type BriefFieldKind =
  | "concept"
  | "reasoning"
  | "conclusion"
  | "resolution"
  | "answer"
  | "selling-points"
  | "outline"
  | "scope"
  | "risk";

export function SourceBadge({ source }: { source: FieldSource }) {
  return (
    <span className={styles.sourceBadge} data-source={source}>
      <i aria-hidden="true" />
      {fieldSourceLabels[source]}
    </span>
  );
}

export function FieldShell({
  agentChanged = false,
  field,
  icon,
  label,
  hint,
  source,
  required = false,
  wide = false,
  children,
}: {
  agentChanged?: boolean;
  field: BriefFieldKind;
  icon: IntakeGlyphName;
  label: string;
  hint: string;
  source: FieldSource;
  required?: boolean;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      className={styles.fieldShell}
      data-agent-changed={agentChanged || undefined}
      data-field={field}
      data-required={required || undefined}
      data-wide={wide}
    >
      <header>
        <div className={styles.fieldShellHeading}>
          <span aria-hidden="true" className={styles.fieldShellIcon}>
            <Glyph name={icon} />
          </span>
          <div>
            <label>
              {label}
              {required ? <em aria-hidden="true">*</em> : null}
            </label>
            <small>{hint}</small>
          </div>
        </div>
        <div className={styles.fieldShellStatus}>
          {agentChanged ? <span>本轮已修改</span> : null}
          <SourceBadge source={source} />
        </div>
      </header>
      {children}
    </section>
  );
}
