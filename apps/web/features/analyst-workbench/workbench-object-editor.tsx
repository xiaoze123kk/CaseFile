"use client";

import type { CaseFileDocument } from "@/lib/api-client";
import { useMemo, useState } from "react";

import {
  buildObjectDetailModel,
  findWorkbenchDetailObject,
  type DetailField,
  type DetailKnowledgeState,
  type DetailObject,
  type DetailReference,
  type ObjectDetailModel,
} from "./workbench-object-detail-model";
import type { ContextFieldCitation } from "./workbench-provenance-model";
import {
  classificationLabel,
  objectSubtypeLabel,
  reliabilityLabel,
} from "./workbench-presenters";
import styles from "./workbench-object-editor.module.css";

type SaveResult = "saved" | "conflict" | "error" | { status: "error"; message: string };
type SelectedDetail = NonNullable<ReturnType<typeof findWorkbenchDetailObject>>;

const truthStatuses = [
  "canon_true",
  "reported",
  "disputed",
  "false_belief",
  "unknown",
] as const;

function positionOf(object: DetailObject) {
  const value = (object as Record<string, unknown>).spatial_position;
  if (!value || typeof value !== "object") return null;
  const position = value as Record<string, unknown>;
  if (position.coordinate_system === "schematic") {
    return {
      coordinate_system: "schematic" as const,
      x: Number(position.x),
      y: Number(position.y),
    };
  }
  if (position.coordinate_system === "wgs84") {
    return {
      coordinate_system: "wgs84" as const,
      latitude: Number(position.latitude),
      longitude: Number(position.longitude),
    };
  }
  return null;
}

function editValuesFor(
  document: CaseFileDocument,
  selected: SelectedDetail,
): Record<string, string> {
  const object = selected.object as Record<string, unknown>;
  const position = selected.collection === "locations" ? positionOf(selected.object) : null;
  const conclusion = object.conclusion && typeof object.conclusion === "object"
    ? object.conclusion as Record<string, unknown>
    : selected.collection === "resolution_specs"
      ? defaultConclusionForResolution(document, object)
      : null;
  return {
    name: String(object.name ?? ""),
    title: String(object.title ?? ""),
    reasoning_question: String(object.reasoning_question ?? ""),
    description: String(object.description ?? ""),
    content: String(object.content ?? ""),
    reliability: String(object.reliability ?? "unknown"),
    truth_status: String(object.truth_status ?? "unknown"),
    classification: String(object.classification ?? "background"),
    proposition: String(object.proposition ?? ""),
    hypothesis_status: String(object.status ?? "undetermined"),
    score:
      object.score === null || object.score === undefined
        ? ""
        : String(Math.round(Number(object.score) * 100)),
    coordinate_system: position?.coordinate_system ?? "",
    x: position?.coordinate_system === "schematic" ? String(position.x) : "",
    y: position?.coordinate_system === "schematic" ? String(position.y) : "",
    latitude:
      position?.coordinate_system === "wgs84" ? String(position.latitude) : "",
    longitude:
      position?.coordinate_system === "wgs84" ? String(position.longitude) : "",
    conclusion_outcome: String(conclusion?.outcome ?? "undetermined"),
    conclusion_summary: String(conclusion?.summary ?? ""),
    conclusion_rationale: String(conclusion?.rationale ?? ""),
    conclusion_gaps: Array.isArray(conclusion?.unresolved_gaps)
      ? conclusion.unresolved_gaps.join("\n")
      : "",
    selected_hypothesis_ids: Array.isArray(conclusion?.selected_hypothesis_refs)
      ? conclusion.selected_hypothesis_refs.flatMap((item) =>
          item && typeof item === "object" && "object_id" in item
            ? [String(item.object_id)]
            : [],
        ).join("\n")
      : "",
    supporting_reasoning_path_ids: Array.isArray(conclusion?.supporting_reasoning_path_refs)
      ? conclusion.supporting_reasoning_path_refs.flatMap((item) =>
          item && typeof item === "object" && "object_id" in item
            ? [String(item.object_id)]
            : [],
        ).join("\n")
      : "",
    ...(Array.isArray(conclusion?.values)
      ? Object.fromEntries(conclusion.values.flatMap((item) => {
          if (!item || typeof item !== "object" || !("slot_id" in item) || !("value" in item)) {
            return [];
          }
          const value = item.value;
          return [[
            `conclusion_slot:${String(item.slot_id)}`,
            value && typeof value === "object" && "object_id" in value
              ? String(value.object_id)
              : String(value ?? ""),
          ]];
        }))
      : {}),
  };
}

