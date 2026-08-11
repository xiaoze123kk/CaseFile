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

export function formatAxisTime(value: number, includeDate: boolean) {
  return utcFormat(includeDate ? "%m-%d %H:%M" : "%H:%M")(new Date(value));
}

export function timelineClock(value: string) {
  const parsed = parseWallClock(value);
  return parsed === null ? value : utcFormat("%H:%M")(new Date(parsed));
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
