import type { BriefIntakePendingDecision } from "@/lib/api-client";

import styles from "./brief-workspace.module.css";

interface BriefPendingDecisionsProps {
  decisions: BriefIntakePendingDecision[];
}

export function BriefPendingDecisions({
  decisions,
}: BriefPendingDecisionsProps) {
  if (!decisions.length) return null;

  return (
    <section
      aria-label="待决定事项（只读）"
      className={styles.pendingDecisionField}
    >
      <header>
        <span>
          <strong>待决定事项</strong>
          <small>来自建案阶段，只读保留</small>
        </span>
        <b>{decisions.length} 项</b>
      </header>
      <ul>
        {decisions.map((decision) => (
          <li key={decision.decision_key}>
            <strong>{decision.prompt}</strong>
            <span>{decision.impact}</span>
          </li>
        ))}
      </ul>
      <footer>这些事项不会阻止正式审阅，可在后续创作中继续决定。</footer>
    </section>
  );
}