function lines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function objectRefForId(document: CaseFileDocument, objectId: string) {
  const collections = [
    ["resolution_spec", document.resolution_specs],
    ["entity", document.entities],
    ["relationship", document.relationships],
    ["location", document.locations],
    ["event", document.events],
    ["information_unit", document.information_units],
    ["claim", document.claims],
    ["hypothesis", document.hypotheses],
    ["reasoning_path", document.reasoning_paths],
    ["constraint", document.constraints],
    ["structure_lock", document.structure_locks],
  ] as const;
  for (const [objectType, collection] of collections) {
    if (collection.some((item) => item.id === objectId)) {
      return { object_type: objectType, object_id: objectId };
    }
  }
  return null;
}

function defaultConclusionForResolution(
  document: CaseFileDocument,
  resolution: Record<string, unknown>,
) {
  const resolutionId = String(resolution.id ?? "");
  const selectedHypothesisRefs = document.hypotheses
    .filter((item) => {
      const target = item.target_resolution_ref;
      return target && "object_id" in target && target.object_id === resolutionId;
    })
    .map((item) => ({ object_type: "hypothesis", object_id: item.id }));
  const selectedHypothesisIds = new Set(selectedHypothesisRefs.map((item) => item.object_id));
  const claimById = new Map(document.claims.map((item) => [item.id, item]));
  const validClaimIds = new Set(
    [
      ...(Array.isArray(resolution.required_claim_refs) ? resolution.required_claim_refs : []),
      ...document.hypotheses
        .filter((item) => selectedHypothesisIds.has(item.id))
        .flatMap((item) => item.required_claim_refs),
    ].flatMap((reference) =>
      reference && typeof reference === "object" && "object_id" in reference
        ? [String(reference.object_id)]
        : [],
    ),
  );
  const pendingClaimIds = [...validClaimIds];
  while (pendingClaimIds.length) {
    const claim = claimById.get(pendingClaimIds.pop() ?? "");
    if (!claim) continue;
    for (const reference of claim.dependency_claim_refs) {
      const claimId = String(reference.object_id);
      if (!validClaimIds.has(claimId)) {
        validClaimIds.add(claimId);
        pendingClaimIds.push(claimId);
      }
    }
  }
  const supportingReasoningPathRefs = document.reasoning_paths
    .filter((item) => {
      const target = item.target_ref;
      if (!item.required_for_resolution || !target || !("object_id" in target)) return false;
      const targetId = String(target.object_id);
      return selectedHypothesisIds.has(targetId) ||
        validClaimIds.has(targetId) ||
        targetId === resolutionId;
    })
    .map((item) => ({ object_type: "reasoning_path", object_id: item.id }));
  return {
    outcome: "undetermined" as const,
    review_status: "proposed" as const,
    summary: "",
    values: [],
    selected_hypothesis_refs: selectedHypothesisRefs,
    supporting_reasoning_path_refs: supportingReasoningPathRefs,
    rationale: "",
    unresolved_gaps: ["请补充仍未解决的证据缺口。"],
  };
}

function KnowledgeItem({
  reference,
  onSelectObject,
}: {
  reference: DetailReference;
  onSelectObject: (objectId: string) => void;
}) {
  if (reference.selectable && !reference.missing) {
    return (
      <button
        aria-label={`查看${reference.kindLabel}“${reference.label}”`}
        className={styles.knowledgeItem}
        onClick={() => onSelectObject(reference.id)}
        title={`查看${reference.kindLabel}“${reference.label}”`}
        type="button"
      >
        {reference.label}
        <small>{reference.kindLabel}</small>
      </button>
    );
  }
  return (
    <span className={styles.knowledgeItem} data-missing={reference.missing}>
      {reference.label}
      <small>{reference.kindLabel}</small>
    </span>
  );
}

