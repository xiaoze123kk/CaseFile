import type { CaseFileDocument } from "@/lib/api-client";

import {
  buildReferenceCatalog,
  resolveReference,
  type DetailReference,
} from "./workbench-object-detail-model";
import {
  buildReferenceIndex,
  collectDocumentReferenceEdges,
  parseObjectRef,
  type DocumentReferenceEdge,
  type TypedObjectRef,
} from "./workbench-reference-index";

export type ContextRelationGroupId =
  | "direct"
  | "events"
  | "information"
  | "reasoning";

export type ContextRelationArrow = "→" | "—" | "⇄";

export interface ContextRelationEndpoint {
  id: string;
  objectType: string;
  kindLabel: string;
  label: string;
  missing: boolean;
  selectable: boolean;
}

export interface ContextRelation {
  id: string;
  group: ContextRelationGroupId;
  /** Natural-language action, e.g. 参与了 / 来源于 / 支持. */
  verb: string;
  arrow: ContextRelationArrow;
  fieldLabel: string;
  /** Raw field path when the relation comes from a reference field. */
  fieldPath: string | null;
  /** Display subject and object for the verb sentence. */
  subject: ContextRelationEndpoint;
  object: ContextRelationEndpoint;
  /** The endpoint that is not the currently selected object. */
  counterpart: ContextRelationEndpoint;
}

export interface ContextIncomingReference {
  id: string;
  source: ContextRelationEndpoint;
  sourceObjectId: string;
  fieldLabel: string;
  fieldPath: string;
}

export interface ContextRelationGroup {
  id: ContextRelationGroupId;
  title: string;
  relations: ContextRelation[];
}

export interface ContextRelationModel {
  groups: ContextRelationGroup[];
  incoming: ContextIncomingReference[];
  totals: {
    all: number;
    direct: number;
    events: number;
    information: number;
    reasoning: number;
    incoming: number;
  };
}

interface FieldRelationSemantics {
  group: ContextRelationGroupId;
  fieldLabel: string;
  /** Which endpoint is the natural subject of the verb sentence. */
  subject: "source" | "target";
  verb: string;
  arrow?: ContextRelationArrow;
}

const GROUP_TITLES: Record<ContextRelationGroupId, string> = {
  direct: "直接关系",
  events: "参与事件",
  information: "信息来源",
  reasoning: "推理作用",
};

