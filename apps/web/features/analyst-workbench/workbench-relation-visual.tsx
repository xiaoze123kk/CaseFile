import type { RelationOverviewItem } from "./workbench-relation-overview";
import styles from "./workbench-relation-visual.module.css";

export function RelationObjectMark({ label, objectType }: { label: string; objectType: string }) {
  return <span aria-hidden="true" className={styles.objectMark} data-kind={objectType}>{label.slice(0, 1)}</span>;
}

export function RelationFlow({ flow }: { flow: RelationOverviewItem["flow"] }) {
  return <span aria-hidden="true" className={styles.flow} data-direction={flow.direction}>
    <span className={styles.endpoint}>{flow.left}</span>
    <span className={styles.connection}><i /><em>{flow.label}</em><i /></span>
    <span className={styles.endpoint}>{flow.right}</span>
  </span>;
}
