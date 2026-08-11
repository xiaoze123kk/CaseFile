"use client";

import type { CaseFileDocument } from "@/lib/api-client";
import { useMemo, useState } from "react";

import type { TimelineEvent } from "./analyst-fixture";
import {
  buildObjectDetailModel,
  findWorkbenchDetailObject,
  type DetailField,
  type DetailObject,
  type ObjectDetailModel,
} from "./workbench-object-detail-model";
import {
  classificationLabel,
  formatCaseWallClock,
  objectSubtypeLabel,
  reliabilityLabel,
} from "./workbench-presenters";
import styles from "./workbench-object-editor.module.css";

type SaveResult = "saved" | "conflict" | "error";
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

function editValuesFor(selected: SelectedDetail): Record<string, string> {
  const object = selected.object as Record<string, unknown>;
  const position = selected.collection === "locations" ? positionOf(selected.object) : null;
  return {
    name: String(object.name ?? ""),
    title: String(object.title ?? ""),
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
  };
}

function renderField(
  field: DetailField,
  onSelectObject: (objectId: string) => void,
) {
  if (field.kind === "text") {
    return <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>;
  }
  if (field.kind === "list") {
    return (
      <div key={field.label}>
        <dt>{field.label}</dt>
        <dd><ul className={styles.valueList}>{field.values.map((value) => <li key={value}>{value}</li>)}</ul></dd>
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
) {
  return sections.map((section) => (
    <section className={styles.detailSection} key={section.title}>
      <h3>{section.title}</h3>
      <dl>{section.fields.map((field) => renderField(field, onSelectObject))}</dl>
    </section>
  ));
}

export function WorkbenchObjectEditor({
  document,
  selectedObjectId,
  revision,
  revisionLabel,
  saving,
  relatedEvents,
  navigationNotice,
  onDirtyChange,
  onSelectObject,
  onSelectRelatedEvent,
  onSave,
  readOnly = false,
  readOnlyReason,
}: {
  document: CaseFileDocument;
  selectedObjectId: string | null;
  revision: number;
  revisionLabel?: string;
  saving: boolean;
  relatedEvents: TimelineEvent[];
  navigationNotice: string | null;
  onDirtyChange: (dirty: boolean) => void;
  onSelectObject: (objectId: string) => void;
  onSelectRelatedEvent: (eventId: string) => void;
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
    selected ? editValuesFor(selected) : {},
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
    setValues(editValuesFor(currentSelected));
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
    } else {
      setNotice("修改未保存。请检查字段或服务状态后重试。");
    }
  }

  function renderQuickEditFields() {
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

  const associationCount = currentDetail.sourceReferences.length + currentDetail.references.length + currentDetail.relationships.length + relatedEvents.length;

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
          <span className={styles.confidenceBadge}>{currentDetail.confidenceLabel}</span>
          {!readOnly && !editing ? <button onClick={() => { setEditing(true); setNotice(null); }} type="button">编辑</button> : null}
        </div>
      </header>

      {editing ? <section aria-label="快速编辑" className={styles.quickEdit}><header><h3>快速编辑</h3><p>仅修改安全字段；关系和推理条件保持不变。</p></header><div className={styles.objectEditorFields}>{renderQuickEditFields()}</div></section> : <>{renderSections(currentDetail.coreSections, onSelectObject)}{currentDetail.moreSections.length ? <details className={styles.moreDetails}><summary>更多创作信息</summary>{renderSections(currentDetail.moreSections, onSelectObject)}</details> : null}</>}

      {associationCount ? <section aria-label="关联信息" className={styles.associations}>
        <header><h3>关联信息</h3><span>{associationCount} 项依据</span></header>
        {currentDetail.sourceReferences.length ? <article><h4>来源</h4><div className={styles.associationList}>{currentDetail.sourceReferences.map((reference) => <span data-missing={reference.missing} key={reference.id}><strong>{reference.label}</strong><small>{reference.kindLabel}</small></span>)}</div></article> : null}
        {currentDetail.references.length ? <article><h4>提及对象</h4><div className={styles.associationList}>{currentDetail.references.map((reference) => reference.selectable ? <button key={reference.id} onClick={() => onSelectObject(reference.id)} type="button"><strong>{reference.label}</strong><small>{reference.kindLabel}</small></button> : <span data-missing={reference.missing} key={reference.id}><strong>{reference.label}</strong><small>{reference.kindLabel}</small></span>)}</div></article> : null}
        {currentDetail.relationships.length ? <article><h4>关系</h4><div className={styles.associationList}>{currentDetail.relationships.map((relationship) => relationship.counterpart.selectable ? <button key={`${relationship.title}-${relationship.counterpart.id}`} onClick={() => onSelectObject(relationship.counterpart.id)} type="button"><strong>{relationship.title} · {relationship.counterpart.label}</strong><small>{relationship.counterpart.kindLabel}</small></button> : <span key={`${relationship.title}-${relationship.counterpart.id}`}><strong>{relationship.title} · {relationship.counterpart.label}</strong><small>{relationship.counterpart.kindLabel}</small></span>)}</div></article> : null}
        {relatedEvents.length ? <article><h4>关联事件</h4><ol>{relatedEvents.map((event) => <li key={event.id}><button onClick={() => onSelectRelatedEvent(event.id)} type="button"><time dateTime={event.time}>{formatCaseWallClock(event.time)}</time><strong>{event.label}</strong></button></li>)}</ol></article> : null}
      </section> : null}

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