const RELATION_SEMANTICS_BY_FIELD: Record<string, FieldRelationSemantics> = {
  "event:participant_refs": {
    group: "events", fieldLabel: "参与者", subject: "target", verb: "参与了",
  },
  "event:location_ref": {
    group: "events", fieldLabel: "发生地点", subject: "source", verb: "发生在",
  },
  "event:cause_refs": {
    group: "events", fieldLabel: "原因事件", subject: "target", verb: "导致",
  },
  "event:effect_refs": {
    group: "events", fieldLabel: "结果事件", subject: "source", verb: "导致",
  },
  "event:observed_by_refs": {
    group: "events", fieldLabel: "观察者", subject: "target", verb: "发现",
  },
  "event:anchor_event_ref": {
    group: "events", fieldLabel: "时间锚点", subject: "source", verb: "时间锚定于",
  },
  "entity:as_of_event_ref": {
    group: "events", fieldLabel: "认知时点", subject: "source", verb: "截至",
  },
  "entity:knows_refs": {
    group: "information", fieldLabel: "已知", subject: "source", verb: "知道",
  },
  "entity:believes_refs": {
    group: "information", fieldLabel: "相信", subject: "source", verb: "相信",
  },
  "entity:false_belief_refs": {
    group: "information", fieldLabel: "错误认知", subject: "source", verb: "误以为",
  },
  "information_unit:source_event_ref": {
    group: "information", fieldLabel: "来源事件", subject: "source", verb: "来源于",
  },
  "information_unit:perspective_refs": {
    group: "information", fieldLabel: "可获得者", subject: "target", verb: "可获得",
  },
  "information_unit:alternative_path_refs": {
    group: "information", fieldLabel: "替代路径", subject: "source", verb: "替代路径为",
  },
  "information_unit:supports_claim_refs": {
    group: "reasoning", fieldLabel: "支持论断", subject: "source", verb: "支持",
  },
  "information_unit:refutes_claim_refs": {
    group: "reasoning", fieldLabel: "反驳论断", subject: "source", verb: "反驳",
  },
  "claim:support_refs": {
    group: "reasoning", fieldLabel: "支持依据", subject: "target", verb: "支持",
  },
  "claim:refute_refs": {
    group: "reasoning", fieldLabel: "反驳依据", subject: "target", verb: "反驳",
  },
  "claim:dependency_claim_refs": {
    group: "reasoning", fieldLabel: "依赖论断", subject: "source", verb: "依赖",
  },
  "hypothesis:target_resolution_ref": {
    group: "reasoning", fieldLabel: "目标问题", subject: "source", verb: "试图回答",
  },
  "hypothesis:required_claim_refs": {
    group: "reasoning", fieldLabel: "必需论断", subject: "source", verb: "需要依据",
  },
  "hypothesis:falsifier_refs": {
    group: "reasoning", fieldLabel: "证伪条件", subject: "target", verb: "可证伪",
  },
  "hypothesis:competing_hypothesis_refs": {
    group: "reasoning", fieldLabel: "竞争假设", subject: "source", verb: "竞争", arrow: "—",
  },
  "hypothesis:information_ref": {
    group: "reasoning", fieldLabel: "评估信息", subject: "source", verb: "评估了",
  },
  "resolution_spec:required_claim_refs": {
    group: "reasoning", fieldLabel: "必需论断", subject: "source", verb: "需要依据",
  },
  "resolution_spec:accepted_answers": {
    group: "reasoning", fieldLabel: "已接受答案", subject: "source", verb: "接受答案为",
  },
  "resolution_spec:selected_hypothesis_refs": {
    group: "reasoning", fieldLabel: "获选假设", subject: "source", verb: "选中",
  },
  "resolution_spec:supporting_reasoning_path_refs": {
    group: "reasoning", fieldLabel: "依据路径", subject: "source", verb: "依据路径为",
  },
  "resolution_spec:value": {
    group: "reasoning", fieldLabel: "结论答案", subject: "source", verb: "结论指向",
  },
  "reasoning_path:target_ref": {
    group: "reasoning", fieldLabel: "推导目标", subject: "source", verb: "推导目标为",
  },
  "reasoning_path:input_refs": {
    group: "reasoning", fieldLabel: "推理输入", subject: "source", verb: "使用依据",
  },
  "reasoning_path:output_ref": {
    group: "reasoning", fieldLabel: "推理输出", subject: "source", verb: "推导出",
  },
  "reasoning_path:alternative_path_refs": {
    group: "reasoning", fieldLabel: "替代路径", subject: "source", verb: "替代路径为", arrow: "—",
  },
  "constraint:scope_refs": {
    group: "reasoning", fieldLabel: "约束范围", subject: "source", verb: "约束范围包括",
  },
  "constraint:conflict_refs": {
    group: "reasoning", fieldLabel: "冲突对象", subject: "source", verb: "冲突于", arrow: "—",
  },
  "location:parent_ref": {
    group: "direct", fieldLabel: "上级地点", subject: "source", verb: "位于",
  },
  "location:adjacency_refs": {
    group: "direct", fieldLabel: "相邻地点", subject: "source", verb: "相邻", arrow: "—",
  },
  "location:to_ref": {
    group: "direct", fieldLabel: "前往地点", subject: "source", verb: "可前往",
  },
  "structure_lock:object_ref": {
    group: "direct", fieldLabel: "锁定对象", subject: "source", verb: "锁定对象为",
  },
};

function semanticsFor(edge: DocumentReferenceEdge): FieldRelationSemantics {
  const segments = edge.field_path.split("/");
  const field = [...segments].reverse().find((segment) =>
    /[^0-9]/u.test(segment),
  ) ?? edge.field_path;
  const scoped = `${edge.source_object_type}:${field}`;
  const semantics = RELATION_SEMANTICS_BY_FIELD[scoped];
  if (semantics) return semantics;
  return {
    group: "direct",
    fieldLabel: edge.field_label,
    subject: "source",
    verb: "关联",
  };
}

function endpointFor(
  reference: TypedObjectRef,
  catalog: Map<string, DetailReference>,
): ContextRelationEndpoint {
  const resolved = resolveReference(reference, catalog);
  return {
    id: resolved.id,
    objectType: reference.object_type,
    kindLabel: resolved.kindLabel,
    label: resolved.label,
    missing: resolved.missing,
    selectable: resolved.selectable,
  };
}

