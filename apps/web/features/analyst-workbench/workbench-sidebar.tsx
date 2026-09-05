import type { ReactNode } from "react";
import type { WorkspaceMode } from "./workbench-views";
import type { SidePanelBase } from "./workbench-collaboration-state";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./analyst-workbench.module.css";

/** A stable pair of base-page slots with an independent temporary-detail slot. */
export function WorkbenchSidebar({ mode, base, open, hidden = false, agentVisible, hasDetail, objectContent,
  agentHostRef, detailHostRef, onBaseChange, onClose, history,
}: {
  mode: WorkspaceMode;
  base: SidePanelBase;
  open: boolean;
  hidden?: boolean;
  agentVisible: boolean;
  hasDetail: boolean;
  objectContent: ReactNode;
  agentHostRef: (element: HTMLDivElement | null) => void;
  detailHostRef: (element: HTMLDivElement | null) => void;
  onBaseChange: (base: SidePanelBase) => void;
  onClose: () => void;
  history: { backLabel: string; forwardLabel: string; canBack: boolean; canForward: boolean; back: () => void; forward: () => void };
}) {
  return <aside aria-label="对象上下文" className={styles.inspector} hidden={hidden}>
    <header className={styles.inspectorHeader}>
      <div>{mode === "analysis" || mode === "compile" ? <div className={styles.sidePanelTabs} role="tablist" aria-label="工作侧栏">
        {(["object", "agent"] as const).map((page) => {
          const label = page === "object" ? "对象详情" : "协作者";
          return <button key={page} type="button" role="tab" aria-label={label} title={label} aria-selected={base === page} onClick={() => onBaseChange(page)}>
            <span aria-hidden="true" className={styles.sidePanelIcon} data-page={page} />
          </button>;
        })}
      </div> : <div aria-label="对象详情" title="对象详情" role="img" className={styles.sidePanelHeading}>
        <span aria-hidden="true" className={styles.sidePanelIcon} data-page="object" />
      </div>}</div>
      <div className={styles.inspectorHeaderActions}>
        <div aria-label="对象上下文导航历史" className={styles.historyControls} role="group">
          <button aria-label="后退到上一个对象" className={styles.historyButton} data-direction="back" disabled={!history.canBack} onClick={history.back} title={history.canBack ? `后退：${history.backLabel}` : undefined} type="button"><WorkbenchIcon name="chevron-left" /></button>
          <button aria-label="前进到下一个对象" className={styles.historyButton} data-direction="forward" disabled={!history.canForward} onClick={history.forward} title={history.canForward ? `前进：${history.forwardLabel}` : undefined} type="button"><WorkbenchIcon name="chevron-right" /></button>
        </div>
        <button aria-label="收起对象上下文" aria-expanded={open} className={styles.inspectorToggle} onClick={onClose} type="button"><WorkbenchIcon name="panel-collapse-right" /></button>
      </div>
    </header>
    <div className={styles.inspectorContent}>
      <div hidden={agentVisible || hasDetail}>{objectContent}</div>
      <div className={styles.agentSideHost} hidden={!agentVisible || hasDetail} ref={agentHostRef} />
      <div className={styles.sideDetailHost} hidden={!hasDetail} ref={detailHostRef} />
    </div>
  </aside>;
}
