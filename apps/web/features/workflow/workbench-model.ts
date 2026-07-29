import type { CaseFileDocument, CaseFileObject } from "@/lib/api-client";

export const WORKBENCH_COLLECTIONS = [
  { key: "resolution_specs", label: "结论规格", shortLabel: "结论" },
  { key: "entities", label: "实体 / 人物", shortLabel: "实体" },
  { key: "relationships", label: "关系", shortLabel: "关系" },
  { key: "locations", label: "地点", shortLabel: "地点" },
  { key: "events", label: "事件", shortLabel: "事件" },
  { key: "information_units", label: "信息单元", shortLabel: "信息" },
  { key: "claims", label: "主张", shortLabel: "主张" },
  { key: "hypotheses", label: "假设", shortLabel: "假设" },
  { key: "reasoning_paths", label: "推理路径", shortLabel: "路径" },
  { key: "constraints", label: "约束", shortLabel: "约束" },
  { key: "structure_locks", label: "结构锁", shortLabel: "结构" },
] as const;

export type WorkbenchCollectionKey =
  (typeof WORKBENCH_COLLECTIONS)[number]["key"];

export type WorkbenchObjectRef = {
  object_type?: string;
  object_id?: string;
};

export interface WorkbenchTime {
  start?: string;
  end?: string | null;
  precision?: "second" | "minute" | "hour" | "day" | "approximate" | "unknown";
}

export interface WorkbenchObject extends CaseFileObject {
  aliases?: string[];
  traits?: string[];
  goals?: string[];
  secrets?: string[];
  capabilities?: string[];
  access_rules?: string[];
  visibility_rules?: string[];
  truth_status?: string;
  time?: WorkbenchTime;
  participant_refs?: WorkbenchObjectRef[];
  location_ref?: WorkbenchObjectRef | null;
  parent_ref?: WorkbenchObjectRef | null;
  adjacency_refs?: WorkbenchObjectRef[];
  from_ref?: WorkbenchObjectRef;
  to_ref?: WorkbenchObjectRef;
  source_event_ref?: WorkbenchObjectRef | null;
}

export interface WorkbenchSelection {
  collection: WorkbenchCollectionKey;
  objectId: string;
}

export interface TimelineEntry {
  event: WorkbenchObject;
  timestamp: number | null;
  dayLabel: string;
  timeLabel: string;
  unknown: boolean;
}

const objectTypeToCollection: Record<string, WorkbenchCollectionKey> = {
  resolution_spec: "resolution_specs",
  entity: "entities",
  relationship: "relationships",
  location: "locations",
  event: "events",
  information_unit: "information_units",
  claim: "claims",
  hypothesis: "hypotheses",
  reasoning_path: "reasoning_paths",
  constraint: "constraints",
  structure_lock: "structure_locks",
};

const collectionToObjectType: Record<WorkbenchCollectionKey, string> = {
  resolution_specs: "resolution_spec",
  entities: "entity",
  relationships: "relationship",
  locations: "location",
  events: "event",
  information_units: "information_unit",
  claims: "claim",
  hypotheses: "hypothesis",
  reasoning_paths: "reasoning_path",
  constraints: "constraint",
  structure_locks: "structure_lock",
};

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "short",
});

const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function collectionObjects(
  document: CaseFileDocument,
  collection: WorkbenchCollectionKey,
) {
  return ((document[collection] ?? []) as WorkbenchObject[]).filter(
    (object) => typeof object.id === "string",
  );
}

export function allWorkbenchObjects(document: CaseFileDocument) {
  return WORKBENCH_COLLECTIONS.flatMap(({ key }) =>
    collectionObjects(document, key).map((object) => ({
      collection: key,
      object,
    })),
  );
}

export function firstWorkbenchSelection(
  document: CaseFileDocument,
): WorkbenchSelection | null {
  const first = allWorkbenchObjects(document)[0];
  return first
    ? { collection: first.collection, objectId: first.object.id }
    : null;
}

