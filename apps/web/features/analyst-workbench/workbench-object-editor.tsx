"use client";

import type { CaseFile } from "@casefile/contracts";
import { useMemo, useState } from "react";

import type { TimelineEvent } from "./analyst-fixture";
import styles from "./workbench-object-editor.module.css";

type EditableCollection =
  | "entities"
  | "information_units"
  | "events"
  | "locations"
  | "hypotheses";

type EditableObject = CaseFile[EditableCollection][number];
type SaveResult = "saved" | "conflict" | "error";

const collections: EditableCollection[] = [
  "entities",
  "information_units",
  "events",
  "locations",
  "hypotheses",
];

const collectionLabels: Record<EditableCollection, string> = {
  entities: "实体",
  information_units: "信息",
  events: "事件",
  locations: "地点",
  hypotheses: "假设",
};

const truthStatuses = [
  "canon_true",
  "reported",
  "disputed",
  "false_belief",
  "unknown",
] as const;

function findObject(document: CaseFile, objectId: string | null) {
  if (!objectId) return null;
  for (const collection of collections) {
    const object = document[collection].find((item) => item.id === objectId);
    if (object) return { collection, object: object as EditableObject };
  }
  return null;
}

function objectSubtype(collection: EditableCollection, object: EditableObject) {
  if (collection === "entities") return String(object.entity_type);
  if (collection === "information_units") return String(object.information_type);
  if (collection === "events") return String(object.truth_status);
  if (collection === "locations") return "location";
  return String(object.status);
}

function positionOf(object: EditableObject) {
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

function referencedIds(object: EditableObject) {
  const ids = new Set<string>();
  const visit = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== "object") return;
    const record = value as Record<string, unknown>;
    if (typeof record.object_id === "string") ids.add(record.object_id);
    Object.values(record).forEach(visit);
  };
  visit(object);
  ids.delete(object.id);
  return [...ids];
}

function objectRefId(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const objectId = (value as Record<string, unknown>).object_id;
  return typeof objectId === "string" ? objectId : null;
}

