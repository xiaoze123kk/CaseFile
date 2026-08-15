type WorkbenchIconName =
  | "search"
  | "command"
  | "validate"
  | "export"
  | "chevron"
  | "clock"
  | "play"
  | "pause"
  | "close"
  | "reset"
  | "chat"
  | "cursor"
  | "hand";

export function WorkbenchIcon({
  name,
  className,
}: {
  name: WorkbenchIconName;
  className?: string;
}) {
  const paths = {
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
    chat: (
      <>
        <path d="M2.5 4.5h11v6.5h-7L3 14v-3h-.5Z" />
        <path d="M5.5 7h5M5.5 9h3" />
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
