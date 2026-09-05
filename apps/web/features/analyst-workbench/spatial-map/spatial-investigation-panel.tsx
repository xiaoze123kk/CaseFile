import type { SpatialInvestigation, SpatialJourney } from "./spatial-investigation-model";
import styles from "./spatial-investigation.module.css";

export function SpatialInvestigationPanel({ model, journeys, personId, activeJourneyId, onPerson, onJourney, onEvent, onLocation }: {
  model: SpatialInvestigation;
  journeys: SpatialJourney[];
  personId: string;
  activeJourneyId: string | null;
  onPerson: (id: string) => void;
  onJourney: (journey: SpatialJourney) => void;
  onEvent: (id: string) => void;
  onLocation: (id: string) => void;
}) {
  const visibleJourneys = journeys.filter((journey) => !personId || journey.personId === personId);
  const missing = model.events.filter((event) => (!personId || event.refs.participantIds.includes(personId))
    && !model.locations.some((location) => location.id === event.refs.locationId));
  const concerns = visibleJourneys.filter((journey) => journey.status !== "recorded");
  return <aside className={styles.panel} aria-label="人物行踪与空间核对">
    <header><span>现场笔记</span><h3>人物行踪</h3><p>从事件记录核对地点与通行时间。</p></header>
    <label className={styles.person}>查看人物<select aria-label="筛选人物行踪" value={personId} onChange={(event) => onPerson(event.target.value)}>
      <option value="">全部人物</option>
      {model.people.map((person) => <option key={person.id} value={person.id}>{person.label}</option>)}
    </select></label>
    {!model.people.length ? <p className={styles.empty}>尚未创建人物；可先查看地点关联的事件。</p> : null}
    <section><h4>需要核对 <b>{concerns.length + missing.length}</b></h4>
      {!concerns.length && !missing.length ? <p className={styles.empty}>当前记录没有待核对项；未记录的行程无法判断。</p> : null}
      {missing.map((event) => <button className={styles.concern} key={event.id} onClick={() => onEvent(event.id)} type="button">
        <strong>{event.refs.locationId ? "事件地点引用失效" : "事件未指定地点"}</strong><span>{event.label}</span><small>打开事件，补充地点 →</small>
      </button>)}
      {concerns.map((journey) => <button className={styles.concern} data-tone={journey.status} aria-pressed={activeJourneyId === journey.id} key={journey.id} onClick={() => onJourney(journey)} type="button">
        <strong>{journey.status === "conflict" ? "通行时间不足" : journey.status === "missing-travel" ? "缺少单向通行时间" : "时间不足以判断行程"}</strong>
        <span>{model.people.find((person) => person.id === journey.personId)?.label} · {journey.from.location} → {journey.to.location}</span>
        <small>{journey.available === null ? "时间不明确或事件重叠，需核对" : `间隔 ${journey.available} 分钟`}{journey.required !== null ? ` · 通行需 ${journey.required} 分钟` : ""}</small>
      </button>)}
    </section>
    {personId ? <section><h4>地点转换 <b>{visibleJourneys.length}</b></h4>
      {visibleJourneys.map((journey) => <button className={styles.journey} key={journey.id} aria-pressed={activeJourneyId === journey.id} onClick={() => onJourney(journey)} type="button">
        <span>{journey.from.location} → {journey.to.location}</span><small>{journey.from.time} → {journey.to.time}</small>
      </button>)}
      {!visibleJourneys.length ? <p className={styles.empty}>该人物尚无可比较的跨地点活动。</p> : null}
    </section> : null}
    <section><h4>地点目录 <b>{model.locations.length}</b></h4>{model.locations.map((location) =>
      <button className={styles.journey} key={location.id} onClick={() => onLocation(location.id)} type="button"><span>{location.label}</span><small>{model.events.filter((event) => event.refs.locationId === location.id).length} 个事件 · 查看现场 →</small></button>)}</section>
  </aside>;
}

export function SpatialActivityStrip({ model, personId, selectedEventId, journey, onEvent, onLocation, onResetJourney }: {
  model: SpatialInvestigation; personId: string; selectedEventId: string | null;
  journey: SpatialJourney | null; onEvent: (id: string) => void; onLocation: (id: string) => void;
  onResetJourney?: () => void;
}) {
  const events = journey ? [journey.from, journey.to] : model.events.filter((event) => !personId || event.refs.participantIds.includes(personId));
  return <section className={styles.activity} aria-label="人物活动记录">
    <div className={styles.activityHeading}><strong>{journey ? "行程核对" : "活动记录"}</strong><small>仅显示事件记录；记录之间位置未知</small>
      {journey && onResetJourney ? <button type="button" onClick={onResetJourney}>返回全部活动</button> : null}
      {journey?.from.refs.locationId ? <button type="button" onClick={() => onLocation(journey.from.refs.locationId!)}>查看出发地通行设定</button> : null}
    </div>
    {journey ? <p className={styles.comparison} data-conflict={journey.status === "conflict"}>
      {journey.available === null ? "当前时间记录不足以确定行程间隔" : `事件间隔 ${journey.available} 分钟`}
      {journey.required === null ? " · 尚未设定此方向的通行时间" : ` · 单向通行 ${journey.required} 分钟`}
      {journey.status === "conflict" ? ` · 少了 ${journey.required! - journey.available!} 分钟` : ""}
    </p> : null}
    <ol>{events.map((event) => <li key={event.id}><button type="button" aria-pressed={selectedEventId === event.id} onClick={() => onEvent(event.id)}>
      <time>{event.timeProjection === "relative-resolved" && event.start ? event.start : event.time}</time><strong>{event.label}</strong><span>{event.location}</span>
    </button></li>)}</ol>
    {!events.length ? <p>尚无活动记录；为事件添加参与人物和地点后，可在这里查看。</p> : null}
  </section>;
}
