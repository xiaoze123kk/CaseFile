import type { ReactNode } from "react";

import styles from "./workbench-agent.module.css";
import type { AgentSurface } from "./workbench-collaboration-state";

export type { AgentSurface } from "./workbench-collaboration-state";

/**
 * Agent owns conversation state; the surface only decides where that state is
 * presented inside the Workbench. Keeping this boundary deliberately thin
 * prevents the dock and desk from growing separate controllers.
 */
export function WorkbenchAgentSurface({
  surface,
  children,
  working = false,
}: {
  surface: AgentSurface;
  children: ReactNode;
  working?: boolean;
}) {
  return (
    <div className={styles.agentSurface} data-surface={surface} data-working={working}>
      {children}
    </div>
  );
}
