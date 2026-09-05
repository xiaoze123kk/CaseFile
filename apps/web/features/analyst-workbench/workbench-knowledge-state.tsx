import type { DetailKnowledgeState, DetailReference } from "./workbench-object-detail-model";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-knowledge-state.module.css";

const categories = [
  { key: "known", label: "已确认", icon: "check-circle", description: "角色在此时点已知的信息", empty: "暂无已知信息" },
  { key: "believes", label: "推测", icon: "question-circle", description: "角色当前相信的判断", empty: "暂无推测" },
  { key: "falseBeliefs", label: "误判", icon: "x-circle", description: "已标记的错误认知", empty: "暂无错误认知" },
] as const;

function KnowledgeItem({ reference, onSelectObject }: {
  reference: DetailReference;
  onSelectObject: (id: string) => void;
}) {
  const content = <>
    <span aria-hidden="true" className={styles.dot} />
    <span className={styles.itemLabel}>{reference.label}</span>
    <small>{reference.kindLabel}</small>
    {reference.selectable && !reference.missing ? <WorkbenchIcon name="chevron-right" /> : null}
  </>;
  return <li className={styles.item} data-missing={reference.missing}>
    {reference.selectable && !reference.missing
      ? <button type="button" aria-label={`查看${reference.kindLabel}“${reference.label}”`} onClick={() => onSelectObject(reference.id)}>{content}</button>
      : <span className={styles.staticItem}>{content}</span>}
  </li>;
}

export function KnowledgeStateList({ states, onSelectObject }: {
  states: DetailKnowledgeState[];
  onSelectObject: (id: string) => void;
}) {
  return <div className={styles.timeline} role="group" aria-label="知识状态时间线">
    {states.map((state, index) => <section className={styles.sheet} key={`${state.asOf.id}:${index}`} aria-label={`截至${state.asOf.label}的知识状态`}>
      <header className={styles.heading}>
        <h4><span className={styles.titleIcon}><WorkbenchIcon name="lightbulb" /></span>知识状态</h4>
        <div className={styles.asOf}>
          <span>截至</span>
          {state.asOf.selectable && !state.asOf.missing
            ? <button type="button" aria-label={`跳转查看截至事件“${state.asOf.label}”`} onClick={() => onSelectObject(state.asOf.id)}>{state.asOf.label}<small>事件</small><WorkbenchIcon name="chevron-right" /></button>
            : <span data-missing={state.asOf.missing}>{state.asOf.label}</span>}
        </div>
      </header>
      <ul className={styles.stats} aria-label="认知分类统计">
        {categories.map((category) => <li key={category.key} data-tone={category.key} aria-label={`${category.label} ${state[category.key].length} 项`}>
          <WorkbenchIcon name={category.icon} /><span>{category.label}</span><b>{state[category.key].length}</b>
        </li>)}
      </ul>
      <div className={styles.groups}>
        {categories.map((category) => <section key={category.key} className={styles.group} data-tone={category.key} aria-label={category.label}>
          <header><h5><WorkbenchIcon name={category.icon} />{category.label}</h5><p>{category.description}</p></header>
          <ul>{state[category.key].length
            ? state[category.key].map((reference) => <KnowledgeItem key={reference.id} reference={reference} onSelectObject={onSelectObject} />)
            : <li className={styles.empty}><span aria-hidden="true" className={styles.dot} />{category.empty}<span aria-hidden="true">—</span></li>}
          </ul>
        </section>)}
      </div>
    </section>)}
  </div>;
}
