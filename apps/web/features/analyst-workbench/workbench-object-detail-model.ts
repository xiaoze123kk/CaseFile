import type { CaseFileDocument } from "@/lib/api-client";

import {
  REFERENCE_FIELD_LABELS,
  type TypedObjectRef,
  walkObjectReferences,
} from "./workbench-reference-index";
import {
  classificationLabel,
  conclusionSlotLabel,
  confidenceLabel,
  confirmationStatusLabel,
  creatorDescription,
  creatorLabel,
  creatorText,
  formatCaseWallClock,
  objectSubtypeLabel,
  objectTypeLabel,
  reliabilityLabel,
} from "./workbench-presenters";

export type DetailCollection =
  | "resolution_specs"
  | "entities"
  | "information_units"
  | "events"
  | "locations"
  | "hypotheses";

export type DetailObject = CaseFileDocument[DetailCollection][number];
export type { TypedObjectRef } from "./workbench-reference-index";

export interface DetailReference {
  id: string;
  kindLabel: string;
  label: string;
  missing: boolean;
  selectable: boolean;
}

export interface DetailKnowledgeState {
  asOf: DetailReference;
  known: DetailReference[];
  believes: DetailReference[];
  falseBeliefs: DetailReference[];
}

export type DetailField =
  | { kind: "text"; label: string; value: string }
  | { kind: "list"; label: string; values: string[] }
  | { kind: "references"; label: string; references: DetailReference[] }
  | { kind: "knowledge_states"; label: string; states: DetailKnowledgeState[] };

export interface DetailSection {
  fields: DetailField[];
  title: string;
}

export interface DetailRelationship {
  counterpart: DetailReference;
  title: string;
}

export interface DetailStructureLock {
  fields: string[];
  reason: string;
  title: string;
}

export interface ObjectDetailModel {
  collection: DetailCollection;
  confidence: number | null;
  confidenceLabel: string;
  confirmationLabel: string;
  coreSections: DetailSection[];
  description: string;
  id: string;
  kindLabel: string;
  moreSections: DetailSection[];
  references: DetailReference[];
  relationships: DetailRelationship[];
  revision: number;
  sourceReferences: DetailReference[];
  structureLocks: DetailStructureLock[];
  subtypeLabel: string;
  technicalDetails: Array<{ label: string; value: string }>;
  title: string;
}

export const detailCollections: DetailCollection[] = [
  "resolution_specs",
  "entities",
  "information_units",
  "events",
  "locations",
  "hypotheses",
];

const collectionLabels: Record<DetailCollection, string> = {
  resolution_specs: "核心问题",
  entities: "实体",
  information_units: "信息",
  events: "事件",
  locations: "地点",
  hypotheses: "假设",
};

const collectionObjectTypes: Record<DetailCollection, string> = {
  resolution_specs: "resolution_spec",
  entities: "entity",
  information_units: "information_unit",
  events: "event",
  locations: "location",
  hypotheses: "hypothesis",
};

