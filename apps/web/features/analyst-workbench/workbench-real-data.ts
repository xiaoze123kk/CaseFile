import type { ObjectRef } from "@casefile/contracts";
import type {
  CaseFileDocument,
  WorkbenchValidationView,
} from "@/lib/api-client";

import type { ValidationIssue, WorkbenchSeed } from "./analyst-fixture";
import type {
  WorkbenchCaseMeta,
  WorkbenchCaseObject,
  WorkbenchContractObject,
  WorkbenchCoordinateSystem,
  WorkbenchGraphEdge,
  WorkbenchGraphEdgeKind,
  WorkbenchGraphNode,
  WorkbenchMapGroup,
  WorkbenchMapLocation,
  WorkbenchMapMarker,
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

type ContractLocation = CaseFileDocument["locations"][number];
type ContractHypothesis = CaseFileDocument["hypotheses"][number];

interface ParsedReference {
  id: string;
  kind: WorkbenchReferenceKind;
}

interface ReferenceCatalogEntry extends ParsedReference {
  label: string;
}

interface SchematicPosition {
  x: number;
  y: number;
  isFallback: boolean;
}

type SpatialPosition =
  | { coordinateSystem: "schematic"; x: number; y: number }
  | {
      coordinateSystem: "wgs84";
      latitude: number;
      longitude: number;
    };

const objectKindOrder: WorkbenchObjectKind[] = [
  "entity",
  "information",
  "event",
  "location",
  "hypothesis",
];

const referenceKindOrder: WorkbenchReferenceKind[] = [
  "entity",
  "information_unit",
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

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function readSpatialPosition(location: ContractLocation): SpatialPosition | null {
  const raw = (location as Record<string, unknown>).spatial_position;
  if (!isRecord(raw)) {
    return null;
  }
  if (raw.coordinate_system === "schematic") {
    const x = finiteNumber(raw.x);
    const y = finiteNumber(raw.y);
    return x !== null && y !== null && x >= 0 && x <= 100 && y >= 0 && y <= 100
      ? { coordinateSystem: "schematic", x, y }
      : null;
  }
  if (raw.coordinate_system === "wgs84") {
    const latitude = finiteNumber(raw.latitude);
    const longitude = finiteNumber(raw.longitude);
    return latitude !== null &&
      longitude !== null &&
      latitude >= -90 &&
      latitude <= 90 &&
      longitude >= -180 &&
      longitude <= 180
      ? { coordinateSystem: "wgs84", latitude, longitude }
      : null;
  }
  return null;
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

function confidenceText(value: number | null): string {
  return value === null ? "置信度未标注" : `置信度 ${Math.round(value * 100)}%`;
}

function metadataForObject(object: WorkbenchContractObject) {
  return {
    description: typeof object.description === "string" ? object.description : "",
    confidence: object.confidence,
    confirmationStatus: object.confirmation_status,
    revision: object.revision,
    sourceRefIds: readReferenceIds(object.source_refs),
  };
}

function objectMeta(object: WorkbenchContractObject): string {
  return `${confidenceText(object.confidence)} · ${object.confirmation_status}`;
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
    ...caseFile.entities.map((entity): WorkbenchCaseObject => ({
      id: entity.id,
      kind: "entity",
      label: entity.name,
      code: entity.entity_type,
      meta: objectMeta(entity),
      subtype: entity.entity_type,
      ...metadataForObject(entity),
      relatedEventIds: related(entity.id),
      source: entity,
    })),
    ...caseFile.information_units.map((information): WorkbenchCaseObject => ({
      id: information.id,
      kind: "information",
      label: information.title,
      code: `${information.information_type} · ${information.classification}`,
      meta: objectMeta(information),
      subtype: information.information_type,
      ...metadataForObject(information),
      relatedEventIds: related(information.id),
      source: information,
    })),
    ...caseFile.events.map((event): WorkbenchCaseObject => ({
      id: event.id,
      kind: "event",
      label: event.title,
      code: `${event.truth_status} · ${temporalSummary(event.time).precision}`,
      meta: objectMeta(event),
      subtype: event.truth_status,
      ...metadataForObject(event),
      relatedEventIds: related(event.id),
      source: event,
    })),
    ...caseFile.locations.map((location): WorkbenchCaseObject => ({
      id: location.id,
      kind: "location",
      label: location.name,
      code: readSpatialPosition(location)?.coordinateSystem ?? "topology",
      meta: objectMeta(location),
      subtype: readSpatialPosition(location)?.coordinateSystem ?? "topology",
      ...metadataForObject(location),
      relatedEventIds: related(location.id),
      source: location,
    })),
    ...caseFile.hypotheses.map((hypothesis): WorkbenchCaseObject => ({
      id: hypothesis.id,
      kind: "hypothesis",
      label: hypothesis.title,
      code: hypothesis.status,
      meta: objectMeta(hypothesis),
      subtype: hypothesis.status,
      ...metadataForObject(hypothesis),
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
    caseFile.locations.map((location) => [location.id, location.name]),
  );

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
      return {
        event: {
          id: event.id,
          time: temporal.label,
          label: event.title,
          location: locationId ? locationNames.get(locationId) ?? locationId : "未指定地点",
          summary: typeof event.description === "string" ? event.description : "",
          relatedObjectIds,
          issueIds: validationIssues
            .filter((issue) => issue.eventId === event.id)
            .map((issue) => issue.id),
          start: temporal.start,
          end: temporal.end,
          precision: temporal.precision,
          truthStatus: event.truth_status,
          sortKey: temporal.sortKey,
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
    { id: caseFile.casefile_id, kind: "casefile", label: caseFile.title },
    ...caseFile.resolution_specs.map((item) => ({
      id: item.id,
      kind: "resolution_spec" as const,
      label: item.title,
    })),
    ...caseFile.entities.map((item) => ({
      id: item.id,
      kind: "entity" as const,
      label: item.name,
    })),
    ...caseFile.relationships.map((item) => ({
      id: item.id,
      kind: "relationship" as const,
      label: item.title,
    })),
    ...caseFile.locations.map((item) => ({
      id: item.id,
      kind: "location" as const,
      label: item.name,
    })),
    ...caseFile.events.map((item) => ({
      id: item.id,
      kind: "event" as const,
      label: item.title,
    })),
    ...caseFile.information_units.map((item) => ({
      id: item.id,
      kind: "information_unit" as const,
      label: item.title,
    })),
    ...caseFile.claims.map((item) => ({
      id: item.id,
      kind: "claim" as const,
      label: item.title,
    })),
    ...caseFile.hypotheses.map((item) => ({
      id: item.id,
      kind: "hypothesis" as const,
      label: item.title,
    })),
    ...caseFile.reasoning_paths.map((item) => ({
      id: item.id,
      kind: "reasoning_path" as const,
      label: item.title,
    })),
    ...caseFile.constraints.map((item) => ({
      id: item.id,
      kind: "constraint" as const,
      label: item.title,
    })),
    ...caseFile.structure_locks.map((item) => ({
      id: item.id,
      kind: "structure_lock" as const,
      label: item.title,
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
      label: relationship.title || relationship.relationship_type,
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
        verb: step.operation,
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
      conclusion: hypothesis?.title ?? labelFor(target?.id ?? null),
      outcome: outcomeForHypothesis(hypothesis),
      hypothesisId: hypothesis?.id ?? target?.id ?? path.id,
      targetHypothesisId: hypothesis?.id ?? null,
      title: path.title,
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

function initialGrid(ids: string[]): Map<string, { x: number; y: number }> {
  const sorted = [...ids].sort();
  const columns = Math.max(1, Math.ceil(Math.sqrt(sorted.length || 1)));
  const rows = Math.max(1, Math.ceil(sorted.length / columns));
  return new Map(
    sorted.map((id, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      return [
        id,
        {
          x: columns === 1 ? 50 : 12 + (column / (columns - 1)) * 76,
          y: rows === 1 ? 50 : 12 + (row / (rows - 1)) * 76,
        },
      ];
    }),
  );
}

function buildSchematicPositions(
  locations: ContractLocation[],
  spatialById: Map<string, SpatialPosition | null>,
): Map<string, SchematicPosition> {
  const candidates = locations
    .filter((location) => spatialById.get(location.id)?.coordinateSystem !== "wgs84")
    .sort((left, right) => left.id.localeCompare(right.id));
  const initial = initialGrid(candidates.map((location) => location.id));
  const positions = new Map<string, SchematicPosition>();
  const fallbackIds = new Set<string>();
  for (const location of candidates) {
    const spatial = spatialById.get(location.id);
    if (spatial?.coordinateSystem === "schematic") {
      positions.set(location.id, { x: spatial.x, y: spatial.y, isFallback: false });
    } else {
      const point = initial.get(location.id) ?? { x: 50, y: 50 };
      positions.set(location.id, { ...point, isFallback: true });
      fallbackIds.add(location.id);
    }
  }

  const candidateIds = new Set(candidates.map((location) => location.id));
  const neighborWeights = new Map<string, Map<string, number>>();
  const connect = (left: string, right: string | null, weight: number) => {
    if (!right || left === right || !candidateIds.has(left) || !candidateIds.has(right)) {
      return;
    }
    const add = (from: string, to: string) => {
      const neighbors = neighborWeights.get(from) ?? new Map<string, number>();
      neighbors.set(to, Math.max(neighbors.get(to) ?? 0, weight));
      neighborWeights.set(from, neighbors);
    };
    add(left, right);
    add(right, left);
  };

  for (const location of candidates) {
    connect(location.id, readReference(location.parent_ref)?.id ?? null, 3);
    for (const ref of location.adjacency_refs) {
      connect(location.id, readReference(ref)?.id ?? null, 2);
    }
    for (const travel of location.travel_times) {
      const weight = clamp(120 / Math.max(1, travel.minutes), 0.25, 4);
      connect(location.id, readReference(travel.to_ref)?.id ?? null, weight);
    }
  }

  for (let iteration = 0; iteration < 24; iteration += 1) {
    const next = new Map(positions);
    for (const id of [...fallbackIds].sort()) {
      const neighbors = neighborWeights.get(id);
      if (!neighbors || neighbors.size === 0) {
        continue;
      }
      let totalWeight = 0;
      let weightedX = 0;
      let weightedY = 0;
      for (const [neighborId, weight] of [...neighbors.entries()].sort()) {
        const neighbor = positions.get(neighborId);
        if (!neighbor) {
          continue;
        }
        totalWeight += weight;
        weightedX += neighbor.x * weight;
        weightedY += neighbor.y * weight;
      }
      if (totalWeight === 0) {
        continue;
      }
      const anchor = initial.get(id) ?? { x: 50, y: 50 };
      const angle = ((hashString(id) % 360) * Math.PI) / 180;
      const targetX = weightedX / totalWeight + Math.cos(angle) * 4;
      const targetY = weightedY / totalWeight + Math.sin(angle) * 4;
      next.set(id, {
        x: clamp(targetX * 0.76 + anchor.x * 0.24, 3, 97),
        y: clamp(targetY * 0.76 + anchor.y * 0.24, 3, 97),
        isFallback: true,
      });
    }
    for (const [id, point] of next) {
      positions.set(id, point);
    }
  }
  return positions;
}

function eventMarkersForGroup(
  coordinateSystem: WorkbenchCoordinateSystem,
  timelineEvents: WorkbenchTimelineEvent[],
  locations: WorkbenchMapLocation[],
): WorkbenchMapMarker[] {
  const points = new Map(
    locations.flatMap((location) =>
      location.locationId ? [[location.locationId, location] as const] : [],
    ),
  );
  return timelineEvents.flatMap((event) => {
    const locationId = event.refs.locationId;
    const point = locationId ? points.get(locationId) : undefined;
    return point
      ? [
          {
            eventId: event.id,
            locationId,
            label: event.label,
            coordinateSystem,
            x: point.x,
            y: point.y,
          } satisfies WorkbenchMapMarker,
        ]
      : [];
  });
}

function buildMap(
  caseFile: CaseFileDocument,
  timelineEvents: WorkbenchTimelineEvent[],
): WorkbenchMapModel {
  const spatialById = new Map(
    caseFile.locations.map((location) => [location.id, readSpatialPosition(location)]),
  );
  const schematicPositions = buildSchematicPositions(caseFile.locations, spatialById);
  const locationNames = new Map(
    caseFile.locations.map((location) => [location.id, location.name]),
  );
  const schematicLocations = [...schematicPositions.entries()]
    .map(([locationId, point]): WorkbenchMapLocation => ({
      locationId,
      label: locationNames.get(locationId) ?? locationId,
      coordinateSystem: "schematic",
      x: point.x,
      y: point.y,
      isFallback: point.isFallback,
    }))
    .sort((left, right) => (left.locationId ?? "").localeCompare(right.locationId ?? ""));

  const geographic = caseFile.locations
    .flatMap((location) => {
      const spatial = spatialById.get(location.id);
      return spatial?.coordinateSystem === "wgs84"
        ? [{ location, spatial }]
        : [];
    })
    .sort((left, right) => left.location.id.localeCompare(right.location.id));
  const latitudes = geographic.map(({ spatial }) => spatial.latitude);
  const longitudes = geographic.map(({ spatial }) => spatial.longitude);
  const bounds = geographic.length
    ? {
        minLatitude: Math.min(...latitudes),
        maxLatitude: Math.max(...latitudes),
        minLongitude: Math.min(...longitudes),
        maxLongitude: Math.max(...longitudes),
      }
    : null;
  const wgs84Locations = geographic.map(({ location, spatial }): WorkbenchMapLocation => {
    const x =
      bounds && bounds.maxLongitude !== bounds.minLongitude
        ? ((spatial.longitude - bounds.minLongitude) /
            (bounds.maxLongitude - bounds.minLongitude)) *
          100
        : 50;
    const y =
      bounds && bounds.maxLatitude !== bounds.minLatitude
        ? ((bounds.maxLatitude - spatial.latitude) /
            (bounds.maxLatitude - bounds.minLatitude)) *
          100
        : 50;
    return {
      locationId: location.id,
      label: location.name,
      coordinateSystem: "wgs84",
      x,
      y,
      isFallback: false,
      latitude: spatial.latitude,
      longitude: spatial.longitude,
    };
  });

  const schematic: WorkbenchMapGroup = {
    coordinateSystem: "schematic",
    locations: schematicLocations,
    eventMarkers: eventMarkersForGroup("schematic", timelineEvents, schematicLocations),
    bounds: null,
  };
  const wgs84: WorkbenchMapGroup = {
    coordinateSystem: "wgs84",
    locations: wgs84Locations,
    eventMarkers: eventMarkersForGroup("wgs84", timelineEvents, wgs84Locations),
    bounds,
  };
  const availableModes: WorkbenchCoordinateSystem[] = [
    ...(wgs84.locations.length ? (["wgs84"] as const) : []),
    ...(schematic.locations.length ? (["schematic"] as const) : []),
  ];
  return {
    availableModes,
    defaultMode: availableModes[0] ?? null,
    groups: { schematic, wgs84 },
    fallbackLocationIds: schematicLocations
      .filter((location) => location.isFallback && location.locationId)
      .map((location) => location.locationId as string),
  };
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
    .map((mode) => (mode === "wgs84" ? "地理坐标" : "空间示意"))
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
  return {
    title: input.caseFile.title,
    monogram: Array.from(input.caseFile.title.trim())[0] ?? "案",
    subtitle: `CaseFile ${input.caseFile.schema_version} · Current Draft`,
    revision: `R${input.draftRevision}`,
    timelineTitle: input.caseFile.title,
    timelineMeta: input.timeline.length
      ? `${input.timeline.length} EVENTS${firstTime && lastTime ? ` · ${firstTime} → ${lastTime}` : ""}`
      : "0 EVENTS",
    mapTitle: `${input.caseFile.title} / 空间图`,
    mapMeta: modeLabel || "0 LOCATIONS",
    mapNote: `${input.map.groups.schematic.locations.filter((item) => !item.isFallback).length} 个示意坐标 · ${input.map.groups.wgs84.locations.length} 个地理坐标 · ${input.map.fallbackLocationIds.length} 个拓扑回退`,
    relationshipSummary: `${input.graph.nodes.length} 个节点 · ${input.graph.edges.length} 条边`,
    exportTitle: input.caseFile.title,
    exportCode: input.caseFile.casefile_id,
    exportSubtitle: "基于当前 Draft 的开发预览",
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
  const map = buildMap(caseFile, timelineEvents);
  const activeMapGroup = map.defaultMode ? map.groups[map.defaultMode] : null;
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
    mapMarkers: activeMapGroup?.eventMarkers ?? [],
    mapLabels:
      activeMapGroup?.locations.map((location) => ({
        locationId: location.locationId,
        label: location.label,
        coordinateSystem: location.coordinateSystem,
        x: location.x,
        y: location.y,
        isFallback: location.isFallback,
      })) ?? [],
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
  const timelineEvents = seed.timelineEvents.map((event): WorkbenchTimelineEvent => ({
    ...event,
    start: event.time,
    end: null,
    precision: "unknown",
    truthStatus: "unknown",
    sortKey: parseTime(event.time),
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
      kind: referenceKindForDirectoryKind(objectById.get(node.objectId)?.kind),
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
  const schematicLocations = seed.mapLabels.map((label): WorkbenchMapLocation => ({
    locationId: null,
    label: label.label,
    coordinateSystem: "schematic",
    x: label.x,
    y: label.y,
    isFallback: false,
  }));
  const schematicMarkers = seed.mapMarkers.map((marker): WorkbenchMapMarker => ({
    ...marker,
    locationId: null,
    coordinateSystem: "schematic",
  }));
  const hasMapData = schematicLocations.length > 0 || schematicMarkers.length > 0;
  const map: WorkbenchMapModel = {
    availableModes: hasMapData ? ["schematic"] : [],
    defaultMode: hasMapData ? "schematic" : null,
    groups: {
      schematic: {
        coordinateSystem: "schematic",
        locations: schematicLocations,
        eventMarkers: schematicMarkers,
        bounds: null,
      },
      wgs84: {
        coordinateSystem: "wgs84",
        locations: [],
        eventMarkers: [],
        bounds: null,
      },
    },
    fallbackLocationIds: [],
  };

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
    mapMarkers: schematicMarkers,
    mapLabels: schematicLocations.map((location) => ({
      locationId: location.locationId,
      label: location.label,
      coordinateSystem: location.coordinateSystem,
      x: location.x,
      y: location.y,
      isFallback: location.isFallback,
    })),
    map,
    drawer: seed.drawer,
    initialAuditEntries: seed.initialAuditEntries,
    defaultEventId: seed.defaultEventId || null,
    defaultObjectId: seed.defaultObjectId || null,
    defaultIssueId: seed.defaultIssueId || null,
  };
}