function KnowledgeCognitionLine({
  label,
  references,
  tone,
  onSelectObject,
}: {
  label: string;
  references: DetailReference[];
  tone: "known" | "believed" | "false";
  onSelectObject: (objectId: string) => void;
}) {
  return (
    <div className={styles.cognitionLine}>
      <span className={styles.cognitionLabel} data-tone={tone}>
        {label}
      </span>
      <div className={styles.cognitionValues}>
        {references.length
          ? references.map((reference) => (
              <KnowledgeItem
                key={reference.id}
                onSelectObject={onSelectObject}
                reference={reference}
              />
            ))
          : <span className={styles.cognitionEmpty}>—（无）</span>}
      </div>
    </div>
  );
}

function KnowledgeMilestone({
  state,
  onSelectObject,
}: {
  state: DetailKnowledgeState;
  onSelectObject: (objectId: string) => void;
}) {
  const eventContent = (
    <>
      <span className={styles.milestoneEventName}>{state.asOf.label}</span>
      <span className={styles.milestoneEventTag}>事件</span>
    </>
  );
  return (
    <li className={styles.knowledgeMilestone}>
      <span aria-hidden="true" className={styles.milestoneNode} />
      <div className={styles.milestoneBody}>
        <div className={styles.milestoneHeading}>
          <span className={styles.milestoneAsOf}>截至</span>
          {state.asOf.selectable && !state.asOf.missing ? (
            <button
              aria-label={`跳转查看截至事件“${state.asOf.label}”`}
              className={styles.milestoneEvent}
              onClick={() => onSelectObject(state.asOf.id)}
              type="button"
            >
              {eventContent}
            </button>
          ) : (
            <span
              className={styles.milestoneEvent}
              data-missing={state.asOf.missing}
            >
              {eventContent}
            </span>
          )}
        </div>
        <div className={styles.cognitionLines}>
          <KnowledgeCognitionLine
            label="已知"
            onSelectObject={onSelectObject}
            references={state.known}
            tone="known"
          />
          <KnowledgeCognitionLine
            label="相信"
            onSelectObject={onSelectObject}
            references={state.believes}
            tone="believed"
          />
          <KnowledgeCognitionLine
            label="错误认知"
            onSelectObject={onSelectObject}
            references={state.falseBeliefs}
            tone="false"
          />
        </div>
      </div>
    </li>
  );
}

function KnowledgeStateList({
  states,
  onSelectObject,
}: {
  states: DetailKnowledgeState[];
  onSelectObject: (objectId: string) => void;
}) {
  return (
    <ol className={styles.knowledgeTimeline}>
      {states.map((state, index) => (
        <KnowledgeMilestone
          key={`${state.asOf.id || "story-start"}-${index}`}
          onSelectObject={onSelectObject}
          state={state}
        />
      ))}
    </ol>
  );
}

function renderField(
  field: DetailField,
  onSelectObject: (objectId: string) => void,
  fieldCitations: ContextFieldCitation[],
  onOpenSources?: () => void,
) {
  if (field.kind === "text") {
    const citations = fieldCitations.filter((citation) =>
      citation.fieldLabel === field.label && citation.fieldValue === field.value,
    );
    return (
      <div key={field.label}>
        <dt>{field.label}</dt>
        <dd>
          {field.value}
          {citations.length ? (
            <span className={styles.citationChips}>
              {citations.flatMap((citation) => citation.matches.map((match) => (
                <button
                  aria-label={`来源：${match.sourceLabel}`}
                  className={styles.citationChip}
                  key={`${citation.fieldLabel}:${match.sourceRecordId}`}
                  onClick={() => onOpenSources?.()}
                  title={`${citation.fieldLabel} · 第 ${match.span.paragraphNo} 段`}
                  type="button"
                >
                  {match.sourceLabel}
                </button>
              )))}
            </span>
          ) : null}
        </dd>
      </div>
    );
  }
  if (field.kind === "list") {
    return (
      <div key={field.label}>
        <dt>{field.label}</dt>
        <dd><ul className={styles.valueList}>{field.values.map((value) => <li key={value}>{value}</li>)}</ul></dd>
      </div>
    );
  }
  if (field.kind === "knowledge_states") {
    return (
      <div className={styles.knowledgeStateField} key={field.label}>
        <dt>{field.label}</dt>
        <dd>
          <KnowledgeStateList
            onSelectObject={onSelectObject}
            states={field.states}
          />
        </dd>
      </div>
    );
  }
  return (
    <div key={field.label}>
      <dt>{field.label}</dt>
      <dd className={styles.referenceList}>
        {field.references.map((reference) => reference.selectable ? (
          <button key={reference.id} onClick={() => onSelectObject(reference.id)} type="button">
            <strong>{reference.label}</strong><small>{reference.kindLabel}</small>
          </button>
        ) : <span data-missing={reference.missing} key={reference.id}><strong>{reference.label}</strong><small>{reference.kindLabel}</small></span>)}
      </dd>
    </div>
  );
}

