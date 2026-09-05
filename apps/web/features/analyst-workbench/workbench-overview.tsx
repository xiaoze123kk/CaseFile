import type { IssueStatus, WorkbenchSeed } from "./analyst-fixture";
import { getObject, objectKindLabels } from "./analyst-fixture";
import { directoryObjectKind, type DirectoryObjectKind } from "./workbench-object-directory";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-overview.module.css";

/** Read-only summary of the loaded draft, with actions delegated to the workbench. */
export function WorkbenchOverview({ seed, statuses, validationLabel, onIssues, onIssue, onAnalysis, onBrowse, onAgent, readOnly }: {
  seed: WorkbenchSeed; statuses: Record<string, IssueStatus>; validationLabel: string; readOnly: boolean;
  onIssues: () => void; onIssue: (id: string) => void; onAnalysis: () => void;
  onBrowse: (kind: DirectoryObjectKind) => void; onAgent: () => void;
}) {
  const issues = seed.validationIssues.filter((issue) => {
    const status = statuses[issue.id] ?? "open";
    return status === "open" || status === "patch-ready";
  }).sort((a, b) => ({ S0: 0, error: 0, S1: 1, S2: 2 }[a.severity] - { S0: 0, error: 0, S1: 1, S2: 2 }[b.severity]));
  const kinds: DirectoryObjectKind[] = ["resolution_spec", "entity", "information", "event", "location", "hypothesis"];
  const questions = seed.caseObjects.filter((object) => object.kind === "resolution_spec");
  const questionLabels = questions.length ? questions.map((question) => question.label) : (seed.reasoningGroups ?? []).map((group) => group.question);
  return <section className={styles.overview} aria-label="卷宗总览">
    <header className={styles.intro}>
      <span>当前卷宗 · {seed.caseMeta.revision}</span>
      <h1>从全貌，进入细节。</h1>
      <p>查阅故事资料，核对结构，再处理需要判断的问题。</p>
    </header>
    <div className={styles.content}>
      <section className={styles.focus}>
        <header><WorkbenchIcon name="hypothesis" /><h2>故事的核心问题</h2></header>
        {questionLabels.length ? <ul>{questionLabels.slice(0, 3).map((question, index) => <li key={index}>{question}</li>)}</ul> : <p>从现有事件、信息和人物关系开始核对故事。</p>}
        <button onClick={onAnalysis} type="button">进入结构分析 <span aria-hidden="true">↗</span></button>
      </section>
      <section className={styles.inventory}>
        <header><h2>卷宗内容</h2><span>{seed.caseObjects.length} 个对象</span></header>
        <div>{kinds.map((kind) => <button key={kind} onClick={() => onBrowse(kind)} type="button" aria-label={`浏览${objectKindLabels[kind]}`}>
          <span>{objectKindLabels[kind]}</span><strong>{seed.caseObjects.filter((object) => directoryObjectKind(object.kind) === kind).length}</strong><span aria-hidden="true">›</span>
        </button>)}</div>
      </section>
      <section className={styles.issues}>
        <header><div><h2>需要你判断</h2><span>{validationLabel}</span></div><button onClick={onIssues} type="button">查看全部 →</button></header>
        {issues.length ? <ol>{issues.slice(0, 3).map((issue) => <li key={issue.id}><button onClick={() => onIssue(issue.id)} type="button">
          <span className={styles.issueMark}><WorkbenchIcon name="validate" /></span><span><strong>{issue.title}</strong><small>{getObject(seed, issue.targetObjectId ?? issue.eventId ?? "")?.label ?? (issue.summary !== issue.title ? issue.summary : "打开问题，查看涉及内容与修改建议")}</small></span><span aria-hidden="true">›</span>
        </button></li>)}</ol> : <div className={styles.empty}><WorkbenchIcon name="check-circle" /><p>{validationLabel === "已通过" ? "当前验证已通过。你仍可以进入分析视图核对故事。" : "当前没有可展示的问题。打开问题页查看验证状态。"}</p></div>}
      </section>
      <section className={styles.collaboration}>
        <WorkbenchIcon name="chat" /><h2>一起梳理下一步</h2><p>让卷宗统筹 Agent 分析当前故事，提出可供你审阅的修改建议。</p>
        <button onClick={onAgent} disabled={readOnly} type="button">与 Agent 讨论 <span aria-hidden="true">→</span></button>
      </section>
    </div>
  </section>;
}
