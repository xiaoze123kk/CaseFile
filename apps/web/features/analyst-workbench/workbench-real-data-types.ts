import type { CaseFile } from "@casefile/contracts";

import type {
  SourceItem,
  ValidationIssue,
  WorkbenchAuditEntry,
  WorkbenchDrawerCopy,
  WorkbenchSeed,
} from "./analyst-fixture";

export type WorkbenchObjectKind =
  | "entity"
  | "information"
  | "event"
  | "location"
  | "hypothesis";

export type WorkbenchReferenceKind =
  | "casefile"
  | "resolution_spec"
  | "entity"
  | "person"
  | "information_unit"
  | "information"
  | "evidence"
  | "event"
  | "location"
  | "hypothesis"
  | "relationship"
  | "claim"
  | "reasoning_path"
  | "constraint"
  | "structure_lock"
  | "source_fragment"
  | "unknown";

export type WorkbenchSpatialMode = "geographic" | "scene" | "topology";

export type WorkbenchPositionSource = "wgs84" | "schematic" | "inferred";

export type SpatialPositionPayload = NonNullable<
  CaseFile["locations"][number]["spatial_position"]
>;

export type SpatialPositionSaveResult = "saved" | "conflict" | "error";

export interface ReloadedSpatialLocation {
  found: boolean;
  position: SpatialPositionPayload | null;
  revision: number;
}

export type SpatialLayerId =
  | "locations"
  | "events"
  | "relations"
  | "unconfirmed";

export type SpatialLayerVisibility = Record<SpatialLayerId, boolean>;

export type WorkbenchContractObject =
  | CaseFile["entities"][number]
  | CaseFile["information_units"][number]
  | CaseFile["events"][number]
  | CaseFile["locations"][number]
  | CaseFile["hypotheses"][number];

export interface WorkbenchCaseObject {
  id: string;
  kind: WorkbenchObjectKind;
  label: string;
  code: string;
  meta: string;
  subtype: string;
  description: string;
  confidence: number | null;
  confirmationStatus: string;
  revision: number;
  sourceRefIds: string[];
  relatedEventIds: string[];
  source: WorkbenchContractObject | null;
}

export interface WorkbenchTimelineReferences {
  participantIds: string[];
  locationId: string | null;
  causeIds: string[];
  effectIds: string[];
  observerIds: string[];
  sourceIds: string[];
}

export interface WorkbenchTimelineEvent {
  id: string;
  time: string;
  label: string;
  location: string;
  summary: string;
  relatedObjectIds: string[];
  issueIds: string[];
  start: string;
  end: string | null;
  precision: string;
  truthStatus: string;
  sortKey: number | null;
  refs: WorkbenchTimelineReferences;
  source: CaseFile["events"][number] | null;
}

export interface WorkbenchGraphNode {
  objectId: string;
  id: string;
  kind: WorkbenchReferenceKind;
  label: string;
  x: number;
  y: number;
  directoryObjectId: string | null;
}

export type WorkbenchGraphEdgeKind =
  | "relationship"
  | "event_participant"
  | "event_location"
  | "event_cause"
  | "event_effect"
  | "event_observer"
  | "source"
  | "location_parent"
  | "location_adjacency"
  | "location_travel"
  | "information_source_event"
  | "information_support"
  | "information_refute"
  | "information_perspective"
  | "hypothesis_resolution"
  | "hypothesis_requirement"
  | "hypothesis_falsifier"
  | "hypothesis_competitor"
  | "fixture";

export interface WorkbenchGraphEdge {
  id: string;
  from: string;
  to: string;
  label: string;
  kind: WorkbenchGraphEdgeKind;
  direction: "directed" | "undirected" | "bidirectional";
  sourceObjectId: string | null;
}

export type WorkbenchReasoningOutcome =
  | "supported"
  | "contested"
  | "eliminated";

export interface WorkbenchReasoningStep {
  id: string;
  verb: string;
  claim: string;
  evidenceIds: string[];
  operation: string;
  inputIds: string[];
  inputLabels: string[];
  outputId: string | null;
  outputLabel: string;
}