const fieldPathLabels: Record<string, string> = {
  ...REFERENCE_FIELD_LABELS,
  aliases: "别名",
  availability: "可获得性",
  capabilities: "能力",
  classification: "叙事分类",
  content: "正文",
  description: "说明",
  entity_type: "实体类型",
  goals: "目标",
  information_type: "信息类型",
  knowledge_states: "知识状态",
  name: "名称",
  proposition: "命题",
  reliability: "可靠度",
  score: "支持度",
  secrets: "秘密",
  spatial_position: "空间位置",
  status: "状态",
  tags: "标签",
  time: "卷宗时间",
  title: "标题",
  traits: "特征",
  truth_status: "事实状态",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asObjectRef(value: unknown): TypedObjectRef | null {
  const record = asRecord(value);
  if (!record) return null;
  return typeof record.object_id === "string" && typeof record.object_type === "string"
    ? { object_id: record.object_id, object_type: record.object_type }
    : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(stringValue).filter(Boolean)
    : [];
}

function textField(label: string, value: unknown): DetailField | null {
  const text = stringValue(value);
  return text ? { kind: "text", label, value: text } : null;
}

function listField(label: string, value: unknown): DetailField | null {
  const values = stringList(value);
  return values.length ? { kind: "list", label, values } : null;
}

function compact<T>(values: Array<T | null>): T[] {
  return values.filter((value): value is T => value !== null);
}

function section(title: string, fields: Array<DetailField | null>): DetailSection | null {
  const presentFields = compact(fields);
  return presentFields.length ? { title, fields: presentFields } : null;
}

function titleFor(
  record: Record<string, unknown>,
  objectType: string,
  index = 0,
): string {
  return creatorLabel(
    stringValue(record.name) ||
      stringValue(record.title) ||
      stringValue(record.statement) ||
      stringValue(record.proposition),
    {
      kind: objectType,
      index,
      description: stringValue(record.description),
    },
  );
}

function creatorTextField(
  label: string,
  value: unknown,
  fallback: string,
): DetailField | null {
  const text = stringValue(value);
  return text ? { kind: "text", label, value: creatorText(text, fallback) } : null;
}

function creatorListField(
  label: string,
  value: unknown,
): DetailField | null {
  const values = stringList(value).map((item, index) =>
    creatorText(item, `${label} ${index + 1} 待补充`),
  );
  return values.length ? { kind: "list", label, values } : null;
}

function catalogEntry(
  object: Record<string, unknown>,
  objectType: string,
  selectable: boolean,
): DetailReference | null {
  const id = stringValue(object.id);
  if (!id) return null;
  return {
    id,
    kindLabel: objectTypeLabel(objectType),
    label: titleFor(object, objectType),
    missing: false,
    selectable,
  };
}

export function buildReferenceCatalog(document: CaseFileDocument): Map<string, DetailReference> {
  const entries = [
    ...document.resolution_specs.map((item) => catalogEntry(item, "resolution_spec", true)),
    ...document.entities.map((item) => catalogEntry(item, "entity", true)),
    ...document.relationships.map((item) => catalogEntry(item, "relationship", false)),
    ...document.locations.map((item) => catalogEntry(item, "location", true)),
    ...document.events.map((item) => catalogEntry(item, "event", true)),
    ...document.information_units.map((item) => catalogEntry(item, "information_unit", true)),
    ...document.claims.map((item) => catalogEntry(item, "claim", false)),
    ...document.hypotheses.map((item) => catalogEntry(item, "hypothesis", true)),
    ...document.reasoning_paths.map((item) => catalogEntry(item, "reasoning_path", false)),
    ...document.constraints.map((item) => catalogEntry(item, "constraint", false)),
    ...document.structure_locks.map((item) => catalogEntry(item, "structure_lock", false)),
  ].filter((entry): entry is DetailReference => entry !== null);
  return new Map(entries.map((entry) => [entry.id, entry]));
}

export function resolveReference(
  ref: TypedObjectRef,
  catalog: Map<string, DetailReference>,
): DetailReference {
  const known = catalog.get(ref.object_id);
  if (known) return known;
  const kindLabel = objectTypeLabel(ref.object_type);
  return {
    id: ref.object_id,
    kindLabel,
    label:
      ref.object_type === "source_fragment"
        ? "来源片段尚未载入"
        : `已缺失的${kindLabel}`,
    missing: true,
    selectable: false,
  };
}

function referencesFor(
  value: unknown,
  catalog: Map<string, DetailReference>,
): DetailReference[] {
  const values = Array.isArray(value) ? value : [value];
  const seen = new Set<string>();
  return values.flatMap((item) => {
    const ref = asObjectRef(item);
    if (!ref || seen.has(ref.object_id)) return [];
    seen.add(ref.object_id);
    return [resolveReference(ref, catalog)];
  });
}

function referenceField(
  label: string,
  value: unknown,
  catalog: Map<string, DetailReference>,
): DetailField | null {
  const references = referencesFor(value, catalog);
  return references.length ? { kind: "references", label, references } : null;
}

function findDetailObject(
  document: CaseFileDocument,
  objectId: string | null,
): { collection: DetailCollection; object: DetailObject } | null {
  if (!objectId) return null;
  for (const collection of detailCollections) {
    const object = document[collection].find((item) => item.id === objectId);
    if (object) return { collection, object: object as DetailObject };
  }
  return null;
}

function subtypeFor(
  collection: DetailCollection,
  object: Record<string, unknown>,
): string {
  if (collection === "resolution_specs") {
    const conclusion = asRecord(object.conclusion);
    return conclusion ? stringValue(conclusion.review_status) : "missing";
  }
  if (collection === "entities") return stringValue(object.entity_type);
  if (collection === "information_units") return stringValue(object.information_type);
  if (collection === "events") return stringValue(object.truth_status);
  if (collection === "locations") {
    const spatial = asRecord(object.spatial_position);
    return stringValue(spatial?.coordinate_system) || "topology";
  }
  return stringValue(object.status);
}

function formatSpatialPosition(value: unknown): string {
  const position = asRecord(value);
  if (!position) return "未标注空间位置";
  if (position.coordinate_system === "schematic") {
    return `示意位置：横向 ${String(position.x)} · 纵向 ${String(position.y)}`;
  }
  if (position.coordinate_system === "wgs84") {
    return `地理坐标：纬度 ${String(position.latitude)} · 经度 ${String(position.longitude)}`;
  }
  return "未标注空间位置";
}

const STORY_START_EVENT: DetailReference = {
  id: "",
  kindLabel: "事件",
  label: "卷宗起点",
  missing: false,
  selectable: false,
};

function knowledgeStatesField(
  value: unknown,
  catalog: Map<string, DetailReference>,
): DetailField | null {
  if (!Array.isArray(value)) return null;
  const states = value.flatMap((item) => {
    const state = asRecord(item);
    if (!state) return [];
    const asOf = asObjectRef(state.as_of_event_ref);
    return [{
      asOf: asOf ? resolveReference(asOf, catalog) : STORY_START_EVENT,
      known: referencesFor(state.knows_refs, catalog),
      believes: referencesFor(state.believes_refs, catalog),
      falseBeliefs: referencesFor(state.false_belief_refs, catalog),
    }];
  });
  return states.length
    ? { kind: "knowledge_states" as const, label: "知识状态", states }
    : null;
}

function formatTravelTimes(
  value: unknown,
  catalog: Map<string, DetailReference>,
): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const travel = asRecord(item);
    const destination = travel ? asObjectRef(travel.to_ref) : null;
    const minutes = travel?.minutes;
    if (!destination || typeof minutes !== "number") return [];
    return [`前往 ${resolveReference(destination, catalog).label}约 ${minutes} 分钟`];
  });
}

