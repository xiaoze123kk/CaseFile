import type { ReactNode } from "react";

import styles from "./workbench-agent.module.css";
import type { AgentSurface } from "./workbench-agent-surface";
import { WorkbenchIcon } from "./workbench-icon";

/**
 * Layout-only shell for the two Agent presentations. Conversation and task
 * state deliberately stay in AgentLivePanel so switching Quick Ask to Desk
 * does not fork the underlying thread controller.
 */
export function WorkbenchAgentDesk({
  surface,
  threadManager,
  taskStrip,
  conversation,
  prompts,
  composer,
  onClose,
}: {
  surface: Exclude<AgentSurface, "closed">;
  threadManager?: ReactNode;
  taskStrip?: ReactNode;
  conversation: ReactNode;
  prompts: ReactNode;
  composer: ReactNode;
  onClose: () => void;
}) {
  return (
    <section
      aria-label="卷宗统筹 Agent 对话"
      className={`${styles.agentPanel} ${styles.agentPanelLive}`}
      data-surface={surface}
    >
      <header className={styles.agentHeader}>
        <div>
          <span>卷宗统筹</span>
          {surface === "desk" && threadManager ? (
            threadManager
          ) : (
            <strong>快速询问</strong>
          )}
        </div>
        <button aria-label="关闭 Agent 对话" onClick={onClose} type="button">
          <WorkbenchIcon name="close" />
        </button>
      </header>
      {surface === "desk" ? taskStrip : null}
      {conversation}
      {prompts}
      {composer}
    </section>
  );
}