export interface WorkbenchReasoningPath {
  id: string;
  question: string;
  evidenceIds: string[];
  steps: WorkbenchReasoningStep[];
  conclusion: string;
  outcome: WorkbenchReasoningOutcome;
  hypothesisId: string;
  targetHypothesisId: string | null;
  title: string;
  pathType: string;
  targetId: string | null;
  targetLabel: string;
  resolutionSpecId: string | null;
  requiredForResolution: boolean;
  alternativePathIds: string[];
  source: CaseFile["reasoning_paths"][number] | null;
}

export type WorkbenchSpatialPosition =
  | {
      kind: "wgs84";
      latitude: number;
      longitude: number;
    }
  | {
      kind: "planar";
      x: number;
      y: number;
    };

export interface WorkbenchSpatialEvent {
  eventId: string;
  label: string;
  time: string;
  relatedObjectIds: string[];
}

export interface WorkbenchSpatialLocation {
  spatialId: string;
  locationId: string | null;
  label: string;
  source: WorkbenchPositionSource;
  position: WorkbenchSpatialPosition;
  events: WorkbenchSpatialEvent[];
  relatedObjectIds: string[];
}

export interface WorkbenchSpatialRelation {
  relationId: string;
  kind: "adjacency" | "travel";
  fromLocationId: string;
  toLocationId: string;
  direction: "directed" | "undirected";
  label: string;
  minutes: number | null;
}

export interface WorkbenchSpatialView {
  mode: WorkbenchSpatialMode;
  locations: WorkbenchSpatialLocation[];
  relations: WorkbenchSpatialRelation[];
}

export interface WorkbenchMapModel {
  availableModes: WorkbenchSpatialMode[];
  defaultMode: WorkbenchSpatialMode | null;
  views: Record<WorkbenchSpatialMode, WorkbenchSpatialView>;
  unlocatedLocationIds: string[];
  unlocatedLocations: Array<{ locationId: string; label: string }>;
  counts: {
    locations: number;
    events: number;
    geographic: number;
    scene: number;
    inferred: number;
    unlocated: number;
  };
}

/** Fixture compatibility fields. Production map rendering consumes `WorkbenchMapModel`. */
export interface WorkbenchMapMarker {
  eventId: string;
  label: string;
  x: number;
  y: number;
}

/** Fixture compatibility fields. Production map rendering consumes `WorkbenchMapModel`. */
export interface WorkbenchMapLabel {
  label: string;
  x: number;
  y: number;
}

export interface WorkbenchCaseMeta {
  title: string;
  monogram: string;
  subtitle: string;
  revision: string;
  timelineTitle: string;
  timelineMeta: string;
  mapTitle: string;
  mapMeta: string;
  mapNote: string;
  relationshipSummary: string;
  exportTitle: string;
  exportCode: string;
  exportSubtitle: string;
  dossierVisibleRoles: string;
  branchLabel: string;
  protagonist: string;
}

export interface WorkbenchModel extends WorkbenchSeed {
  id: string;
  origin: "contract" | "fixture";
  draftRevision: number | null;
  caseFile: CaseFile | null;
  caseMeta: WorkbenchCaseMeta;
  caseObjects: WorkbenchCaseObject[];
  objectCounts: Record<WorkbenchObjectKind, number>;
  timelineEvents: WorkbenchTimelineEvent[];
  validationIssues: ValidationIssue[];
  sourceItems: SourceItem[];
  graphNodes: WorkbenchGraphNode[];
  graphEdges: WorkbenchGraphEdge[];
  relationshipGraph: {
    nodes: WorkbenchGraphNode[];
    edges: WorkbenchGraphEdge[];
  };
  reasoningPaths: WorkbenchReasoningPath[];
  mapMarkers: WorkbenchMapMarker[];
  mapLabels: WorkbenchMapLabel[];
  map: WorkbenchMapModel;
  drawer: WorkbenchDrawerCopy;
  initialAuditEntries: WorkbenchAuditEntry[];
  defaultEventId: string | null;
  defaultObjectId: string | null;
  defaultIssueId: string | null;
}
