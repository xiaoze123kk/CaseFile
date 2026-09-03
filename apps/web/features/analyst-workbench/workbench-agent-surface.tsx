import type { ReactNode } from "react";

import styles from "./workbench-agent.module.css";

export type AgentSurface = "dock" | "desk";

/**
 * Agent owns conversation state; the surface only decides where that state is
 * presented inside the Workbench. Keeping this boundary deliberately thin
 * prevents the dock and desk from growing separate controllers.
 */
export function WorkbenchAgentSurface({
  surface,
  children,
}: {
  surface: AgentSurface;
  children: ReactNode;
}) {
  return (
    <div className={styles.agentSurface} data-surface={surface}>
      {children}
    </div>
  );
}
