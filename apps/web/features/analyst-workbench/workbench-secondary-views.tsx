import {
  getEvent,
  type IssueStatus,
  type WorkbenchSeed,
} from "./analyst-fixture";
import styles from "./analyst-workbench.module.css";
import { formatCaseClock } from "./workbench-presenters";


export function TimelineOverview({
  seed,
  selectedEventId,
  issueStatuses,
  onSelectEvent,
}: {
  seed: WorkbenchSeed;
  selectedEventId: string | null;
  issueStatuses: Record<string, IssueStatus>;
  onSelectEvent: (eventId: string) => void;
}) {
  const selectedEvent = getEvent(seed, selectedEventId) ?? seed.timelineEvents[0];

  if (!selectedEvent) {
    return null;
  }

  return (
    <section
      className={styles.timelinePanel}
      aria-labelledby="timeline-heading"
    >
      <header className={styles.sectionHeader}>
        <div>
          <span>事件序列</span>
          <h2 id="timeline-heading">{seed.caseMeta.timelineTitle}</h2>
        </div>
        <small>{seed.caseMeta.timelineMeta}</small>
      </header>
      <ol className={styles.timelineList}>
        {seed.timelineEvents.map((event) => {
          const selected = event.id === selectedEventId;
          const issue = seed.validationIssues.find((item) =>
            event.issueIds.includes(item.id),
          );
          const issueStatus = issue ? issueStatuses[issue.id] : undefined;
          return (
            <li key={event.id}>
              <button
                aria-pressed={selected}
                data-selected={selected}
                onClick={() => onSelectEvent(event.id)}
                type="button"
              >
                <time
                  aria-label={event.time}
                  className={styles.eventTime}
                  dateTime={event.time}
                  title={event.time}
                >
                  {formatCaseClock(event.time)}
                </time>
                <span className={styles.eventMarker} aria-hidden="true" />
                <span className={styles.eventCopy}>
                  <strong>{event.label}</strong>
                  <small>{event.location}</small>
                  {selected ? <em>{event.summary}</em> : null}
                </span>
                {issue ? (
                  <span
                    className={styles.eventIssue}
                    data-status={issueStatus}
                  >
                    {issue.severity}
                  </span>
                ) : (
                  <span className={styles.eventClear}>通过</span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