function allReferences(object: unknown): TypedObjectRef[] {
  const references = new Map<string, TypedObjectRef>();
  walkObjectReferences(object, (reference) => {
    references.set(`${reference.object_type}:${reference.object_id}`, reference);
  });
  return [...references.values()];
}

function fieldPathLabel(value: unknown): string {
  const path = stringValue(value);
  if (!path) return "未标注字段";
  const segments = path
    .split("/")
    .filter(Boolean)
    .map((segment) => segment.replaceAll("~1", "/").replaceAll("~0", "~"));
  return segments.map((segment) => fieldPathLabels[segment] ?? segment).join(" / ");
}

function structureLocksFor(
  document: CaseFileDocument,
  objectId: string,
): DetailStructureLock[] {
  return document.structure_locks.flatMap((lock) => {
    if (asObjectRef(lock.object_ref)?.object_id !== objectId) return [];
    return [{
      title: creatorLabel(lock.title, {
        kind: "structure_lock",
        index: document.structure_locks.findIndex((item) => item.id === lock.id),
        description: lock.description,
      }),
      reason: creatorText(lock.reason, "该结构约束的说明待补充。"),
      fields: lock.field_paths.map(fieldPathLabel),
    }];
  });
}

function relationshipsFor(
  document: CaseFileDocument,
  objectId: string,
  catalog: Map<string, DetailReference>,
): DetailRelationship[] {
  return document.relationships.flatMap((relationship) => {
    const from = asObjectRef(relationship.from_ref);
    const to = asObjectRef(relationship.to_ref);
    const counterpart = from?.object_id === objectId ? to : to?.object_id === objectId ? from : null;
    if (!counterpart) return [];
    return [{
      title: creatorLabel(relationship.title, {
        kind: "relationship",
        index: document.relationships.findIndex((item) => item.id === relationship.id),
        description: relationship.description,
      }),
      counterpart: resolveReference(counterpart, catalog),
    }];
  });
}

