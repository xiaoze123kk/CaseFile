type WorkbenchIconName =
  | "search"
  | "command"
  | "validate"
  | "export"
  | "chevron"
  | "chevron-left"
  | "chevron-right"
  | "panel-collapse-right"
  | "panel-expand-left"
  | "panel-collapse-left"
  | "panel-expand-right"
  | "clock"
  | "play"
  | "pause"
  | "close"
  | "reset"
  | "settings"
  | "send"
  | "chat"
  | "archive"
  | "focus"
  | "entity"
  | "document"
  | "event"
  | "location"
  | "hypothesis"
  | "cursor"
  | "hand"
  | "lock"
  | "tag"
  | "lightbulb"
  | "check-circle"
  | "question-circle"
  | "x-circle";

export function WorkbenchIcon({
  name,
  className,
}: {
  name: WorkbenchIconName;
  className?: string;
}) {
  const paths = {
    lock: <><rect x="3.5" y="7" width="9" height="7" rx="1" /><path d="M5.5 7V4a2.5 2.5 0 0 1 5 0v3M8 10v1.5" /></>,
    tag: <><path d="M2 2h5l7 7-5 5-7-7Z" /><circle cx="5" cy="5" r=".7" /></>,
    lightbulb: <><path d="M5.5 11c0-2-2-2.5-2-5a4.5 4.5 0 0 1 9 0c0 2.5-2 3-2 5ZM6 13h4M7 15h2" /></>,
    "check-circle": <><circle cx="8" cy="8" r="6" /><path d="m4.5 8 2.3 2.3 4.7-4.6" /></>,
    "question-circle": <><circle cx="8" cy="8" r="6" /><path d="M6 5.5a2 2 0 1 1 3.5 1.3C8.5 7.5 8 8 8 9M8 11.5h.01" /></>,
    "x-circle": <><circle cx="8" cy="8" r="6" /><path d="m5.5 5.5 5 5m0-5-5 5" /></>,
    search: (
      <>
        <circle cx="7" cy="7" r="4.5" />
        <path d="m10.5 10.5 3.5 3.5" />
      </>
    ),
    command: (
      <>
        <path d="M5 2.5v11M11 2.5v11M2.5 5h11M2.5 11h11" />
        <circle cx="5" cy="5" r="2.5" />
        <circle cx="11" cy="11" r="2.5" />
      </>
    ),
    validate: (
      <>
        <path d="m3 8 3 3 7-7" />
        <path d="M13 8v5H3V3h6" />
      </>
    ),
    export: (
      <>
        <path d="M8 10V2m0 0L5 5m3-3 3 3" />
        <path d="M3 9v4h10V9" />
      </>
    ),
    chevron: <path d="m5 6 3 3 3-3" />,
    "chevron-left": <path d="m10 4-4 4 4 4" />,
    "chevron-right": <path d="m6 4 4 4-4 4" />,
    "panel-collapse-right": (
      <>
        <path d="M13 2v12" />
        <path d="m6 5 3 3-3 3" />
      </>
    ),
    "panel-expand-left": (
      <>
        <path d="M13 2v12" />
        <path d="m10 5-3 3 3 3" />
      </>
    ),
    "panel-collapse-left": (
      <>
        <path d="M3 2v12" />
        <path d="m10 5-3 3 3 3" />
      </>
    ),
    "panel-expand-right": (
      <>
        <path d="M3 2v12" />
        <path d="m6 5 3 3-3 3" />
      </>
    ),
    clock: (
      <>
        <circle cx="8" cy="8" r="5.25" />
        <path d="M8 5.2V8l1.9 1.2" />
      </>
    ),
    play: <path d="m5 3 8 5-8 5Z" />,
    pause: (
      <>
        <path d="M5 3v10M11 3v10" />
      </>
    ),
    close: <path d="m3 3 10 10M13 3 3 13" />,
    reset: (
      <>
        <path d="M3 6a5 5 0 1 1 1 5" />
        <path d="M3 2v4h4" />
      </>
    ),
    settings: (
      <>
        <path d="M2.5 4h3M8.5 4h5M2.5 8h6M11.5 8h2M2.5 12h1.5M7 12h6.5" />
        <circle cx="7" cy="4" r="1.5" />
        <circle cx="10" cy="8" r="1.5" />
        <circle cx="5.5" cy="12" r="1.5" />
      </>
    ),
    send: (
      <>
        <path d="m2.5 7.4 11-4.6-4.6 11-1.6-4.1Z" />
        <path d="m7.3 9.7 2.8-2.8" />
      </>
    ),
    chat: (
      <>
        <path d="M2.5 4.5h11v6.5h-7L3 14v-3h-.5Z" />
        <path d="M5.5 7h5M5.5 9h3" />
      </>
    ),
    archive: (
      <>
        <path d="M3 3.5h4l1.1 1.4H13v8.2H3Z" />
        <path d="M3 6.5h10M6 9h4" />
      </>
    ),
    focus: (
      <>
        <circle cx="8" cy="8" r="4.6" />
        <circle cx="8" cy="8" r="1.5" />
        <path d="M8 1.7v2M8 12.3v2M1.7 8h2M12.3 8h2" />
      </>
    ),
    entity: (
      <>
        <circle cx="8" cy="5.2" r="2.3" />
        <path d="M3.8 13c.4-2.6 1.8-4 4.2-4s3.8 1.4 4.2 4" />
        <path d="M3.9 6.6a1.7 1.7 0 1 1 1.3-3M12.1 6.6a1.7 1.7 0 1 0-1.3-3" />
      </>
    ),
    document: (
      <>
        <path d="M4 2.2h5l3 3V14H4Z" />
        <path d="M9 2.2v3h3M6 8h4M6 10.5h4" />
      </>
    ),
    event: (
      <>
        <circle cx="4" cy="4" r="1.5" />
        <circle cx="12" cy="8" r="1.5" />
        <circle cx="4" cy="12" r="1.5" />
        <path d="M5.5 4c3 0 2.2 4 5 4M5.5 12c3 0 2.2-4 5-4" />
      </>
    ),
    location: (
      <>
        <path d="M8 14s4-4.1 4-7.4a4 4 0 1 0-8 0C4 9.9 8 14 8 14Z" />
        <circle cx="8" cy="6.5" r="1.35" />
      </>
    ),
    hypothesis: (
      <>
        <path d="M6 2.2h4M7 2.2v3L3.9 11a1.8 1.8 0 0 0 1.6 2.8h5a1.8 1.8 0 0 0 1.6-2.8L9 5.2v-3" />
        <path d="M5.5 9.5h5M6.6 7.5h2.8" />
      </>
    ),
    cursor: <path d="m3.5 2.5 9 5.2-4.3 1.2-1.2 4.3Z" />,
    hand: (
      <>
        <path d="M5.5 8.5V4a1.4 1.4 0 0 1 2.8 0v4M8.3 8V3.4a1.4 1.4 0 0 1 2.8 0V8M11.1 8.5V5.8a1.4 1.4 0 0 1 2.8 0v3.7c0 2.8-2.2 4.5-4.4 4.5S5.8 12 5.2 10L4.5 8.6a1.25 1.25 0 0 1 2.1-1.3L7 8" />
      </>
    ),
  } as const;

  return (
    <svg
      aria-hidden="true"
      className={className}
      data-icon={name}
      fill="none"
      viewBox="0 0 16 16"
    >
      <g
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.35"
      >
        {paths[name]}
      </g>
    </svg>
  );
}
