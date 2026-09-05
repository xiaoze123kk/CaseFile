import type { ReactNode } from "react";

import styles from "./workbench-agent.module.css";
import type { AgentSurface } from "./workbench-agent-surface";

/**
 * Layout-only shell for the two Agent presentations. Conversation and task
 * state deliberately stay in AgentLivePanel so switching Quick Ask to Desk
 * does not fork the underlying thread controller.
 */
export function WorkbenchAgentDesk({
  surface,
  taskStrip,
  conversation,
  prompts,
  composer,
}: {
  surface: AgentSurface;
  taskStrip?: ReactNode;
  conversation: ReactNode;
  prompts: ReactNode;
  composer: ReactNode;
}) {
  return (
    <section
      aria-label={surface === "dock" ? undefined : "卷宗统筹 Agent 对话"}
      className={`${styles.agentPanel} ${styles.agentPanelLive}`}
      data-surface={surface}
    >
      <div className={styles.agentConversationSlot} hidden={surface === "dock"}>{conversation}</div>
      {taskStrip}
      {prompts}
      {composer}
    </section>
  );
}
