import type { BriefIntakeFieldSource } from "@/lib/api-client";

import { sourceLabels, sourceTones } from "./intake-model";
import styles from "./brief-intake-workspace.module.css";

export function IntakeSourceBadge({ source }: { source: BriefIntakeFieldSource }) {
  return (
    <span className={styles.sourceBadge} data-tone={sourceTones[source]}>
      {sourceLabels[source]}
    </span>
  );
}
