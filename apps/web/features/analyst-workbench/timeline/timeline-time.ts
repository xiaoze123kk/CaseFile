import { utcDay, utcHour, utcMinute, utcSecond } from "d3-time";
import { utcFormat } from "d3-time-format";

import type {
  CaseFileDocument,
  TimelineTemporalPosition,
} from "@/lib/api-client";

export type TimelinePrecision = Extract<
  TimelineTemporalPosition,
  { kind: "exact" }
>["precision"];

const wallClockPattern =
  /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2})(?::(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?)?$/;

const displayClockPattern =
  /^(?:(\d{4})-(\d{2})-(\d{2})T)?(\d{2})(?::(\d{2})(?::\d{2}(?:\.\d{1,6})?)?)?(?:Z|[+-]\d{2}:\d{2})?$/;
const displayDatePattern = /^(\d{4})-(\d{2})-(\d{2})$/;

const wallClockFormats: Record<TimelinePrecision, (date: Date) => string> = {
  day: utcFormat("%Y-%m-%d"),
  hour: utcFormat("%Y-%m-%dT%H"),
  minute: utcFormat("%Y-%m-%dT%H:%M"),
  second: utcFormat("%Y-%m-%dT%H:%M:%S"),
};

export function parseWallClock(value: string): number | null {
  const match = value.match(wallClockPattern);
  if (!match) return null;
  const [, year, month, day, hour = "00", minute = "00", second = "00", fraction = ""] =
    match;
  const millisecond = Number(`0.${fraction || "0"}`) * 1000;
  const result = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
    millisecond,
  );
  const date = new Date(result);
  if (
    date.getUTCFullYear() !== Number(year) ||
    date.getUTCMonth() !== Number(month) - 1 ||
    date.getUTCDate() !== Number(day) ||
    date.getUTCHours() !== Number(hour) ||
    date.getUTCMinutes() !== Number(minute) ||
    date.getUTCSeconds() !== Number(second)
  ) {
    return null;
  }
  return result;
}

export function formatWallClock(value: number, precision: TimelinePrecision) {
  return wallClockFormats[precision](new Date(value));
}

export type TimelineAxisLabelMode = "time" | "date" | "date-time";

export function formatAxisTime(value: number, mode: TimelineAxisLabelMode) {
  const pattern = {
    time: "%H:%M",
    date: "%m-%d",
    "date-time": "%m-%d %H:%M",
  }[mode];
  return utcFormat(pattern)(new Date(value));
}

function displayTimeParts(value: string) {
  const normalized = value.trim();
  const dateMatch = normalized.match(displayDatePattern);
  if (dateMatch) {
    return {
      date: `${dateMatch[2]}-${dateMatch[3]}`,
      clock: null,
    };
  }
  const match = normalized.match(displayClockPattern);
  if (!match) return null;
  return {
    date: match[2] && match[3] ? `${match[2]}-${match[3]}` : null,
    clock: match[5] ? `${match[4]}:${match[5]}` : `${match[4]}时`,
  };
}

function formatDisplayClock(value: string) {
  const parts = displayTimeParts(value);
  return parts?.clock ?? parts?.date ?? null;
}

function formatEventTimeValue(value: string, omitDate = false) {
  const parts = displayTimeParts(value);
  if (!parts) return null;
  if (!parts.date || omitDate) return parts.clock ?? parts.date;
  return parts.clock ? `${parts.date} ${parts.clock}` : parts.date;
}

export function timelineClock(value: string) {
  const normalized = value.trim();
  const approximate = normalized.match(/^约\s+(.+)$/);
  if (approximate) {
    const formatted = formatDisplayClock(approximate[1]);
    return formatted ? `约 ${formatted}` : normalized;
  }
  const range = normalized.split(/\s+[–—]\s+/);
  if (range.length === 2) {
    const start = formatDisplayClock(range[0]);
    const end = formatDisplayClock(range[1]);
    if (start && end) return `${start}–${end}`;
  }
  return formatDisplayClock(normalized) ?? normalized;
}

export function timelineEventTime(value: string) {
  const normalized = value.trim();
  const approximate = normalized.match(/^约\s+(.+)$/);
  if (approximate) {
    const formatted = formatEventTimeValue(approximate[1]);
    return formatted ? `约 ${formatted}` : normalized;
  }
  const range = normalized.split(/\s+[–—]\s+/);
  if (range.length === 2) {
    const startParts = displayTimeParts(range[0]);
    const endParts = displayTimeParts(range[1]);
    if (startParts && endParts) {
      const start = formatEventTimeValue(range[0]);
      const end = formatEventTimeValue(
        range[1],
        Boolean(startParts.date && startParts.date === endParts.date),
      );
      if (start && end) return `${start}–${end}`;
    }
  }
  return formatEventTimeValue(normalized) ?? normalized;
}

export function timelineEventTimeRange(start: string, end: string | null) {
  if (!end || end === start) return timelineEventTime(start);
  return timelineEventTime(`${start} – ${end}`);
}

export function isV2TemporalPosition(
  time: CaseFileDocument["events"][number]["time"] | null | undefined,
): time is TimelineTemporalPosition {
  return Boolean(time && "kind" in time);
}

export function absoluteTemporalBounds(
  time: CaseFileDocument["events"][number]["time"] | null | undefined,
) {
  if (!time) return null;
  if (!("kind" in time)) {
    if (time.precision === "unknown") return null;
    const start = parseWallClock(time.start.replace(/(?:Z|[+-]\d{2}:\d{2})$/, ""));
    const end = time.end
      ? parseWallClock(time.end.replace(/(?:Z|[+-]\d{2}:\d{2})$/, ""))
      : start;
    return start === null || end === null
      ? null
      : { start, end, precision: time.precision === "approximate" ? "minute" : time.precision };
  }
  if (time.kind === "unknown" || time.kind === "relative") return null;
  if (time.kind === "range") {
    const start = parseWallClock(time.start);
    const end = parseWallClock(time.end);
    return start === null || end === null
      ? null
      : { start, end, precision: time.precision };
  }
  const value = parseWallClock(time.value);
  return value === null
    ? null
    : { start: value, end: value, precision: time.precision };
}

function snapDate(value: number, precision: TimelinePrecision) {
  const date = new Date(value);
  if (precision === "day") return utcDay.floor(date).valueOf();
  if (precision === "hour") return utcHour.floor(date).valueOf();
  if (precision === "minute") return utcMinute.floor(date).valueOf();
  return utcSecond.floor(date).valueOf();
}

export function shiftTemporalPosition(
  time: TimelineTemporalPosition,
  nextStart: number,
): TimelineTemporalPosition | null {
  if (time.kind === "unknown" || time.kind === "relative") return null;
  const bounds = absoluteTemporalBounds(time);
  if (!bounds) return null;
  const snappedStart = snapDate(nextStart, time.precision);
  if (time.kind === "range") {
    const duration = Math.max(0, bounds.end - bounds.start);
    return {
      kind: "range",
      start: formatWallClock(snappedStart, time.precision),
      end: formatWallClock(snappedStart + duration, time.precision),
      precision: time.precision,
    };
  }
  return {
    kind: time.kind,
    value: formatWallClock(snappedStart, time.precision),
    precision: time.precision,
  };
}

export function keyboardStep(precision: TimelinePrecision) {
  if (precision === "day") return 24 * 60 * 60 * 1000;
  if (precision === "hour") return 60 * 60 * 1000;
  if (precision === "minute") return 60 * 1000;
  return 1000;
}
