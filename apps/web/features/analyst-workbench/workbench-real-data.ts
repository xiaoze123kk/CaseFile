import type { ObjectRef } from "@casefile/contracts";
import type {
  CaseFileDocument,
  WorkbenchValidationView,
} from "@/lib/api-client";

import type {
  ValidationIssue,
  WorkbenchReasoningGroup,
  WorkbenchSeed,
} from "./analyst-fixture";
import {
  classificationLabel,
  confirmationStatusLabel,
  creatorDescription,
  creatorLabel,
  creatorText,
  objectSubtypeLabel,
  reasoningOperationLabel,
} from "./workbench-presenters";
import {
  buildFixtureSpatialModel,
  buildWorkbenchSpatialModel,
} from "./workbench-spatial-model";
import {
  formatWallClock,
  parseWallClock,
  timelineClock,
} from "./timeline/timeline-time";
import type {
  WorkbenchCaseMeta,
  WorkbenchCaseObject,
  WorkbenchContractObject,
  WorkbenchGraphEdge,
  WorkbenchGraphEdgeKind,
  WorkbenchGraphNode,
  WorkbenchMapModel,
  WorkbenchModel,
  WorkbenchObjectKind,
  WorkbenchReasoningOutcome,
  WorkbenchReasoningPath,
  WorkbenchReasoningStep,
  WorkbenchReferenceKind,
  WorkbenchTimelineEvent,
} from "./workbench-real-data-types";

export type * from "./workbench-real-data-types";

type ContractHypothesis = CaseFileDocument["hypotheses"][number];

interface ParsedReference {
  id: string;
  kind: WorkbenchReferenceKind;
}

interface ReferenceCatalogEntry extends ParsedReference {
  label: string;
}

const objectKindOrder: WorkbenchObjectKind[] = [
  "entity",
  "information",
  "event",
  "location",
  "hypothesis",
];

const referenceKindOrder: WorkbenchReferenceKind[] = [
  "entity",
  "person",
  "information_unit",
  "information",
  "evidence",
  "event",
  "location",
  "hypothesis",
  "claim",
  "resolution_spec",
  "source_fragment",
  "casefile",
  "relationship",
  "reasoning_path",
  "constraint",
  "structure_lock",
  "unknown",
];

const emptyDrawer: WorkbenchModel["drawer"] = {
  audioTitle: "暂无真实来源录音",
  audioDuration: "—",
  audioProgress: "0 / 0",
  keyTime: "—",
  keyExcerpt: "",
  transcript: "当前工作稿尚未接入来源抽屉。",
  logs: [],
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asReferenceKind(value: unknown): WorkbenchReferenceKind {
  return typeof value === "string" &&
    referenceKindOrder.includes(value as WorkbenchReferenceKind)
    ? (value as WorkbenchReferenceKind)
    : "unknown";
}

function referenceKindForDirectoryKind(
  kind: WorkbenchObjectKind | undefined,
): WorkbenchReferenceKind {
  if (!kind) {
    return "unknown";
  }
  return kind === "information" ? "information_unit" : kind;
}

function readReference(ref: ObjectRef | null | undefined): ParsedReference | null {
  if (!isRecord(ref) || typeof ref.object_id !== "string") {
    return null;
  }
  return {
    id: ref.object_id,
    kind: asReferenceKind(ref.object_type),
  };
}

function readReferenceIds(refs: ObjectRef[]): string[] {
  return uniqueStrings(
    refs.flatMap((ref) => {
      const parsed = readReference(ref);
      return parsed ? [parsed.id] : [];
    }),
  );
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))];
}

