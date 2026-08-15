import type { CaseFileDocument } from "@/lib/api-client";

/**
 * Shared CaseFile ObjectRef traversal used by the object detail model, the
 * relation model and the reverse-reference index.
 *
 * Every `{ object_type, object_id }` pair inside the document is registered
 * once per field path. This is the single place that knows where references
 * live, so views no longer re-derive their own reference walkers.
 */

export interface TypedObjectRef {
  object_type: string;
  object_id: string;
}

export interface DocumentReferenceEdge {
  source_object_id: string;
  source_object_type: string;
  target_object_id: string;
  target_object_type: string;
  /** JSON-pointer-like path, e.g. /events/0/participant_refs/0 */
  field_path: string;
  /** Display label for the field that holds the reference. */
  field_label: string;
}

export type ReferenceIndex = Map<string, DocumentReferenceEdge[]>;

const DOCUMENT_COLLECTIONS: Array<[keyof CaseFileDocument, string]> = [
  ["resolution_specs", "resolution_spec"],
  ["entities", "entity"],
  ["relationships", "relationship"],
  ["locations", "location"],
  ["events", "event"],
  ["information_units", "information_unit"],
  ["claims", "claim"],
  ["hypotheses", "hypothesis"],
  ["reasoning_paths", "reasoning_path"],
  ["constraints", "constraint"],
  ["structure_locks", "structure_lock"],
];

export const REFERENCE_FIELD_LABELS: Record<string, string> = {
  accepted_answers: "已接受答案",
  adjacency_refs: "相邻地点",
  alternative_path_refs: "替代路径",
  anchor_event_ref: "时间锚点",
  as_of_event_ref: "认知时点",
  believes_refs: "相信",
  cause_refs: "原因事件",
  competing_hypothesis_refs: "竞争假设",
  conflict_refs: "冲突对象",
  dependency_claim_refs: "依赖论断",
  effect_refs: "结果事件",
  falsifier_refs: "证伪条件",
  false_belief_refs: "错误认知",
  from_ref: "关系起点",
  information_ref: "评估信息",
  input_refs: "推理输入",
  knows_refs: "已知",
  location_ref: "发生地点",
  object_ref: "锁定对象",
  observed_by_refs: "观察者",
  output_ref: "推理输出",
  parent_ref: "上级地点",
  participant_refs: "参与者",
  perspective_refs: "可获得者",
  refute_refs: "反驳依据",
  refutes_claim_refs: "反驳论断",
  required_claim_refs: "必需论断",
  scope_refs: "约束范围",
  selected_hypothesis_refs: "获选假设",
  source_event_ref: "来源事件",
  source_refs: "来源",
  support_refs: "支持依据",
  supporting_reasoning_path_refs: "依据路径",
  supports_claim_refs: "支持论断",
  target_ref: "推导目标",
  target_resolution_ref: "目标问题",
  to_ref: "前往地点",
};

export function parseObjectRef(value: unknown): TypedObjectRef | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  return typeof record.object_type === "string" && typeof record.object_id === "string"
    ? { object_type: record.object_type, object_id: record.object_id }
    : null;
}

/**
 * Visits every ObjectRef reachable from `value`, including refs nested inside
 * arrays and plain objects. Ref-shaped objects are registered and still
 * traversed once more so extension fields cannot hide nested references.
 */
export function walkObjectReferences(
  value: unknown,
  visit: (reference: TypedObjectRef) => void,
) {
  if (Array.isArray(value)) {
    for (const item of value) walkObjectReferences(item, visit);
    return;
  }
  if (!value || typeof value !== "object") return;
  const reference = parseObjectRef(value);
  if (reference) visit(reference);
  for (const [key, child] of Object.entries(value)) {
    if (reference && (key === "object_type" || key === "object_id")) continue;
    walkObjectReferences(child, visit);
  }
}

function isReferenceField(
  segments: Array<string | number>,
  sourceObjectType: string,
): boolean {
  if (sourceObjectType === "relationship") {
    const last = segments[segments.length - 1];
    return last === "from_ref" || last === "to_ref";
  }
  if (segments[segments.length - 1] === "value" && segments.includes("conclusion")) {
    return true;
  }
  return segments.some((segment) => typeof segment === "string" &&
    (segment.endsWith("_ref") || segment.endsWith("_refs")));
}

function lastNamedSegment(segments: Array<string | number>): string {
  for (let index = segments.length - 1; index >= 0; index -= 1) {
    const segment = segments[index];
    if (typeof segment === "string") return segment;
  }
  return "引用";
}

function fieldLabelFor(
  segments: Array<string | number>,
  sourceObjectType: string,
): string {
  const last = lastNamedSegment(segments);
  if (sourceObjectType === "relationship") {
    if (last === "from_ref") return "关系起点";
    if (last === "to_ref") return "关系终点";
  }
  if (last === "value" && segments.includes("conclusion")) return "结论答案";
  return REFERENCE_FIELD_LABELS[last] ?? last;
}

/**
 * Emits one edge per unique (source, field path, target) triple so the same
 * reference value is never double counted when it is reachable twice.
 */
export function collectDocumentReferenceEdges(
  document: CaseFileDocument,
): DocumentReferenceEdge[] {
  const edges: DocumentReferenceEdge[] = [];
  const seen = new Set<string>();
  const record = document as unknown as Record<string, unknown>;

  for (const [collection, objectType] of DOCUMENT_COLLECTIONS) {
    const items = record[collection];
    if (!Array.isArray(items)) continue;
    items.forEach((item, index) => {
      if (!item || typeof item !== "object") return;
      const sourceObjectId = (item as Record<string, unknown>).id;
      if (typeof sourceObjectId !== "string" || !sourceObjectId) return;

      const visit = (value: unknown, segments: Array<string | number>) => {
        if (Array.isArray(value)) {
          value.forEach((entry, entryIndex) => visit(entry, [...segments, entryIndex]));
          return;
        }
        if (!value || typeof value !== "object") return;
        const reference = parseObjectRef(value);
        if (reference && isReferenceField(segments, objectType)) {
          const fieldPath = `/${segments.map(String).join("/")}`;
          const key = [
            objectType,
            sourceObjectId,
            fieldPath,
            reference.object_type,
            reference.object_id,
          ].join("|");
          if (!seen.has(key)) {
            seen.add(key);
            edges.push({
              source_object_id: sourceObjectId,
              source_object_type: objectType,
              target_object_id: reference.object_id,
              target_object_type: reference.object_type,
              field_path: fieldPath,
              field_label: fieldLabelFor(segments, objectType),
            });
          }
          return;
        }
        for (const [key, child] of Object.entries(value)) {
          if (key === "object_type" || key === "object_id") continue;
          visit(child, [...segments, key]);
        }
      };

      visit(item, [collection, index]);
    });
  }

  return edges;
}

/** Reverse lookup: objectId -> every field anywhere that points at it. */
export function buildReferenceIndex(document: CaseFileDocument): ReferenceIndex {
  const index: ReferenceIndex = new Map();
  for (const edge of collectDocumentReferenceEdges(document)) {
    const entries = index.get(edge.target_object_id) ?? [];
    entries.push(edge);
    index.set(edge.target_object_id, entries);
  }
  return index;
}