export function selectedWorkbenchObject(
  document: CaseFileDocument,
  selection: WorkbenchSelection | null,
) {
  if (!selection) return null;
  return (
    collectionObjects(document, selection.collection).find(
      (object) => object.id === selection.objectId,
    ) ?? null
  );
}

export function objectHeadline(object: WorkbenchObject) {
  return String(
    object.name ??
      object.title ??
      object.statement ??
      object.proposition ??
      "未命名对象",
  );
}

export function objectDescription(object: WorkbenchObject) {
  return typeof object.description === "string" && object.description.trim()
    ? object.description
    : "尚未补充说明。";
}

export function collectionLabel(collection: WorkbenchCollectionKey) {
  return (
    WORKBENCH_COLLECTIONS.find((item) => item.key === collection)?.label ??
    "卷宗对象"
  );
}

export function collectionForObjectType(
  objectType: string | undefined,
): WorkbenchCollectionKey | null {
  return objectType ? objectTypeToCollection[objectType] ?? null : null;
}

export function objectTypeForCollection(collection: WorkbenchCollectionKey) {
  return collectionToObjectType[collection];
}

export function resolveObjectRef(
  document: CaseFileDocument,
  ref: WorkbenchObjectRef | null | undefined,
) {
  if (!ref?.object_id) return null;
  const collection = collectionForObjectType(ref.object_type);
  if (collection) {
    const object = collectionObjects(document, collection).find(
      (candidate) => candidate.id === ref.object_id,
    );
    return object
      ? { collection, object, label: objectHeadline(object) }
      : null;
  }
  const match = allWorkbenchObjects(document).find(
    ({ object }) => object.id === ref.object_id,
  );
  return match
    ? { ...match, label: objectHeadline(match.object) }
    : null;
}

export function timelineEntries(events: WorkbenchObject[]): TimelineEntry[] {
  return events
    .map((event) => {
      const start = event.time?.start;
      const parsed = start ? Date.parse(start) : Number.NaN;
      const unknown =
        event.time?.precision === "unknown" || !Number.isFinite(parsed);
      const startDate = unknown ? null : new Date(parsed);
      const endParsed = event.time?.end
        ? Date.parse(event.time.end)
        : Number.NaN;
      const precision = event.time?.precision;
      const approximation = precision === "approximate" ? "约 " : "";
      const timeLabel = startDate
        ? `${approximation}${timeFormatter.format(startDate)}${
            Number.isFinite(endParsed)
              ? ` — ${timeFormatter.format(new Date(endParsed))}`
              : ""
          }`
        : "时间待定";
      return {
        event,
        timestamp: unknown ? null : parsed,
        dayLabel: startDate ? dateFormatter.format(startDate) : "时间待定",
        timeLabel,
        unknown,
      };
    })
    .sort((left, right) => {
      if (left.unknown !== right.unknown) return left.unknown ? 1 : -1;
      if (left.timestamp !== right.timestamp) {
        return (left.timestamp ?? 0) - (right.timestamp ?? 0);
      }
      return objectHeadline(left.event).localeCompare(
        objectHeadline(right.event),
        "zh-CN",
      );
    });
}

export function stringList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function listFieldValue(value: unknown) {
  return stringList(value).join("\n");
}

export function parseListField(value: string) {
  return [
    ...new Set(
      value
        .split(/\r?\n|，|,/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

export function dateTimeLocalValue(value: string | null | undefined) {
  if (!value) return "";
  const match = value.match(
    /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/,
  );
  return match ? `${match[1]}T${match[2]}` : "";
}

export function dateTimeRequestValue(value: string) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

export const truthStatusOptions = [
  ["canon_true", "已确认事实"],
  ["reported", "他人陈述"],
  ["disputed", "存在争议"],
  ["false_belief", "错误认知"],
  ["unknown", "尚未确定"],
] as const;

export const timePrecisionOptions = [
  ["minute", "精确到分钟"],
  ["hour", "精确到小时"],
  ["day", "精确到日期"],
  ["approximate", "约略时间"],
  ["unknown", "时间未知"],
] as const;
