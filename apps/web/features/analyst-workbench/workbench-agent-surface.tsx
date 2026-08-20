import type { ReactNode } from "react";

import styles from "./workbench-agent.module.css";

export type AgentSurface = "closed" | "quick" | "desk";

/**
 * Agent owns conversation state; the surface only decides where that state is
 * presented inside the Workbench. Keeping this boundary deliberately thin
 * prevents Quick Ask and Desk from growing separate controllers.
 */
export function WorkbenchAgentSurface({
  surface,
  children,
}: {
  surface: Exclude<AgentSurface, "closed">;
  children: ReactNode;
}) {
  return (
    <div className={styles.agentSurface} data-surface={surface}>
      {children}
    </div>
  );
}
