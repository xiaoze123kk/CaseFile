import type { ReactNode } from "react";
import { WorkbenchIcon } from "./workbench-icon";
import { workbenchViewOptions, type WorkbenchView, type WorkspaceMode } from "./workbench-views";
import styles from "./workbench-navigation.module.css";

/** Persistent destinations; the host retains navigation guards and domain state. */
export function WorkbenchNavigation({ mode, view, issuesActive, issueLabel, readOnly, directoryOpen,
  onMode, onView, onIssues, onDirectoryToggle, onCollapse, children,
}: {
  mode: WorkspaceMode; view: WorkbenchView; issuesActive: boolean; issueLabel: string;
  readOnly: boolean; directoryOpen: boolean;
  onMode: (mode: WorkspaceMode) => void; onView: (view: WorkbenchView, label: string) => void;
  onIssues: () => void; onDirectoryToggle: () => void; onCollapse: () => void; children: ReactNode;
}) {
  return <aside aria-label="当前模式导航" className={styles.rail}>
    <header className={styles.heading}>
      <span>分析师工作台</span>
      <button aria-label="收起当前模式导航" onClick={onCollapse} type="button"><WorkbenchIcon name="panel-collapse-left" /></button>
    </header>
    <div className={styles.scroll}>
      <nav aria-label="主要工作模式" role="tablist" className={styles.destinations}>
        <button role="tab" aria-label="工作台" aria-selected={mode === "workbench" || mode === "dossier"} onClick={() => onMode("workbench")} type="button">
          <WorkbenchIcon name="archive" /><span>工作台</span><small>总览</small>
        </button>
        <button role="tab" aria-label="分析" aria-selected={mode === "analysis"} onClick={() => onMode("analysis")} type="button" className={styles.sectionLink}>
          <span>分析</span><small>结构与逻辑</small>
        </button>
      </nav>
      <nav aria-label="分析工具" role="tablist" className={styles.tools}>
        {workbenchViewOptions.filter((option) => option.id !== "compile").map((option) => <button
          key={option.id} role="tab" type="button"
          aria-selected={mode === "analysis" && view === option.id && !issuesActive}
          onClick={() => onView(option.id, option.label)}
        ><span aria-hidden="true" className={styles.illustration} data-view={option.id} /><span>{option.label}</span></button>)}
      </nav>
      <button className={styles.issueLink} aria-pressed={issuesActive} onClick={onIssues} type="button">
        <WorkbenchIcon name="validate" /><span>待处理问题</span><small>{issueLabel}</small>
      </button>
      <nav aria-label="编译工具" role="tablist" className={styles.destinations}>
        <button role="tab" aria-label="编译作品" aria-selected={mode === "compile"} disabled={readOnly} onClick={() => onMode("compile")} type="button">
          <span aria-hidden="true" className={styles.illustration} data-view="compile" /><span>编译作品</span><small>编译中心</small>
        </button>
      </nav>
      <div className={styles.directory}>
        <button aria-controls="workbench-object-directory" aria-expanded={directoryOpen} onClick={onDirectoryToggle} type="button">
          <WorkbenchIcon name="search" /><span>对象档案</span><WorkbenchIcon name={directoryOpen ? "chevron" : "chevron-right"} />
        </button>
        <div id="workbench-object-directory" hidden={!directoryOpen}>{children}</div>
      </div>
    </div>
    <footer className={styles.footer}><kbd>Ctrl K</kbd><span>搜索全部对象与功能</span></footer>
  </aside>;
}