function parseTime(value: string): string | null {
  const match = value.match(
    /^(\d{4}-\d{2}-\d{2})(?:T(\d{2})(?::(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?)?(?:Z|[+-]\d{2}:\d{2})?$/,
  );
  if (!match) return null;
  const [, date, hour = "00", minute = "00", second = "00", fraction = ""] = match;
  return `${date}T${hour}:${minute}:${second}.${fraction.padEnd(6, "0")}`;
}

function temporalSummary(
  time: CaseFileDocument["events"][number]["time"],
): {
  label: string;
  start: string | null;
  end: string | null;
  precision: string;
  sortKey: string | null;
} {
  if (!("kind" in time)) {
    const start = time.precision === "unknown" ? null : time.start;
    return {
      label: start ?? "时间未定",
      start,
      end: time.end,
      precision: time.precision,
      sortKey: start ? parseTime(start) : null,
    };
  }
  if (time.kind === "unknown") {
    return {
      label: "时间未定",
      start: null,
      end: null,
      precision: "unknown",
      sortKey: null,
    };
  }
  if (time.kind === "relative") {
    const relation = {
      before: "之前",
      after: "之后",
      same_time: "同时",
    }[time.relation];
    const offset = time.offset_minutes === null ? "" : ` ${time.offset_minutes} 分钟`;
    return {
      label: `相对 ${time.anchor_event_ref.object_id}${offset}${relation}`,
      start: null,
      end: null,
      precision: "relative",
      sortKey: null,
    };
  }
  if (time.kind === "range") {
    return {
      label: `${time.start} – ${time.end}`,
      start: time.start,
      end: time.end,
      precision: time.precision,
      sortKey: parseTime(time.start),
    };
  }
  return {
    label: time.kind === "approximate" ? `约 ${time.value}` : time.value,
    start: time.value,
    end: null,
    precision: time.precision,
    sortKey: parseTime(time.value),
  };
}

interface ResolvedTimelineBounds {
  start: string;
  end: string | null;
  sortKey: string;
}

function resolvedTemporalBounds(
  events: CaseFileDocument["events"],
): Map<string, ResolvedTimelineBounds> {
  const eventById = new Map(events.map((event) => [event.id, event]));
  const resolved = new Map<string, ResolvedTimelineBounds>();
  const resolving = new Set<string>();

  const resolve = (eventId: string): ResolvedTimelineBounds | null => {
    const cached = resolved.get(eventId);
    if (cached) return cached;
    if (resolving.has(eventId)) return null;
    const event = eventById.get(eventId);
    if (!event) return null;
    const temporal = temporalSummary(event.time);
    if (temporal.start && temporal.sortKey) {
      const value = {
        start: temporal.start,
        end: temporal.end,
        sortKey: temporal.sortKey,
      };
      resolved.set(eventId, value);
      return value;
    }
    if (!("kind" in event.time) || event.time.kind !== "relative") return null;
    resolving.add(eventId);
    const anchorRef = readReference(event.time.anchor_event_ref);
    const anchor = anchorRef?.kind === "event" ? resolve(anchorRef.id) : null;
    resolving.delete(eventId);
    if (!anchor || event.time.offset_minutes === null) return null;
    const anchorStart = parseWallClock(anchor.start);
    const anchorEnd = parseWallClock(anchor.end ?? anchor.start);
    if (anchorStart === null || anchorEnd === null) return null;
    const offsetMilliseconds = event.time.offset_minutes * 60 * 1000;
    const value =
      event.time.relation === "before"
        ? anchorStart - offsetMilliseconds
        : event.time.relation === "after"
          ? anchorEnd + offsetMilliseconds
          : anchorStart;
    const projected = formatWallClock(value, "second");
    const result = { start: projected, end: null, sortKey: projected };
    resolved.set(eventId, result);
    return result;
  };

  for (const event of events) resolve(event.id);
  return resolved;
}

function confidenceText(value: number | null): string {
  return value === null ? "置信度未标注" : `置信度 ${Math.round(value * 100)}%`;
}

function metadataForObject(object: WorkbenchContractObject, kind: WorkbenchReferenceKind) {
  return {
    description: creatorDescription(object.description, kind),
    confidence: object.confidence,
    confirmationStatus: object.confirmation_status,
    revision: object.revision,
    sourceRefIds: readReferenceIds(object.source_refs),
  };
}

function objectMeta(object: WorkbenchContractObject): string {
  return `${confidenceText(object.confidence)} · ${confirmationStatusLabel(object.confirmation_status)}`;
}

function buildRelatedEventIds(caseFile: CaseFileDocument): Map<string, Set<string>> {
  const result = new Map<string, Set<string>>();
  const directoryIds = new Set(
    [
      ...caseFile.entities,
      ...caseFile.information_units,
      ...caseFile.events,
      ...caseFile.locations,
      ...caseFile.hypotheses,
    ].map((object) => object.id),
  );
  const link = (objectId: string | null, eventId: string) => {
    if (!objectId || !directoryIds.has(objectId)) {
      return;
    }
    const ids = result.get(objectId) ?? new Set<string>();
    ids.add(eventId);
    result.set(objectId, ids);
  };

  for (const event of caseFile.events) {
    link(event.id, event.id);
    const relatedIds = [
      ...readReferenceIds(event.participant_refs),
      readReference(event.location_ref)?.id ?? null,
      ...readReferenceIds(event.cause_refs),
      ...readReferenceIds(event.effect_refs),
      ...readReferenceIds(event.observed_by_refs),
      ...readReferenceIds(event.source_refs),
    ];
    for (const objectId of relatedIds) {
      link(objectId, event.id);
    }
  }
  for (const information of caseFile.information_units) {
    const eventId = readReference(information.source_event_ref)?.id ?? null;
    if (eventId) {
      link(information.id, eventId);
    }
  }
  return result;
}

function buildCaseObjects(caseFile: CaseFileDocument): WorkbenchCaseObject[] {
  const relatedEvents = buildRelatedEventIds(caseFile);
  const related = (id: string) => [...(relatedEvents.get(id) ?? [])].sort();

  return [
    ...caseFile.entities.map((entity, index): WorkbenchCaseObject => ({
      id: entity.id,
      kind: "entity",
      label: creatorLabel(entity.name, {
        kind: "entity",
        index,
        description: entity.description,
      }),
      code: objectSubtypeLabel(entity.entity_type),
      meta: objectMeta(entity),
      subtype: entity.entity_type,
      ...metadataForObject(entity, "entity"),
      relatedEventIds: related(entity.id),
      source: entity,
    })),
    ...caseFile.information_units.map((information, index): WorkbenchCaseObject => ({
      id: information.id,
      kind: "information",
      label: creatorLabel(information.title, {
        kind: "information_unit",
        index,
        description: information.description,
      }),
      code: `${objectSubtypeLabel(information.information_type)} · ${classificationLabel(information.classification)}`,
      meta: objectMeta(information),
      subtype: information.information_type,
      ...metadataForObject(information, "information_unit"),
      relatedEventIds: related(information.id),
      source: information,
    })),
    ...caseFile.events.map((event, index): WorkbenchCaseObject => ({
      id: event.id,
      kind: "event",
      label: creatorLabel(event.title, {
        kind: "event",
        index,
        description: event.description,
      }),
      code: `${objectSubtypeLabel(event.truth_status)} · ${objectSubtypeLabel(temporalSummary(event.time).precision)}`,
      meta: objectMeta(event),
      subtype: event.truth_status,
      ...metadataForObject(event, "event"),
      relatedEventIds: related(event.id),
      source: event,
    })),
    ...caseFile.locations.map((location, index): WorkbenchCaseObject => ({
      id: location.id,
      kind: "location",
      label: creatorLabel(location.name, {
        kind: "location",
        index,
        description: location.description,
      }),
      code: objectSubtypeLabel(location.spatial_position?.coordinate_system ?? "topology"),
      meta: objectMeta(location),
      subtype: location.spatial_position?.coordinate_system ?? "topology",
      ...metadataForObject(location, "location"),
      relatedEventIds: related(location.id),
      source: location,
    })),
    ...caseFile.hypotheses.map((hypothesis, index): WorkbenchCaseObject => ({
      id: hypothesis.id,
      kind: "hypothesis",
      label: creatorLabel(hypothesis.title, {
        kind: "hypothesis",
        index,
        description: hypothesis.description,
      }),
      code: objectSubtypeLabel(hypothesis.status),
      meta: objectMeta(hypothesis),
      subtype: hypothesis.status,
      ...metadataForObject(hypothesis, "hypothesis"),
      relatedEventIds: related(hypothesis.id),
      source: hypothesis,
    })),
  ];
}

function buildTimeline(
  caseFile: CaseFileDocument,
  objects: WorkbenchCaseObject[],
  validationIssues: ValidationIssue[],
): WorkbenchTimelineEvent[] {
  const objectIds = new Set(objects.map((object) => object.id));
  const locationNames = new Map(
    caseFile.locations.map((location, index) => [
      location.id,
      creatorLabel(location.name, {
        kind: "location",
        index,
        description: location.description,
      }),
    ]),
  );
  const temporalBounds = resolvedTemporalBounds(caseFile.events);

  return caseFile.events
    .map((event, originalIndex) => {
      const participantIds = readReferenceIds(event.participant_refs);
      const locationId = readReference(event.location_ref)?.id ?? null;
      const causeIds = readReferenceIds(event.cause_refs);
      const effectIds = readReferenceIds(event.effect_refs);
      const observerIds = readReferenceIds(event.observed_by_refs);
      const sourceIds = readReferenceIds(event.source_refs);
      const relatedObjectIds = uniqueStrings([
        event.id,
        ...participantIds,
        locationId,
        ...causeIds,
        ...effectIds,
        ...observerIds,
        ...sourceIds,
      ]).filter((id) => objectIds.has(id));
      const temporal = temporalSummary(event.time);
      const projection = temporalBounds.get(event.id);
      const isRelative = "kind" in event.time && event.time.kind === "relative";
      return {
        event: {
          id: event.id,
          time: temporal.label,
          label: creatorLabel(event.title, {
            kind: "event",
            index: originalIndex,
            description: event.description,
          }),
          location: locationId ? locationNames.get(locationId) ?? locationId : "未指定地点",
          summary: creatorDescription(event.description, "event"),
          relatedObjectIds,
          issueIds: validationIssues
            .filter((issue) => issue.eventId === event.id)
            .map((issue) => issue.id),
          start: projection?.start ?? temporal.start,
          end: projection?.end ?? temporal.end,
          precision: temporal.precision,
          truthStatus: event.truth_status,
          sortKey: projection?.sortKey ?? temporal.sortKey,
          timeProjection: isRelative
            ? projection
              ? "relative-resolved"
              : "unresolved"
            : temporal.sortKey
              ? "absolute"
              : "unresolved",
          refs: {
            participantIds,
            locationId,
            causeIds,
            effectIds,
            observerIds,
            sourceIds,
          },
          source: event,
        } satisfies WorkbenchTimelineEvent,
        originalIndex,
      };
    })
    .sort((left, right) => {
      if (left.event.sortKey === null && right.event.sortKey !== null) {
        return 1;
      }
      if (left.event.sortKey !== null && right.event.sortKey === null) {
        return -1;
      }
      if (left.event.sortKey !== null && right.event.sortKey !== null) {
        const byTime = left.event.sortKey.localeCompare(right.event.sortKey);
        if (byTime !== 0) {
          return byTime;
        }
      }
      return left.originalIndex - right.originalIndex;
    })
    .map(({ event }) => event);
}

function buildValidationIssues(
  validation: WorkbenchValidationView | null,
): ValidationIssue[] {
  if (validation?.status !== "failed") {
    return [];
  }
  return validation.issues.map((issue) => {
    const objectRef = issue.target.object_ref;
    const eventId = objectRef?.object_type === "event" ? objectRef.object_id : null;
    return {
      id: issue.issue_id,
      severity: issue.severity,
      title: issue.message,
      summary: issue.message,
      eventId,
      rule: `${issue.code} · ${issue.path}`,
      evidenceIds: [],
      beforeKnowledge: "",
      eventClaim: "",
      afterKnowledge: "",
      patchBefore: "",
      patchAfter: "",
      source: "validator",
      targetObjectId: objectRef?.object_id ?? null,
      targetObjectType: objectRef?.object_type ?? null,
      fieldPath: issue.target.field_path,
    };
  });
}

function buildReferenceCatalog(caseFile: CaseFileDocument): Map<string, ReferenceCatalogEntry> {
  const entries: ReferenceCatalogEntry[] = [
    { id: caseFile.casefile_id, kind: "casefile", label: creatorLabel(caseFile.title, { kind: "casefile", index: 0, description: caseFile.title }) },
    ...caseFile.resolution_specs.map((item, index) => ({
      id: item.id,
      kind: "resolution_spec" as const,
      label: creatorLabel(item.title, { kind: "resolution_spec", index, description: item.description }),
    })),
    ...caseFile.entities.map((item, index) => ({
      id: item.id,
      kind: "entity" as const,
      label: creatorLabel(item.name, { kind: "entity", index, description: item.description }),
    })),
    ...caseFile.relationships.map((item, index) => ({
      id: item.id,
      kind: "relationship" as const,
      label: creatorLabel(item.title, { kind: "relationship", index, description: item.description }),
    })),
    ...caseFile.locations.map((item, index) => ({
      id: item.id,
      kind: "location" as const,
      label: creatorLabel(item.name, { kind: "location", index, description: item.description }),
    })),
    ...caseFile.events.map((item, index) => ({
      id: item.id,
      kind: "event" as const,
      label: creatorLabel(item.title, { kind: "event", index, description: item.description }),
    })),
    ...caseFile.information_units.map((item, index) => ({
      id: item.id,
      kind: "information_unit" as const,
      label: creatorLabel(item.title, { kind: "information_unit", index, description: item.description }),
    })),
    ...caseFile.claims.map((item, index) => ({
      id: item.id,
      kind: "claim" as const,
      label: creatorLabel(item.title, { kind: "claim", index, description: item.description }),
    })),
    ...caseFile.hypotheses.map((item, index) => ({
      id: item.id,
      kind: "hypothesis" as const,
      label: creatorLabel(item.title, { kind: "hypothesis", index, description: item.description }),
    })),
    ...caseFile.reasoning_paths.map((item, index) => ({
      id: item.id,
      kind: "reasoning_path" as const,
      label: creatorLabel(item.title, { kind: "reasoning_path", index, description: item.description }),
    })),
    ...caseFile.constraints.map((item, index) => ({
      id: item.id,
      kind: "constraint" as const,
      label: creatorLabel(item.title, { kind: "constraint", index, description: item.description }),
    })),
    ...caseFile.structure_locks.map((item, index) => ({
      id: item.id,
      kind: "structure_lock" as const,
      label: creatorLabel(item.title, { kind: "structure_lock", index, description: item.description }),
    })),
  ];
  return new Map(entries.map((entry) => [entry.id, entry]));
}

function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function layoutGraphNodes(nodes: WorkbenchGraphNode[]): WorkbenchGraphNode[] {
  const sorted = [...nodes].sort((left, right) => {
    const kindDifference =
      referenceKindOrder.indexOf(left.kind) - referenceKindOrder.indexOf(right.kind);
    return kindDifference || left.id.localeCompare(right.id);
  });
  if (sorted.length === 0) {
    return [];
  }
  const columns = Math.max(1, Math.ceil(Math.sqrt(sorted.length * 1.5)));
  const rows = Math.ceil(sorted.length / columns);
  return sorted.map((node, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    return {
      ...node,
      x: columns === 1 ? 50 : 8 + (column / (columns - 1)) * 84,
      y: rows === 1 ? 50 : 8 + (row / (rows - 1)) * 84,
    };
  });
}

function buildRelationshipGraph(
  caseFile: CaseFileDocument,
  objects: WorkbenchCaseObject[],
): { nodes: WorkbenchGraphNode[]; edges: WorkbenchGraphEdge[] } {
  const catalog = buildReferenceCatalog(caseFile);
  const directoryIds = new Set(objects.map((object) => object.id));
  const nodes = new Map<string, WorkbenchGraphNode>();
  const edges = new Map<string, WorkbenchGraphEdge>();

  const ensureNode = (reference: ParsedReference | null) => {
    if (!reference) {
      return;
    }
    const catalogEntry = catalog.get(reference.id);
    nodes.set(reference.id, {
      objectId: reference.id,
      id: reference.id,
      kind: catalogEntry?.kind ?? reference.kind,
      label: catalogEntry?.label ?? reference.id,
      x: 0,
      y: 0,
      directoryObjectId: directoryIds.has(reference.id) ? reference.id : null,
    });
  };

  const addEdge = (input: {
    identity: string;
    from: ParsedReference | null;
    to: ParsedReference | null;
    label: string;
    kind: WorkbenchGraphEdgeKind;
    direction?: WorkbenchGraphEdge["direction"];
    sourceObjectId?: string | null;
  }) => {
    if (!input.from || !input.to || input.from.id === input.to.id) {
      return;
    }
    ensureNode(input.from);
    ensureNode(input.to);
    if (!edges.has(input.identity)) {
      edges.set(input.identity, {
        id: `edge_${hashString(input.identity).toString(36)}`,
        from: input.from.id,
        to: input.to.id,
        label: input.label,
        kind: input.kind,
        direction: input.direction ?? "directed",
        sourceObjectId: input.sourceObjectId ?? null,
      });
    }
  };

  for (const object of objects) {
    ensureNode({
      id: object.id,
      kind: referenceKindForDirectoryKind(object.kind),
    });
  }

  for (const relationship of caseFile.relationships) {
    addEdge({
      identity: `relationship:${relationship.id}`,
      from: readReference(relationship.from_ref),
      to: readReference(relationship.to_ref),
      label: creatorLabel(relationship.title, {
        kind: "relationship",
        index: caseFile.relationships.findIndex((item) => item.id === relationship.id),
        description: relationship.description,
      }),
      kind: "relationship",
      direction: relationship.direction,
      sourceObjectId: relationship.id,
    });
  }

  for (const event of caseFile.events) {
    const eventRef = { id: event.id, kind: "event" as const };
    for (const ref of event.participant_refs) {
      const parsed = readReference(ref);
      addEdge({
        identity: `event:${event.id}:participant:${parsed?.id ?? "invalid"}`,
        from: eventRef,
        to: parsed,
        label: "参与",
        kind: "event_participant",
        sourceObjectId: event.id,
      });
    }
    const location = readReference(event.location_ref);
    addEdge({
      identity: `event:${event.id}:location:${location?.id ?? "none"}`,
      from: eventRef,
      to: location,
      label: "发生地点",
      kind: "event_location",
      sourceObjectId: event.id,
    });
    for (const ref of event.cause_refs) {
      const parsed = readReference(ref);
      addEdge({
        identity: `event:${event.id}:cause:${parsed?.id ?? "invalid"}`,
        from: parsed,
        to: eventRef,
        label: "导致",
        kind: "event_cause",
        sourceObjectId: event.id,
      });
    }
    for (const ref of event.effect_refs) {
      const parsed = readReference(ref);
      addEdge({
        identity: `event:${event.id}:effect:${parsed?.id ?? "invalid"}`,
        from: eventRef,
        to: parsed,
        label: "影响",
        kind: "event_effect",
        sourceObjectId: event.id,
      });
    }
    for (const ref of event.observed_by_refs) {
      const parsed = readReference(ref);
      addEdge({
        identity: `event:${event.id}:observer:${parsed?.id ?? "invalid"}`,
        from: eventRef,
        to: parsed,
        label: "被观察",
        kind: "event_observer",
        sourceObjectId: event.id,
      });
    }
    for (const ref of event.source_refs) {
      const parsed = readReference(ref);
      addEdge({
        identity: `event:${event.id}:source:${parsed?.id ?? "invalid"}`,
        from: parsed,
        to: eventRef,
        label: "来源",
        kind: "source",
        sourceObjectId: event.id,
      });
    }
  }

  for (const location of caseFile.locations) {
    const locationRef = { id: location.id, kind: "location" as const };
    const parent = readReference(location.parent_ref);
    addEdge({
      identity: `location:${location.id}:parent:${parent?.id ?? "none"}`,
      from: parent,
      to: locationRef,
      label: "包含",
      kind: "location_parent",
      sourceObjectId: location.id,
    });
    for (const ref of location.adjacency_refs) {
      const adjacent = readReference(ref);
      const pair = [location.id, adjacent?.id ?? "invalid"].sort().join(":");
      addEdge({
        identity: `location:adjacency:${pair}`,
        from: locationRef,
        to: adjacent,
        label: "相邻",
        kind: "location_adjacency",
        direction: "undirected",
        sourceObjectId: location.id,
      });
    }
    for (const travelTime of location.travel_times) {
      const target = readReference(travelTime.to_ref);
      addEdge({
        identity: `location:${location.id}:travel:${target?.id ?? "invalid"}`,
        from: locationRef,
        to: target,
        label: `${travelTime.minutes} 分钟`,
        kind: "location_travel",
        sourceObjectId: location.id,
      });
    }
    for (const ref of location.source_refs) {
      const source = readReference(ref);
      addEdge({
        identity: `location:${location.id}:source:${source?.id ?? "invalid"}`,
        from: source,
        to: locationRef,
        label: "来源",
        kind: "source",
        sourceObjectId: location.id,
      });
    }
  }

  for (const information of caseFile.information_units) {
    const informationRef = {
      id: information.id,
      kind: "information_unit" as const,
    };
    const sourceEvent = readReference(information.source_event_ref);
    addEdge({
      identity: `information:${information.id}:event:${sourceEvent?.id ?? "none"}`,
      from: sourceEvent,
      to: informationRef,
      label: "产生信息",
      kind: "information_source_event",
      sourceObjectId: information.id,
    });
    for (const ref of information.supports_claim_refs) {
      const target = readReference(ref);
      addEdge({
        identity: `information:${information.id}:support:${target?.id ?? "invalid"}`,
        from: informationRef,
        to: target,
        label: "支持",
        kind: "information_support",
        sourceObjectId: information.id,
      });
    }
    for (const ref of information.refutes_claim_refs) {
      const target = readReference(ref);
      addEdge({
        identity: `information:${information.id}:refute:${target?.id ?? "invalid"}`,
        from: informationRef,
        to: target,
        label: "反驳",
        kind: "information_refute",
        sourceObjectId: information.id,
      });
    }
    for (const ref of information.availability.perspective_refs) {
      const target = readReference(ref);
      addEdge({
        identity: `information:${information.id}:perspective:${target?.id ?? "invalid"}`,
        from: informationRef,
        to: target,
        label: "可见于",
        kind: "information_perspective",
        sourceObjectId: information.id,
      });
    }
    for (const ref of information.source_refs) {
      const source = readReference(ref);
      addEdge({
        identity: `information:${information.id}:source:${source?.id ?? "invalid"}`,
        from: source,
        to: informationRef,
        label: "来源",
        kind: "source",
        sourceObjectId: information.id,
      });
    }
  }

  for (const hypothesis of caseFile.hypotheses) {
    const hypothesisRef = { id: hypothesis.id, kind: "hypothesis" as const };
    const resolution = readReference(hypothesis.target_resolution_ref);
    addEdge({
      identity: `hypothesis:${hypothesis.id}:resolution:${resolution?.id ?? "invalid"}`,
      from: resolution,
      to: hypothesisRef,
      label: "回答",
      kind: "hypothesis_resolution",
      sourceObjectId: hypothesis.id,
    });
    for (const ref of hypothesis.required_claim_refs) {
      const target = readReference(ref);
      addEdge({
        identity: `hypothesis:${hypothesis.id}:requirement:${target?.id ?? "invalid"}`,
        from: target,
        to: hypothesisRef,
        label: "必要依据",
        kind: "hypothesis_requirement",
        sourceObjectId: hypothesis.id,
      });
    }
    for (const ref of hypothesis.falsifier_refs) {
      const target = readReference(ref);
      addEdge({
        identity: `hypothesis:${hypothesis.id}:falsifier:${target?.id ?? "invalid"}`,
        from: target,
        to: hypothesisRef,
        label: "可证伪",
        kind: "hypothesis_falsifier",
        sourceObjectId: hypothesis.id,
      });
    }
    for (const ref of hypothesis.competing_hypothesis_refs) {
      const target = readReference(ref);
      const pair = [hypothesis.id, target?.id ?? "invalid"].sort().join(":");
      addEdge({
        identity: `hypothesis:competitor:${pair}`,
        from: hypothesisRef,
        to: target,
        label: "竞争",
        kind: "hypothesis_competitor",
        direction: "undirected",
        sourceObjectId: hypothesis.id,
      });
    }
  }

  return {
    nodes: layoutGraphNodes([...nodes.values()]),
    edges: [...edges.values()].sort((left, right) => left.id.localeCompare(right.id)),
  };
}

function outcomeForHypothesis(
  hypothesis: ContractHypothesis | undefined,
): WorkbenchReasoningOutcome {
  if (!hypothesis) {
    return "contested";
  }
  if (hypothesis.status === "accepted" || hypothesis.status === "supported") {
    return "supported";
  }
  if (hypothesis.status === "eliminated" || hypothesis.status === "rejected") {
    return "eliminated";
  }
  return "contested";
}

function buildReasoningPaths(caseFile: CaseFileDocument): WorkbenchReasoningPath[] {
  const catalog = buildReferenceCatalog(caseFile);
  const hypotheses = new Map(caseFile.hypotheses.map((item) => [item.id, item]));
  const resolutions = new Map(caseFile.resolution_specs.map((item) => [item.id, item]));
  const informationIds = new Set(caseFile.information_units.map((item) => item.id));
  const labelFor = (id: string | null) => (id ? catalog.get(id)?.label ?? id : "");

  return caseFile.reasoning_paths.map((path) => {
    const target = readReference(path.target_ref);
    let hypothesis = target?.kind === "hypothesis" ? hypotheses.get(target.id) : undefined;
    if (!hypothesis && target?.kind === "resolution_spec") {
      hypothesis = [...hypotheses.values()]
        .filter(
          (item) => readReference(item.target_resolution_ref)?.id === target.id,
        )
        .sort((left, right) => left.id.localeCompare(right.id))[0];
    }
    const resolutionId = hypothesis
      ? readReference(hypothesis.target_resolution_ref)?.id ?? null
      : target?.kind === "resolution_spec"
        ? target.id
        : null;
    const resolution = resolutionId ? resolutions.get(resolutionId) : undefined;
    const steps = path.steps.map((step): WorkbenchReasoningStep => {
      const inputIds = readReferenceIds(step.input_refs);
      const outputId = readReference(step.output_ref)?.id ?? null;
      const inputLabels = inputIds.map(labelFor);
      const outputLabel = labelFor(outputId);
      return {
        id: step.step_id,
        verb: reasoningOperationLabel(step.operation),
        claim: `${inputLabels.join("、")}${inputLabels.length ? " → " : ""}${outputLabel}`,
        evidenceIds: inputIds.filter((id) => informationIds.has(id)),
        operation: step.operation,
        inputIds,
        inputLabels,
        outputId,
        outputLabel,
      };
    });
    return {
      id: path.id,
      question: resolution?.reasoning_question ?? "",
      evidenceIds: uniqueStrings(steps.flatMap((step) => step.evidenceIds)),
      steps,
      conclusion: hypothesis
        ? creatorLabel(hypothesis.title, {
            kind: "hypothesis",
            index: caseFile.hypotheses.findIndex((item) => item.id === hypothesis?.id),
            description: hypothesis.description,
          })
        : labelFor(target?.id ?? null),
      outcome: outcomeForHypothesis(hypothesis),
      hypothesisId: hypothesis?.id ?? target?.id ?? path.id,
      targetHypothesisId: hypothesis?.id ?? null,
      title: creatorLabel(path.title, {
        kind: "reasoning_path",
        index: caseFile.reasoning_paths.findIndex((item) => item.id === path.id),
        description: path.description,
      }),
      pathType: path.path_type,
      targetId: target?.id ?? null,
      targetLabel: labelFor(target?.id ?? null),
      resolutionSpecId: resolutionId,
      requiredForResolution: path.required_for_resolution,
      alternativePathIds: readReferenceIds(path.alternative_path_refs),
      source: path,
    };
  });
}

function buildReasoningGroups(
  caseFile: CaseFileDocument,
): WorkbenchReasoningGroup[] {
  const resolutions = new Map(caseFile.resolution_specs.map((item) => [item.id, item]));
  const information = new Map(caseFile.information_units.map((item) => [item.id, item]));
  const groups = new Map<
    string,
    {
      hypotheses: ContractHypothesis[];
      assessments: WorkbenchReasoningGroup["assessments"];
      informationIds: Set<string>;
    }
  >();

  for (const hypothesis of caseFile.hypotheses) {
    const resolutionId = readReference(hypothesis.target_resolution_ref)?.id;
    if (!resolutionId || !resolutions.has(resolutionId)) {
      continue;
    }
    const group = groups.get(resolutionId) ?? {
      hypotheses: [],
      assessments: [],
      informationIds: new Set<string>(),
    };
    group.hypotheses.push(hypothesis);
    for (const assessment of hypothesis.evidence_assessments ?? []) {
      const informationId = readReference(assessment.information_ref)?.id;
      if (!informationId || !information.has(informationId)) {
        continue;
      }
      group.informationIds.add(informationId);
      group.assessments.push({
        hypothesisId: hypothesis.id,
        informationId,
        effect: assessment.effect,
        strength: assessment.strength,
        rationale: creatorText(assessment.rationale, "该判定依据待补充。"),
      });
    }
    groups.set(resolutionId, group);
  }

  return caseFile.resolution_specs.flatMap((resolution) => {
    const group = groups.get(resolution.id);
    if (!group) {
      return [];
    }
    return [
      {
        resolutionSpecId: resolution.id,
        question:
          resolution.reasoning_question || resolution.title
            ? creatorLabel(
                resolution.reasoning_question || resolution.title,
                {
                  kind: "resolution_spec",
                  index: caseFile.resolution_specs.findIndex((item) => item.id === resolution.id),
                  description: resolution.description,
                },
              )
            : "未命名待解问题",
        hypotheses: group.hypotheses.map((hypothesis) => ({
          id: hypothesis.id,
          title: creatorLabel(hypothesis.title, {
            kind: "hypothesis",
            index: caseFile.hypotheses.findIndex((item) => item.id === hypothesis.id),
            description: hypothesis.description,
          }),
          outcome: outcomeForHypothesis(hypothesis),
        })),
        information: caseFile.information_units.flatMap((item) =>
          group.informationIds.has(item.id)
            ? [{
                id: item.id,
                title: creatorLabel(item.title, {
                  kind: "information_unit",
                  index: caseFile.information_units.findIndex((candidate) => candidate.id === item.id),
                  description: item.description,
                }),
                reliability: item.reliability,
              }]
            : [],
        ),
        assessments: group.assessments,
      },
    ];
  });
}

function countObjects(
  objects: WorkbenchCaseObject[],
): Record<WorkbenchObjectKind, number> {
  return Object.fromEntries(
    objectKindOrder.map((kind) => [
      kind,
      objects.filter((object) => object.kind === kind).length,
    ]),
  ) as Record<WorkbenchObjectKind, number>;
}

function buildCaseMeta(input: {
  caseFile: CaseFileDocument;
  draftRevision: number;
  timeline: WorkbenchTimelineEvent[];
  graph: { nodes: WorkbenchGraphNode[]; edges: WorkbenchGraphEdge[] };
  map: WorkbenchMapModel;
}): WorkbenchCaseMeta {
  const firstTime = input.timeline.find((event) => event.sortKey !== null)?.start;
  const lastTime = [...input.timeline].reverse().find((event) => event.sortKey !== null)?.start;
  const modeLabel = input.map.availableModes
    .map((mode) =>
      mode === "geographic"
        ? "真实地图"
        : mode === "scene"
          ? "场景图"
          : "自动布局",
    )
    .join(" / ");
  const realObjects = [
    ...input.caseFile.entities,
    ...input.caseFile.information_units,
    ...input.caseFile.events,
    ...input.caseFile.locations,
    ...input.caseFile.hypotheses,
  ];
  const actor = [...realObjects]
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0]
    ?.created_by.actor_id ?? "system";
  const caseTitle = creatorLabel(input.caseFile.title, {
    kind: "casefile",
    index: 0,
  });
  return {
    title: caseTitle,
    monogram: Array.from(caseTitle.trim())[0] ?? "案",
    subtitle: `卷宗契约 ${input.caseFile.schema_version} · 当前工作稿`,
    revision: `R${input.draftRevision}`,
    timelineTitle: caseTitle,
    timelineMeta: input.timeline.length
      ? `${input.timeline.length} 个事件${firstTime && lastTime ? ` · ${timelineClock(firstTime)} → ${timelineClock(lastTime)}` : ""}`
      : "0 个事件",
    mapTitle: `${caseTitle} / 空间图`,
    mapMeta: modeLabel || "0 个地点",
    mapNote: `${input.map.counts.geographic} 个地理坐标 · ${input.map.counts.scene} 个场景坐标 · ${input.map.counts.inferred} 个推算位置 · ${input.map.counts.unlocated} 个未定位`,
    relationshipSummary: `${input.graph.nodes.length} 个节点 · ${input.graph.edges.length} 条边`,
    exportTitle: caseTitle,
    exportCode: input.caseFile.casefile_id,
    exportSubtitle: "基于当前工作稿的开发预览",
    dossierVisibleRoles: `${input.caseFile.entities.length} 个实体`,
    branchLabel: "当前工作稿",
    protagonist: actor,
  };
}

export function mapCaseFileToWorkbenchModel(
  caseFile: CaseFileDocument,
  draftRevision: number,
  validation: WorkbenchValidationView | null = null,
): WorkbenchModel {
  const caseObjects = buildCaseObjects(caseFile);
  const validationIssues = buildValidationIssues(validation);
  const timelineEvents = buildTimeline(caseFile, caseObjects, validationIssues);
  const relationshipGraph = buildRelationshipGraph(caseFile, caseObjects);
  const reasoningPaths = buildReasoningPaths(caseFile);
  const reasoningGroups = buildReasoningGroups(caseFile);
  const map = buildWorkbenchSpatialModel(caseFile, timelineEvents);
  const caseMeta = buildCaseMeta({
    caseFile,
    draftRevision,
    timeline: timelineEvents,
    graph: relationshipGraph,
    map,
  });

  return {
    id: caseFile.casefile_id,
    origin: "contract",
    draftRevision,
    caseFile,
    caseMeta,
    caseObjects,
    objectCounts: countObjects(caseObjects),
    timelineEvents,
    validationIssues,
    sourceItems: [],
    graphNodes: relationshipGraph.nodes,
    graphEdges: relationshipGraph.edges,
    relationshipGraph,
    reasoningPaths,
    reasoningGroups,
    mapMarkers: [],
    mapLabels: [],
    map,
    drawer: emptyDrawer,
    initialAuditEntries: [],
    defaultEventId: timelineEvents[0]?.id ?? null,
    defaultObjectId: caseObjects[0]?.id ?? null,
    defaultIssueId: validationIssues[0]?.id ?? null,
  };
}

function fixtureKind(kind: WorkbenchSeed["caseObjects"][number]["kind"]): WorkbenchObjectKind {
  if (kind === "person" || kind === "entity") {
    return "entity";
  }
  if (kind === "evidence" || kind === "information") {
    return "information";
  }
  return kind;
}

export function mapFixtureToWorkbenchModel(seed: WorkbenchSeed): WorkbenchModel {
  const caseObjects = seed.caseObjects.map((object): WorkbenchCaseObject => ({
    ...object,
    kind: fixtureKind(object.kind),
    subtype: object.code,
    description: "",
    confidence: null,
    confirmationStatus: "unresolved",
    revision: 0,
    sourceRefIds: [],
    source: null,
  }));
  const objectById = new Map(caseObjects.map((object) => [object.id, object]));
  const fixtureKindById = new Map(
    seed.caseObjects.map((object) => [object.id, object.kind]),
  );
  const timelineEvents = seed.timelineEvents.map((event): WorkbenchTimelineEvent => ({
    ...event,
    start: event.time,
    end: null,
    precision: "unknown",
    truthStatus: "unknown",
    sortKey: parseTime(event.time),
    timeProjection: parseTime(event.time) ? "absolute" : "unresolved",
    refs: {
      participantIds: event.relatedObjectIds.filter(
        (id) => objectById.get(id)?.kind === "entity",
      ),
      locationId: null,
      causeIds: [],
      effectIds: [],
      observerIds: [],
      sourceIds: [],
    },
    source: null,
  }));
  const graphNodes = seed.graphNodes.map((node): WorkbenchGraphNode => ({
      ...node,
      id: node.objectId,
      kind:
        fixtureKindById.get(node.objectId) ??
        referenceKindForDirectoryKind(objectById.get(node.objectId)?.kind),
      label: objectById.get(node.objectId)?.label ?? node.objectId,
      directoryObjectId: objectById.has(node.objectId) ? node.objectId : null,
    }));
  const graphEdges = seed.graphEdges.map((edge, index): WorkbenchGraphEdge => ({
    id: `edge_${hashString(`fixture:${index}:${edge.from}:${edge.to}:${edge.label}`).toString(36)}`,
    ...edge,
    kind: "fixture",
    direction: "directed",
    sourceObjectId: null,
  }));
  const reasoningPaths = seed.reasoningPaths.map((path): WorkbenchReasoningPath => ({
    ...path,
    hypothesisId: path.hypothesisId || path.id,
    targetHypothesisId: path.hypothesisId || null,
    title: path.question,
    pathType: "fixture",
    targetId: path.hypothesisId || null,
    targetLabel: path.conclusion,
    resolutionSpecId: null,
    requiredForResolution: true,
    alternativePathIds: [],
    source: null,
    steps: path.steps.map((step) => ({
      ...step,
      operation: step.verb,
      inputIds: step.evidenceIds,
      inputLabels: step.evidenceIds.map(
        (id) => objectById.get(id)?.label ?? id,
      ),
      outputId: path.hypothesisId || null,
      outputLabel: path.conclusion,
    })),
  }));
  const map = buildFixtureSpatialModel({ ...seed, timelineEvents });

  return {
    id: seed.id,
    origin: "fixture",
    draftRevision: null,
    caseFile: null,
    caseMeta: seed.caseMeta,
    caseObjects,
    objectCounts: countObjects(caseObjects),
    timelineEvents,
    validationIssues: seed.validationIssues,
    sourceItems: seed.sourceItems,
    graphNodes,
    graphEdges,
    relationshipGraph: { nodes: graphNodes, edges: graphEdges },
    reasoningPaths,
    reasoningGroups: seed.reasoningGroups ?? [],
    mapMarkers: seed.mapMarkers,
    mapLabels: seed.mapLabels,
    map,
    drawer: seed.drawer,
    initialAuditEntries: seed.initialAuditEntries,
    defaultEventId: seed.defaultEventId || null,
    defaultObjectId: seed.defaultObjectId || null,
    defaultIssueId: seed.defaultIssueId || null,
  };
}