function eventReasoningSection(
  document: CaseFileDocument,
  eventId: string,
  catalog: Map<string, DetailReference>,
): DetailSection | null {
  const information = document.information_units.filter(
    (item) => asObjectRef(item.source_event_ref)?.object_id === eventId,
  );
  const informationIds = new Set(information.map((item) => item.id));
  const claimRefs = information.flatMap((item) =>
    Array.isArray(item.supports_claim_refs)
      ? item.supports_claim_refs.flatMap((value) => asObjectRef(value) ?? [])
      : [],
  );
  const claimIds = new Set(claimRefs.map((item) => item.object_id));
  const hypotheses = document.hypotheses.filter((hypothesis) =>
    hypothesis.required_claim_refs.some((value) =>
      claimIds.has(asObjectRef(value)?.object_id ?? ""),
    ) || (Array.isArray(hypothesis.evidence_assessments) &&
      hypothesis.evidence_assessments.some((assessment) =>
        informationIds.has(asObjectRef(assessment.information_ref)?.object_id ?? ""),
      )),
  );
  const hypothesisRefs = hypotheses.map((item): TypedObjectRef => ({
    object_type: "hypothesis",
    object_id: item.id,
  }));
  const resolutionIds = new Set([
    ...hypotheses.flatMap((item) => {
      const reference = asObjectRef(item.target_resolution_ref);
      return reference ? [reference.object_id] : [];
    }),
    ...document.resolution_specs.flatMap((resolution) =>
      resolution.required_claim_refs.some((value) =>
        claimIds.has(asObjectRef(value)?.object_id ?? ""),
      ) ? [resolution.id] : [],
    ),
  ]);
  const resolutionRefs = [...resolutionIds].map((objectId): TypedObjectRef => ({
    object_type: "resolution_spec",
    object_id: objectId,
  }));
  return section("推理影响", [
    referenceField("支持论断", claimRefs, catalog),
    referenceField("关联假设", hypothesisRefs, catalog),
    referenceField("待解问题", resolutionRefs, catalog),
  ]);
}

