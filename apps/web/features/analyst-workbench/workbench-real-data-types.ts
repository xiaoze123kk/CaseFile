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
  | "information_unit"
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

export type WorkbenchCoordinateSystem = "schematic" | "wgs84";

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

export interface WorkbenchMapLocation {
  locationId: string | null;
  label: string;
  coordinateSystem: WorkbenchCoordinateSystem;
  x: number;
  y: number;
  isFallback: boolean;
  latitude?: number;
  longitude?: number;
}

export interface WorkbenchMapMarker {
  eventId: string;
  locationId: string | null;
  label: string;
  coordinateSystem: WorkbenchCoordinateSystem;
  x: number;
  y: number;
}

export interface WorkbenchMapBounds {
  minLatitude: number;
  maxLatitude: number;
  minLongitude: number;
  maxLongitude: number;
}

export interface WorkbenchMapGroup {
  coordinateSystem: WorkbenchCoordinateSystem;
  locations: WorkbenchMapLocation[];
  eventMarkers: WorkbenchMapMarker[];
  bounds: WorkbenchMapBounds | null;
}

export interface WorkbenchMapModel {
  availableModes: WorkbenchCoordinateSystem[];
  defaultMode: WorkbenchCoordinateSystem | null;
  groups: Record<WorkbenchCoordinateSystem, WorkbenchMapGroup>;
  fallbackLocationIds: string[];
}

export interface WorkbenchMapLabel {
  locationId: string | null;
  label: string;
  coordinateSystem: WorkbenchCoordinateSystem;
  x: number;
  y: number;
  isFallback: boolean;
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
