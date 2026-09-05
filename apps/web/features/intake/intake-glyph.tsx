export type IntakeGlyphName =
  | "archive"
  | "arrow"
  | "check"
  | "compare"
  | "history"
  | "spark"
  | "target";

export function Glyph({ name }: { name: IntakeGlyphName }) {
  if (name === "check") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="m5 12.5 4.2 4.2L19 7" />
      </svg>
    );
  }
  if (name === "compare") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M7 5h12M7 12h8M7 19h12M3 5h.01M3 12h.01M3 19h.01" />
      </svg>
    );
  }
  if (name === "history") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6M4 4v4.6h4.6M12 7.5V12l3 2" />
      </svg>
    );
  }
  if (name === "archive") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M4 7.5h16v12H4zM3 4.5h18v3H3zM8 12h8M9 15.5h6" />
      </svg>
    );
  }
  if (name === "spark") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <path d="M12 2.8 13.8 9l6.2 1.8-6.2 1.8L12 19l-1.8-6.4L4 10.8 10.2 9 12 2.8Z" />
      </svg>
    );
  }
  if (name === "target") {
    return (
      <svg aria-hidden="true" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="8" />
        <circle cx="12" cy="12" r="3" />
        <path d="M12 2v3M22 12h-3M12 22v-3M2 12h3" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M5 12h14M14 6l6 6-6 6" />
    </svg>
  );
}