function relationKey(relation: {
  group: ContextRelationGroupId;
  verb: string;
  subject: ContextRelationEndpoint;
  object: ContextRelationEndpoint;
}) {
  return [
    relation.group,
    relation.verb,
    relation.subject.id,
    relation.object.id,
  ].join("|");
}

function relationshipArrow(direction: string): ContextRelationArrow {
  if (direction === "directed") return "→";
  if (direction === "bidirectional") return "⇄";
  return "—";
}

export function buildContextRelations(
  document: CaseFileDocument,
  objectId: string,
): ContextRelationModel {
  const catalog = buildReferenceCatalog(document);
  const relations: ContextRelation[] = [];
  const seenRelations = new Set<string>();

  for (const edge of collectDocumentReferenceEdges(document)) {
    const isSource = edge.source_object_id === objectId;
    const isTarget = edge.target_object_id === objectId;
    if ((!isSource && !isTarget) || (isSource && isTarget)) continue;
    if (edge.source_object_type === "relationship") continue;
    if (edge.target_object_type === "source_fragment") continue;
    if (edge.field_path.includes("/source_refs/")) continue;

    const semantics = semanticsFor(edge);
    const sourceRef = {
      object_type: edge.source_object_type,
      object_id: edge.source_object_id,
    };
    const targetRef = {
      object_type: edge.target_object_type,
      object_id: edge.target_object_id,
    };
    const source = endpointFor(sourceRef, catalog);
    const target = endpointFor(targetRef, catalog);
    const subject = semantics.subject === "source" ? source : target;
    const object = semantics.subject === "source" ? target : source;
    const relation: ContextRelation = {
      id: `field:${edge.source_object_type}:${edge.source_object_id}:${edge.field_path}`,
      group: semantics.group,
      verb: semantics.verb,
      arrow: semantics.arrow ?? "→",
      fieldLabel: semantics.fieldLabel,
      fieldPath: edge.field_path,
      subject,
      object,
      counterpart: isSource ? target : source,
    };
    const key = relationKey(relation);
    if (seenRelations.has(key)) continue;
    seenRelations.add(key);
    relations.push(relation);
  }

  for (const relationship of document.relationships) {
    const from = parseObjectRef(relationship.from_ref);
    const to = parseObjectRef(relationship.to_ref);
    if (!from || !to) continue;
    const isFrom = from.object_id === objectId;
    const isTo = to.object_id === objectId;
    if (!isFrom && !isTo) continue;

    const fromEndpoint = endpointFor(from, catalog);
    const toEndpoint = endpointFor(to, catalog);
    const relation: ContextRelation = {
      id: `relationship:${relationship.id}`,
      group: "direct",
      verb: relationship.title.trim() || "关联",
      arrow: relationshipArrow(relationship.direction),
      fieldLabel: relationship.direction === "directed"
        ? "有向关系"
        : relationship.direction === "bidirectional"
          ? "双向关系"
          : "无向关系",
      fieldPath: null,
      subject: fromEndpoint,
      object: toEndpoint,
      counterpart: isFrom ? toEndpoint : fromEndpoint,
    };
    const key = relationKey(relation);
    if (seenRelations.has(key)) continue;
    seenRelations.add(key);
    relations.push(relation);
  }

  const groups: ContextRelationGroup[] = (
    ["direct", "events", "information", "reasoning"] as const
  ).flatMap((groupId) => {
    const members = relations.filter((relation) => relation.group === groupId);
    return members.length
      ? [{ id: groupId, title: GROUP_TITLES[groupId], relations: members }]
      : [];
  });

  const incoming = (buildReferenceIndex(document).get(objectId) ?? [])
    .filter((edge) => edge.source_object_id !== objectId)
    .map((edge): ContextIncomingReference => ({
      id: `incoming:${edge.source_object_type}:${edge.source_object_id}:${edge.field_path}`,
      source: endpointFor({
        object_type: edge.source_object_type,
        object_id: edge.source_object_id,
      }, catalog),
      sourceObjectId: edge.source_object_id,
      fieldLabel: edge.field_label,
      fieldPath: edge.field_path,
    }));

  const countFor = (groupId: ContextRelationGroupId) =>
    relations.filter((relation) => relation.group === groupId).length;

  return {
    groups,
    incoming,
    totals: {
      all: relations.length,
      direct: countFor("direct"),
      events: countFor("events"),
      information: countFor("information"),
      reasoning: countFor("reasoning"),
      incoming: incoming.length,
    },
  };
}