function sectionsFor(
  collection: DetailCollection,
  object: Record<string, unknown>,
  catalog: Map<string, DetailReference>,
): { coreSections: DetailSection[]; moreSections: DetailSection[] } {
  const commonMore = [section("补充信息", [creatorListField("标签", object.tags)])].filter(
    (item): item is DetailSection => item !== null,
  );

  if (collection === "resolution_specs") {
    const conclusion = asRecord(object.conclusion);
    const conclusionValues = Array.isArray(conclusion?.values)
      ? conclusion.values.flatMap((item) => {
          const value = asRecord(item);
          if (!value) return [];
          const answer = asObjectRef(value.value);
          return [
            answer
              ? `${conclusionSlotLabel(stringValue(value.slot_id))}：${resolveReference(answer, catalog).label}`
              : `${conclusionSlotLabel(stringValue(value.slot_id))}：${String(value.value ?? "未填写")}`,
          ];
        })
      : [];
    return {
      coreSections: compact([
        section("核心问题", [
          creatorTextField("待解问题", object.reasoning_question, "核心问题待补充。"),
          textField("解答模式", objectSubtypeLabel(stringValue(object.conclusion_mode))),
        ]),
        conclusion
          ? section("当前结论", [
              textField(
                "裁决状态",
                stringValue(conclusion.review_status) === "confirmed"
                  ? "作者已确认"
                  : "待作者确认",
              ),
              textField(
                "结论类型",
                stringValue(conclusion.outcome) === "undetermined" ? "未定论" : "答案",
              ),
              creatorTextField("结论摘要", conclusion.summary, "当前结论摘要待补充。"),
              listField("答案槽位", conclusionValues),
              creatorTextField("裁决依据", conclusion.rationale, "当前结论依据待补充。"),
              creatorListField("未解决缺口", conclusion.unresolved_gaps),
            ])
          : section("当前结论", [
              textField("裁决状态", "尚未形成结论"),
              textField("下一步", "填写答案或未定论，并关联同题假设与有效推理路径。"),
            ]),
      ]),
      moreSections: [
        ...compact([
          section("解答约束", [
            listField(
              "必填槽位",
              Array.isArray(object.required_slots)
                ? object.required_slots.map((item) => {
                    const slot = asRecord(item);
                    return slot
                      ? `${conclusionSlotLabel(stringValue(slot.slot_id))} · ${objectSubtypeLabel(stringValue(slot.value_type))}${slot.required ? " · 必填" : ""}`
                      : "";
                  }).filter(Boolean)
                : [],
            ),
            referenceField("必需论断", object.required_claim_refs, catalog),
            referenceField("获选假设", conclusion?.selected_hypothesis_refs, catalog),
            referenceField("依据路径", conclusion?.supporting_reasoning_path_refs, catalog),
          ]),
        ]),
        ...commonMore,
      ],
    };
  }

  if (collection === "entities") {
    return {
      coreSections: compact([
        section("核心信息", [
          creatorListField("别名", object.aliases),
          creatorListField("特征", object.traits),
        ]),
      ]),
      moreSections: [
        ...compact([
          section("人物与实体设定", [
            creatorListField("目标", object.goals),
            creatorListField("秘密", object.secrets),
            creatorListField("能力", object.capabilities),
            knowledgeStatesField(object.knowledge_states, catalog),
          ]),
        ]),
        ...commonMore,
      ],
    };
  }

  if (collection === "information_units") {
    const availability = asRecord(object.availability);
    return {
      coreSections: compact([
        section("信息内容", [creatorTextField("正文", object.content, "信息正文待补充。")]),
        section("判断", [
          textField("可靠度", reliabilityLabel(stringValue(object.reliability))),
          textField("事实状态", objectSubtypeLabel(stringValue(object.truth_status))),
          textField("叙事分类", classificationLabel(stringValue(object.classification))),
        ]),
      ]),
      moreSections: [
        ...compact([
          section("来源与推理", [
            referenceField("来源事件", object.source_event_ref, catalog),
            referenceField("支持论断", object.supports_claim_refs, catalog),
            referenceField("反驳论断", object.refutes_claim_refs, catalog),
          ]),
          section("获得条件", [
            referenceField("可获得者", availability?.perspective_refs, catalog),
            creatorListField("获得条件", availability?.acquisition_conditions),
            referenceField("替代路径", availability?.alternative_path_refs, catalog),
          ]),
        ]),
        ...commonMore,
      ],
    };
  }

  if (collection === "events") {
    const time = asRecord(object.time);
    return {
      coreSections: compact([
        section("发生信息", [
          textField("卷宗时间", formatCaseWallClock(stringValue(time?.start))),
          textField("时间精度", objectSubtypeLabel(stringValue(time?.precision))),
          textField("事实状态", objectSubtypeLabel(stringValue(object.truth_status))),
          referenceField("发生地点", object.location_ref, catalog),
          referenceField("参与者", object.participant_refs, catalog),
        ]),
      ]),
      moreSections: [
        ...compact([
          section("因果与观察", [
            referenceField("原因事件", object.cause_refs, catalog),
            referenceField("结果事件", object.effect_refs, catalog),
            referenceField("观察者", object.observed_by_refs, catalog),
          ]),
        ]),
        ...commonMore,
      ],
    };
  }

  if (collection === "locations") {
    return {
      coreSections: compact([
        section("空间信息", [
          textField("空间方式", formatSpatialPosition(object.spatial_position)),
          referenceField("上级地点", object.parent_ref, catalog),
        ]),
      ]),
      moreSections: [
        ...compact([
          section("地点规则", [
            referenceField("相邻地点", object.adjacency_refs, catalog),
            creatorListField("通行规则", object.access_rules),
            listField("移动时间", formatTravelTimes(object.travel_times, catalog)),
            creatorListField("可见性规则", object.visibility_rules),
          ]),
        ]),
        ...commonMore,
      ],
    };
  }

  return {
    coreSections: compact([
      section("假设内容", [
        creatorTextField("命题", object.proposition, "假设命题待补充。"),
      ]),
      section("判断", [
        textField("状态", objectSubtypeLabel(stringValue(object.status))),
        typeof object.score === "number"
          ? { kind: "text" as const, label: "支持度", value: `${Math.round(object.score * 100)}%` }
          : null,
      ]),
    ]),
    moreSections: [
      ...compact([
        section("推理条件", [
          referenceField("目标问题", object.target_resolution_ref, catalog),
          referenceField("必要依据", object.required_claim_refs, catalog),
          referenceField("证伪条件", object.falsifier_refs, catalog),
          referenceField("竞争假设", object.competing_hypothesis_refs, catalog),
        ]),
      ]),
      ...commonMore,
    ],
  };
}