function renderSections(
  sections: ObjectDetailModel["coreSections"],
  onSelectObject: (objectId: string) => void,
  fieldCitations: ContextFieldCitation[],
  onOpenSources?: () => void,
) {
  return sections.map((section) => (
    <section className={styles.detailSection} key={section.title}>
      <h3>{section.title}</h3>
      <dl>{section.fields.map((field) => renderField(field, onSelectObject, fieldCitations, onOpenSources))}</dl>
    </section>
  ));
}

export function WorkbenchObjectEditor({
  document,
  selectedObjectId,
  revision,
  revisionLabel,
  saving,
  navigationNotice,
  fieldCitations = [],
  onDirtyChange,
  onSelectObject,
  onOpenSources,
  onSave,
  readOnly = false,
  readOnlyReason,
}: {
  document: CaseFileDocument;
  selectedObjectId: string | null;
  revision: number;
  revisionLabel?: string;
  saving: boolean;
  navigationNotice: string | null;
  fieldCitations?: ContextFieldCitation[];
  onDirtyChange: (dirty: boolean) => void;
  onSelectObject: (objectId: string) => void;
  onOpenSources?: () => void;
  onSave?: (objectId: string, changes: Record<string, unknown>) => Promise<SaveResult>;
  readOnly?: boolean;
  readOnlyReason?: string;
}) {
  const selected = useMemo(
    () => findWorkbenchDetailObject(document, selectedObjectId),
    [document, selectedObjectId],
  );
  const detail = useMemo(
    () => buildObjectDetailModel(document, selectedObjectId),
    [document, selectedObjectId],
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    selected ? editValuesFor(document, selected) : {},
  );
  const [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  if (!selected || !detail) {
    return (
      <div className={styles.emptyState}>
        <strong>选择一个对象开始核对</strong>
        <p>这里会显示创作信息、关联依据和可安全修改的内容。</p>
      </div>
    );
  }
  const currentSelected: SelectedDetail = selected;
  const currentDetail: ObjectDetailModel = detail;

  function change(name: string, value: string) {
    if (readOnly) return;
    setValues((current) => ({ ...current, [name]: value }));
    if (!dirty) {
      setDirty(true);
      onDirtyChange(true);
    }
    setNotice(null);
  }

  function cancelChanges() {
    setValues(editValuesFor(document, currentSelected));
    setDirty(false);
    setEditing(false);
    onDirtyChange(false);
    setNotice("已取消未保存修改。");
  }

  function field(label: string, name: string, options?: { multiline?: boolean; type?: string; min?: number; max?: number; step?: number }) {
    const multiline = options?.multiline ?? false;
    return (
      <label className={multiline ? styles.objectEditorWide : undefined}>
        <span>{label}</span>
        {multiline ? <textarea onChange={(event) => change(name, event.target.value)} readOnly={readOnly} rows={4} value={values[name] ?? ""} /> : <input max={options?.max} min={options?.min} onChange={(event) => change(name, event.target.value)} readOnly={readOnly} step={options?.step} type={options?.type ?? "text"} value={values[name] ?? ""} />}
      </label>
    );
  }

  function selectField(
    label: string,
    name: string,
    options: Array<{ label: string; value: string }>,
  ) {
    return <label><span>{label}</span><select disabled={readOnly} onChange={(event) => change(name, event.target.value)} value={values[name] ?? ""}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
  }

  function buildChanges() {
    if (currentSelected.collection === "resolution_specs") {
      const object = currentSelected.object as Record<string, unknown>;
      const existing = object.conclusion && typeof object.conclusion === "object"
        ? object.conclusion as Record<string, unknown>
        : defaultConclusionForResolution(document, object);
      const requiredSlots = Array.isArray(object.required_slots)
        ? object.required_slots.flatMap((item) =>
            item && typeof item === "object" ? [item as Record<string, unknown>] : [],
          )
        : [];
      const conclusionValues = values.conclusion_outcome === "answer"
        ? requiredSlots.flatMap((slot) => {
            const slotId = String(slot.slot_id ?? "");
            const rawValue = values[`conclusion_slot:${slotId}`]?.trim() ?? "";
            if (!rawValue) return [];
            const valueType = String(slot.value_type ?? "");
            let value: string | number | boolean | Record<string, string> = rawValue;
            if (valueType === "number") value = Number(rawValue);
            if (valueType === "boolean") value = rawValue === "true";
            if (["object_ref", "entity_or_claim_ref"].includes(valueType)) {
              value = objectRefForId(document, rawValue) ?? { object_type: "unknown", object_id: rawValue };
            }
            if (valueType === "text_or_claim_ref") {
              const reference = objectRefForId(document, rawValue);
              if (reference?.object_type === "claim") value = reference;
            }
            return [{ slot_id: slotId, value }];
          })
        : [];
      return {
        title: values.title,
        description: values.description,
        reasoning_question: values.reasoning_question,
        conclusion: {
          ...existing,
          outcome: values.conclusion_outcome,
          review_status: "proposed",
          summary: values.conclusion_summary,
          values: conclusionValues,
          selected_hypothesis_refs: lines(values.selected_hypothesis_ids).map((object_id) => ({
            object_type: "hypothesis",
            object_id,
          })),
          supporting_reasoning_path_refs: lines(
            values.supporting_reasoning_path_ids,
          ).map((object_id) => ({ object_type: "reasoning_path", object_id })),
          rationale: values.conclusion_rationale,
          unresolved_gaps: lines(values.conclusion_gaps),
        },
      };
    }
    if (currentSelected.collection === "entities") {
      return { name: values.name, description: values.description };
    }
    if (currentSelected.collection === "information_units") {
      return {
        title: values.title,
        description: values.description,
        content: values.content,
        reliability: values.reliability,
        truth_status: values.truth_status,
        classification: values.classification,
      };
    }
    if (currentSelected.collection === "events") {
      return {
        title: values.title,
        description: values.description,
        truth_status: values.truth_status,
      };
    }
    if (currentSelected.collection === "locations") {
      let spatialPosition: Record<string, unknown> | undefined;
      if (values.coordinate_system === "schematic") {
        spatialPosition = { coordinate_system: "schematic", x: Number(values.x), y: Number(values.y) };
      } else if (values.coordinate_system === "wgs84") {
        spatialPosition = { coordinate_system: "wgs84", latitude: Number(values.latitude), longitude: Number(values.longitude) };
      }
      return { name: values.name, description: values.description, ...(spatialPosition ? { spatial_position: spatialPosition } : {}) };
    }
    return {
      title: values.title,
      description: values.description,
      proposition: values.proposition,
      status: values.hypothesis_status,
      score: values.score.trim() ? Number(values.score) / 100 : null,
    };
  }

  function coordinateInvalid() {
    if (currentSelected.collection !== "locations") return false;
    if (values.coordinate_system === "schematic") {
      const x = Number(values.x);
      const y = Number(values.y);
      return !values.x.trim() || !values.y.trim() || !Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 100 || y < 0 || y > 100;
    }
    if (values.coordinate_system === "wgs84") {
      const latitude = Number(values.latitude);
      const longitude = Number(values.longitude);
      return !values.latitude.trim() || !values.longitude.trim() || !Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180;
    }
    return false;
  }

  function invalidMessage() {
    if (coordinateInvalid()) return "坐标超出允许范围，请检查后再保存。";
    if (currentSelected.collection === "hypotheses" && values.score.trim()) {
      const score = Number(values.score);
      if (!Number.isFinite(score) || score < 0 || score > 100) return "支持度必须介于 0% 与 100% 之间。";
    }
    if (currentSelected.collection === "resolution_specs") {
      if (!values.conclusion_summary.trim()) return "请填写结论摘要。";
      if (!values.conclusion_rationale.trim()) return "请填写结论依据。";
      if (values.conclusion_outcome === "undetermined" && !values.conclusion_gaps.trim()) {
        return "未定论必须说明证据缺口。";
      }
      if (!values.selected_hypothesis_ids.trim()) return "请至少关联一个同题假设。";
      if (!values.supporting_reasoning_path_ids.trim()) return "请至少关联一条有效推理路径。";
      if (values.conclusion_outcome === "answer") {
        const object = currentSelected.object as Record<string, unknown>;
        const missing = Array.isArray(object.required_slots)
          ? object.required_slots.flatMap((item) => {
              if (!item || typeof item !== "object" || !("slot_id" in item) || !item.required) {
                return [];
              }
              const slotId = String(item.slot_id);
              return values[`conclusion_slot:${slotId}`]?.trim() ? [] : [slotId];
            })
          : [];
        if (missing.length) return `请填写必填答案槽位：${missing.join("、")}。`;
      }
    }
    return null;
  }

  async function save() {
    if (!onSave) return;
    const validationMessage = invalidMessage();
    if (!dirty || validationMessage) {
      if (validationMessage) setNotice(validationMessage);
      return;
    }
    const result = await onSave(currentSelected.object.id, buildChanges());
    if (result === "saved") {
      setDirty(false);
      setEditing(false);
      onDirtyChange(false);
      setNotice("修改已写入当前工作稿。");
    } else if (result === "conflict") {
      setNotice("工作稿已更新。你的输入已保留，请核对最新版后再次保存。");
    } else if (typeof result === "object") {
      setNotice(result.message);
    } else {
      setNotice("修改未保存。请检查字段或服务状态后重试。");
    }
  }

  function renderQuickEditFields() {
    if (currentSelected.collection === "resolution_specs") return <>
      {field("标题", "title")}
      {field("说明", "description", { multiline: true })}
      {field("核心问题", "reasoning_question", { multiline: true })}
      {selectField("结论类型", "conclusion_outcome", [
        { value: "answer", label: "答案" },
        { value: "undetermined", label: "未定论" },
      ])}
      {field("结论摘要", "conclusion_summary", { multiline: true })}
      {field("裁决依据", "conclusion_rationale", { multiline: true })}
      {field("未解决缺口（每行一项）", "conclusion_gaps", { multiline: true })}
      {field("获选或并存假设 ID（每行一项）", "selected_hypothesis_ids", { multiline: true })}
      {field("依据路径 ID（每行一项）", "supporting_reasoning_path_ids", { multiline: true })}
      {values.conclusion_outcome === "answer" && Array.isArray(
        (currentSelected.object as Record<string, unknown>).required_slots,
      ) ? ((currentSelected.object as Record<string, unknown>).required_slots as Array<Record<string, unknown>>)
        .map((slot) => <div key={String(slot.slot_id)}>{field(
          `答案槽位 · ${String(slot.slot_id)}${slot.required ? "（必填）" : ""}`,
          `conclusion_slot:${String(slot.slot_id)}`,
        )}</div>) : null}
      <p className={styles.conclusionEditNote}>保存后会明确退回“待作者确认”，必须再次确认才成为最终结论。对象型槽位请填写稳定对象 ID。</p>
    </>;
    if (currentSelected.collection === "entities") return <>{field("名称", "name")}{field("说明", "description", { multiline: true })}</>;
    if (currentSelected.collection === "information_units") return <>
      {field("标题", "title")}{field("说明", "description", { multiline: true })}{field("正文", "content", { multiline: true })}
      {selectField("可靠度", "reliability", ["high", "medium", "low", "unknown"].map((value) => ({ value, label: reliabilityLabel(value) })))}
      {selectField("事实状态", "truth_status", truthStatuses.map((value) => ({ value, label: objectSubtypeLabel(value) })))}
      {selectField("叙事分类", "classification", ["key", "supporting", "background", "distractor", "misleading", "incomplete"].map((value) => ({ value, label: classificationLabel(value) })))}
    </>;
    if (currentSelected.collection === "events") return <>
      {field("标题", "title")}{field("说明", "description", { multiline: true })}
      {selectField("事实状态", "truth_status", truthStatuses.map((value) => ({ value, label: objectSubtypeLabel(value) })))}
    </>;
    if (currentSelected.collection === "locations") return <>
      {field("名称", "name")}{field("说明", "description", { multiline: true })}
      {selectField("空间方式", "coordinate_system", [{ value: "", label: "未标注空间位置" }, { value: "schematic", label: "示意位置" }, { value: "wgs84", label: "地理坐标（经纬度）" }])}
      {values.coordinate_system === "schematic" ? <>{field("水平位置（0–100）", "x", { type: "number", min: 0, max: 100 })}{field("垂直位置（0–100）", "y", { type: "number", min: 0, max: 100 })}</> : null}
      {values.coordinate_system === "wgs84" ? <>{field("纬度", "latitude", { type: "number", min: -90, max: 90, step: 0.000001 })}{field("经度", "longitude", { type: "number", min: -180, max: 180, step: 0.000001 })}</> : null}
    </>;
    return <>
      {field("标题", "title")}{field("说明", "description", { multiline: true })}{field("命题", "proposition", { multiline: true })}
      {selectField("状态", "hypothesis_status", ["active", "supported", "eliminated", "accepted", "rejected", "undetermined"].map((value) => ({ value, label: objectSubtypeLabel(value) })))}
      {field("支持度（0–100%）", "score", { type: "number", min: 0, max: 100, step: 1 })}
    </>;
  }

  const associationCount = currentDetail.sourceReferences.length + currentDetail.references.length + currentDetail.relationships.length;

  return (
    <section aria-label={readOnly ? "对象详情（只读）" : "对象详情与编辑"} className={styles.objectEditor} data-editing={editing}>
      <header className={styles.detailHeader}>
        <span className={styles.objectIndex}>{currentDetail.kindLabel.slice(0, 1)}</span>
        <div className={styles.detailIdentity}>
          <p>{currentDetail.kindLabel} · {currentDetail.subtypeLabel}</p>
          <h2>{currentDetail.title}</h2>
          {currentDetail.description ? <span>{currentDetail.description}</span> : null}
        </div>
        <div className={styles.headerActions}>
          <span className={styles.statusBadge}>{currentDetail.confirmationLabel}</span>
          {currentDetail.confidence !== null ? (
            <span className={styles.confidenceBadge}>{currentDetail.confidenceLabel}</span>
          ) : null}
          {!readOnly && !editing ? <button onClick={() => { setEditing(true); setNotice(null); }} type="button">编辑</button> : null}
        </div>
      </header>

      {editing ? <section aria-label="快速编辑" className={styles.quickEdit}><header><h3>快速编辑</h3><p>仅修改安全字段；关系和推理条件保持不变。</p></header><div className={styles.objectEditorFields}>{renderQuickEditFields()}</div></section> : <>{renderSections(currentDetail.coreSections, onSelectObject, fieldCitations, onOpenSources)}{currentDetail.moreSections.length ? <details className={styles.moreDetails}><summary>更多创作信息</summary>{renderSections(currentDetail.moreSections, onSelectObject, fieldCitations, onOpenSources)}</details> : null}</>}

      {associationCount ? (
        <p className={styles.associationHint}>
          {associationCount} 项关联依据已转入下方「关系上下文」按语义与动词展示。
        </p>
      ) : null}

      {currentDetail.structureLocks.length ? <section aria-label="结构约束" className={styles.structureLocks}><h3>结构约束</h3>{currentDetail.structureLocks.map((lock) => <article key={lock.title}><strong>{lock.title}</strong><p>{lock.reason}</p><span>{lock.fields.join("、")}</span></article>)}</section> : null}

      <details className={styles.technicalDetails}><summary>技术信息</summary><dl><div><dt>当前工作稿</dt><dd>{revisionLabel ?? `工作稿 R${revision}`}</dd></div>{currentDetail.technicalDetails.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></details>

      {navigationNotice || notice ? <p className={styles.objectEditorNotice} role="status">{navigationNotice ?? notice}</p> : null}
      <footer className={styles.editorFooter}>
        <span>{readOnly ? readOnlyReason ?? "候选预览，只读" : dirty ? "有未保存修改" : "已与服务端同步"}</span>
        {!readOnly ? <div className={styles.footerActions}>{editing ? <><button className={styles.cancelButton} disabled={saving} onClick={cancelChanges} type="button">取消修改</button><button disabled={!dirty || saving} onClick={() => void save()} type="button">{saving ? "正在保存…" : "保存修改"}</button></> : null}</div> : null}
      </footer>
    </section>
  );
}