function valuesForSelected(
  selected: ReturnType<typeof findObject>,
): Record<string, string> {
  if (!selected) return {};
  const object = selected.object as Record<string, unknown>;
  const time =
    selected.collection === "events"
      ? (object.time as Record<string, unknown>)
      : null;
  const position =
    selected.collection === "locations" ? positionOf(selected.object) : null;
  return {
    name: String(object.name ?? ""),
    title: String(object.title ?? ""),
    description: String(object.description ?? ""),
    content: String(object.content ?? ""),
    reliability: String(object.reliability ?? "unknown"),
    truth_status: String(object.truth_status ?? "unknown"),
    classification: String(object.classification ?? "background"),
    time_start: String(time?.start ?? ""),
    time_end: String(time?.end ?? ""),
    time_precision: String(time?.precision ?? "unknown"),
    proposition: String(object.proposition ?? ""),
    hypothesis_status: String(object.status ?? "undetermined"),
    score:
      object.score === null || object.score === undefined
        ? ""
        : String(object.score),
    coordinate_system: position?.coordinate_system ?? "",
    x: position?.coordinate_system === "schematic" ? String(position.x) : "",
    y: position?.coordinate_system === "schematic" ? String(position.y) : "",
    latitude:
      position?.coordinate_system === "wgs84"
        ? String(position.latitude)
        : "",
    longitude:
      position?.coordinate_system === "wgs84"
        ? String(position.longitude)
        : "",
  };
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
  onSelectRelatedEvent,
  onSave,
  readOnly = false,
  readOnlyReason,
}: {
  document: CaseFile;
  selectedObjectId: string | null;
  revision: number;
  revisionLabel?: string;
  saving: boolean;
  relatedEvents: TimelineEvent[];
  navigationNotice: string | null;
  onDirtyChange: (dirty: boolean) => void;
  onSelectRelatedEvent: (eventId: string) => void;
  onSave?: (
    objectId: string,
    changes: Record<string, unknown>,
  ) => Promise<SaveResult>;
  readOnly?: boolean;
  readOnlyReason?: string;
}) {
  const selected = useMemo(
    () => findObject(document, selectedObjectId),
    [document, selectedObjectId],
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    valuesForSelected(selected),
  );
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  if (!selected) {
    return (
      <div className={styles.emptyState}>
        <strong>选择一个对象开始核对</strong>
        <p>这里会显示真实字段、引用和可安全编辑的内容。</p>
      </div>
    );
  }

  const { collection, object } = selected;
  const references = referencedIds(object);
  const metadata = object as Record<string, unknown>;
  const relationships = document.relationships.filter(
    (relationship) =>
      objectRefId(relationship.from_ref) === object.id ||
      objectRefId(relationship.to_ref) === object.id,
  );
  const structureLocks = document.structure_locks.filter(
    (lock) => objectRefId(lock.object_ref) === object.id,
  );

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
    setValues(valuesForSelected(selected));
    setDirty(false);
    onDirtyChange(false);
    setNotice("已取消未保存修改。");
  }

  function field(label: string, name: string, multiline = false) {
    return (
      <label className={multiline ? styles.objectEditorWide : undefined}>
        <span>{label}</span>
        {multiline ? (
          <textarea
            onChange={(event) => change(name, event.target.value)}
            readOnly={readOnly}
            rows={4}
            value={values[name] ?? ""}
          />
        ) : (
          <input
            onChange={(event) => change(name, event.target.value)}
            readOnly={readOnly}
            value={values[name] ?? ""}
          />
        )}
      </label>
    );
  }

  function buildChanges() {
    if (collection === "entities") {
      return { name: values.name, description: values.description };
    }
    if (collection === "information_units") {
      return {
        title: values.title,
        description: values.description,
        content: values.content,
        reliability: values.reliability,
        truth_status: values.truth_status,
        classification: values.classification,
      };
    }
    if (collection === "events") {
      return {
        title: values.title,
        description: values.description,
        truth_status: values.truth_status,
        time: {
          start: values.time_start,
          end: values.time_end.trim() || null,
          precision: values.time_precision,
        },
      };
    }
    if (collection === "locations") {
      let spatialPosition: Record<string, unknown> | undefined;
      if (values.coordinate_system === "schematic") {
        spatialPosition = {
          coordinate_system: "schematic",
          x: Number(values.x),
          y: Number(values.y),
        };
      } else if (values.coordinate_system === "wgs84") {
        spatialPosition = {
          coordinate_system: "wgs84",
          latitude: Number(values.latitude),
          longitude: Number(values.longitude),
        };
      }
      return {
        name: values.name,
        description: values.description,
        ...(spatialPosition ? { spatial_position: spatialPosition } : {}),
      };
    }
    return {
      title: values.title,
      description: values.description,
      proposition: values.proposition,
      status: values.hypothesis_status,
      score: values.score.trim() ? Number(values.score) : null,
    };
  }

  function coordinateInvalid() {
    if (collection !== "locations") return false;
    if (values.coordinate_system === "schematic") {
      if (!values.x.trim() || !values.y.trim()) return true;
      const x = Number(values.x);
      const y = Number(values.y);
      return !Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 100 || y < 0 || y > 100;
    }
    if (values.coordinate_system === "wgs84") {
      if (!values.latitude.trim() || !values.longitude.trim()) return true;
      const latitude = Number(values.latitude);
      const longitude = Number(values.longitude);
      return !Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180;
    }
    return false;
  }

  function invalidMessage() {
    if (coordinateInvalid()) return "坐标超出允许范围，请检查后再保存。";
    if (collection === "events") {
      if (!values.time_start.trim() || Number.isNaN(Date.parse(values.time_start))) {
        return "开始时间必须是有效的 ISO 8601 日期时间。";
      }
      if (values.time_end.trim() && Number.isNaN(Date.parse(values.time_end))) {
        return "结束时间必须是有效的 ISO 8601 日期时间。";
      }
    }
    if (collection === "hypotheses" && values.score.trim()) {
      const score = Number(values.score);
      if (!Number.isFinite(score) || score < 0 || score > 1) {
        return "假设分数必须介于 0 与 1 之间。";
      }
    }
    return null;
  }

  async function save() {
    if (readOnly || !onSave) return;
    const validationMessage = invalidMessage();
    if (!dirty || validationMessage) {
      if (validationMessage) setNotice(validationMessage);
      return;
    }
    const result = await onSave(object.id, buildChanges());
    if (result === "saved") {
      setDirty(false);
      onDirtyChange(false);
      setNotice("修改已写入当前工作稿。");
    } else if (result === "conflict") {
      setNotice("工作稿已更新。你的输入已保留，请核对最新版后再次保存。");
    } else {
      setNotice("修改未保存。请检查字段或服务状态后重试。");
    }
  }

  return (
    <section
      className={styles.objectEditor}
      aria-label={readOnly ? "对象详情（只读）" : "对象详情与编辑"}
    >
      <header>
        <div>
          <span>{collectionLabels[collection]} · {objectSubtype(collection, object)}</span>
          <strong>{object.id}</strong>
        </div>
        <small>{revisionLabel ?? `Draft R${revision}`} · 对象 R{String(metadata.revision ?? "—")}</small>
      </header>

      <div className={styles.objectEditorFields}>
        {collection === "entities" ? <>{field("名称", "name")}{field("说明", "description", true)}</> : null}
        {collection === "information_units" ? (
          <>
            {field("标题", "title")}
            {field("说明", "description", true)}
            {field("正文", "content", true)}
            <label><span>可靠度</span><select disabled={readOnly} onChange={(event) => change("reliability", event.target.value)} value={values.reliability}>{["high", "medium", "low", "unknown"].map((value) => <option key={value}>{value}</option>)}</select></label>
            <label><span>真值状态</span><select disabled={readOnly} onChange={(event) => change("truth_status", event.target.value)} value={values.truth_status}>{truthStatuses.map((value) => <option key={value}>{value}</option>)}</select></label>
            <label><span>分类</span><select disabled={readOnly} onChange={(event) => change("classification", event.target.value)} value={values.classification}>{["key", "supporting", "background", "distractor", "misleading", "incomplete"].map((value) => <option key={value}>{value}</option>)}</select></label>
          </>
        ) : null}
        {collection === "events" ? (
          <>
            {field("标题", "title")}
            {field("说明", "description", true)}
            {field("开始时间", "time_start")}
            {field("结束时间", "time_end")}
            <label><span>时间精度</span><select disabled={readOnly} onChange={(event) => change("time_precision", event.target.value)} value={values.time_precision}>{["second", "minute", "hour", "day", "approximate", "unknown"].map((value) => <option key={value}>{value}</option>)}</select></label>
            <label><span>真值状态</span><select disabled={readOnly} onChange={(event) => change("truth_status", event.target.value)} value={values.truth_status}>{truthStatuses.map((value) => <option key={value}>{value}</option>)}</select></label>
          </>
        ) : null}
        {collection === "locations" ? (
          <>
            {field("名称", "name")}
            {field("说明", "description", true)}
            <label><span>坐标系统</span><select disabled={readOnly} onChange={(event) => change("coordinate_system", event.target.value)} value={values.coordinate_system}><option disabled value="">选择坐标系统</option><option value="schematic">空间示意</option><option value="wgs84">WGS84</option></select></label>
            {values.coordinate_system === "schematic" ? <>{field("X（0–100）", "x")}{field("Y（0–100）", "y")}</> : null}
            {values.coordinate_system === "wgs84" ? <>{field("纬度", "latitude")}{field("经度", "longitude")}</> : null}
          </>
        ) : null}
        {collection === "hypotheses" ? (
          <>
            {field("标题", "title")}
            {field("说明", "description", true)}
            {field("命题", "proposition", true)}
            <label><span>状态</span><select disabled={readOnly} onChange={(event) => change("hypothesis_status", event.target.value)} value={values.hypothesis_status}>{["active", "supported", "eliminated", "accepted", "rejected", "undetermined"].map((value) => <option key={value}>{value}</option>)}</select></label>
            {field("分数（0–1，可空）", "score")}
          </>
        ) : null}
      </div>

      <section className={styles.relatedEvents} aria-label="关联事件">
        <header>
          <strong>关联事件</strong>
          <small>{relatedEvents.length} EVENTS</small>
        </header>
        {relatedEvents.length ? (
          <ol>
            {relatedEvents.map((event) => (
              <li key={event.id}>
                <button
                  onClick={() => onSelectRelatedEvent(event.id)}
                  type="button"
                >
                  <time>{event.time}</time>
                  <span>
                    <strong>{event.label}</strong>
                    <small>{event.id}</small>
                  </span>
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p>此对象尚未关联事件，时间线不会沿用上一次选择。</p>
        )}
      </section>

      <dl className={styles.objectMetadata}>
        <div><dt>确认状态</dt><dd>{String(metadata.confirmation_status ?? "—")}</dd></div>
        <div><dt>置信度</dt><dd>{metadata.confidence === null || metadata.confidence === undefined ? "—" : String(metadata.confidence)}</dd></div>
        <div><dt>引用对象</dt><dd>{references.length ? references.join("、") : "无"}</dd></div>
        <div><dt>只读关系</dt><dd>{relationships.length ? relationships.map((relationship) => `${relationship.id} · ${relationship.title}`).join("；") : "无"}</dd></div>
        <div><dt>结构锁</dt><dd>{structureLocks.length ? structureLocks.map((lock) => `${lock.id} · ${lock.title}`).join("；") : "无"}</dd></div>
      </dl>

      {navigationNotice || notice ? (
        <p className={styles.objectEditorNotice} role="status">
          {navigationNotice ?? notice}
        </p>
      ) : null}
      <footer>
        <span>{readOnly ? readOnlyReason ?? "候选预览只读" : dirty ? "有未保存修改" : "已与服务端同步"}</span>
        <div className={styles.footerActions}>
          <button
            className={styles.cancelButton}
            disabled={readOnly || !dirty || saving}
            onClick={cancelChanges}
            type="button"
          >
            取消修改
          </button>
          <button disabled={readOnly || !dirty || saving} onClick={() => void save()} type="button">
            {readOnly ? readOnlyReason ? "位置编辑进行中" : "采用后才能编辑" : saving ? "正在保存…" : "保存到当前工作稿"}
          </button>
        </div>
      </footer>
    </section>
  );
}