export function findWorkbenchDetailObject(
  document: CaseFileDocument,
  objectId: string | null,
) {
  return findDetailObject(document, objectId);
}

export function buildObjectDetailModel(
  document: CaseFileDocument,
  objectId: string | null,
): ObjectDetailModel | null {
  const selected = findDetailObject(document, objectId);
  if (!selected) return null;
  const { collection, object } = selected;
  const record = object as Record<string, unknown>;
  const catalog = buildReferenceCatalog(document);
  const sourceReferences = referencesFor(record.source_refs, catalog);
  const sourceReferenceIds = new Set(sourceReferences.map((reference) => reference.id));
  const references = allReferences(record)
    .filter(
      (reference) =>
        reference.object_id !== object.id &&
        !sourceReferenceIds.has(reference.object_id),
    )
    .map((reference) => resolveReference(reference, catalog));
  const { coreSections, moreSections } = sectionsFor(collection, record, catalog);
  const eventReasoning = collection === "events"
    ? eventReasoningSection(document, object.id, catalog)
    : null;
  const subtype = subtypeFor(collection, record);
  const createdBy = asRecord(record.created_by);

  return {
    collection,
    confidence: typeof record.confidence === "number" ? record.confidence : null,
    confidenceLabel: confidenceLabel(
      typeof record.confidence === "number" ? record.confidence : null,
    ),
    confirmationLabel: confirmationStatusLabel(stringValue(record.confirmation_status)),
    coreSections,
    description: creatorDescription(
      stringValue(record.description),
      collectionObjectTypes[collection],
    ),
    id: object.id,
    kindLabel: collectionLabels[collection],
    moreSections: eventReasoning ? [eventReasoning, ...moreSections] : moreSections,
    references,
    relationships: relationshipsFor(document, object.id, catalog),
    revision: typeof record.revision === "number" ? record.revision : 0,
    sourceReferences,
    structureLocks: structureLocksFor(document, object.id),
    subtypeLabel: objectSubtypeLabel(subtype),
    technicalDetails: compact([
      textField("稳定编号", object.id) as Extract<DetailField, { kind: "text" }> | null,
      textField("对象修订", typeof record.revision === "number" ? `R${record.revision}` : "" ) as Extract<DetailField, { kind: "text" }> | null,
      textField("原始类型", subtype) as Extract<DetailField, { kind: "text" }> | null,
      textField(
        "创建者",
        createdBy
          ? `${stringValue(createdBy.actor_type)} · ${stringValue(createdBy.actor_id)}`
          : "",
      ) as Extract<DetailField, { kind: "text" }> | null,
      textField("更新时间", record.updated_at) as Extract<DetailField, { kind: "text" }> | null,
    ]).map((field) => ({ label: field.label, value: field.value })),
    title: titleFor(
      record,
      collectionObjectTypes[collection],
      document[collection].findIndex((item) => item.id === object.id),
    ),
  };
}
