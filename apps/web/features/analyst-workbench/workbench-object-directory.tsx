"use client";

import { useMemo } from "react";

import {
  type CaseObject,
  type ObjectKind,
  objectKindLabels,
} from "./analyst-fixture";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-object-directory.module.css";

export const productionObjectKinds = [
  "entity",
  "information",
  "event",
  "location",
  "hypothesis",
] as const satisfies readonly ObjectKind[];

export type DirectoryObjectKind = (typeof productionObjectKinds)[number];

export const fixtureObjectKinds = productionObjectKinds;

const subtypeLabels: Record<string, string> = {
  accepted: "已采纳",
  active: "核对中",
  approximate: "约略时间",
  canon_true: "既定事实",
  dialogue: "对话",
  disputed: "有争议",
  document: "文档",
  eliminated: "已排除",
  environment: "环境信息",
  evidence: "证据",
  faction: "阵营",
  false_belief: "错误认知",
  feedback: "反馈",
  information: "信息",
  minute: "分钟",
  object: "物件",
  observation: "观察",
  organization: "组织",
  other: "其他",
  person: "人物",
  rejected: "已拒绝",
  reported: "转述事实",
  rule: "规则",
  rule_actor: "规则角色",
  schematic: "示意坐标",
  supported: "已支持",
  system: "系统",
  system_log: "系统日志",
  topology: "拓扑位置",
  undetermined: "待判定",
  unknown: "未知",
  wgs84: "地理坐标",
};

const subtypeOrder: Record<DirectoryObjectKind, string[]> = {
  entity: ["person", "organization", "object", "system", "faction", "rule_actor", "other"],
  information: ["evidence", "observation", "dialogue", "document", "system_log", "rule", "environment", "feedback", "other"],
  event: ["canon_true", "reported", "disputed", "false_belief", "unknown"],
  location: ["schematic", "wgs84", "topology"],
  hypothesis: ["active", "supported", "eliminated", "accepted", "rejected", "undetermined"],
};

export function directoryObjectKind(kind: ObjectKind): DirectoryObjectKind {
  if (kind === "person") return "entity";
  if (kind === "evidence") return "information";
  return kind;
}

function objectSubtype(object: CaseObject) {
  return object.subtype ?? object.code.split("·")[0]?.trim() ?? "other";
}

function subtypeLabel(subtype: string) {
  return subtypeLabels[subtype] ?? subtype.replaceAll("_", " ");
}

function normalizeQuery(value: string) {
  return value.trim().toLocaleLowerCase("zh-CN");
}

function matchesQuery(object: CaseObject, query: string) {
  if (!query) return true;
  return `${object.label} ${object.id} ${object.code} ${object.meta} ${objectSubtype(object)} ${subtypeLabel(objectSubtype(object))}`
    .toLocaleLowerCase("zh-CN")
    .includes(query);
}

export function WorkbenchObjectDirectory({
  objects,
  kinds,
  kindFilter,
  subtypeFilter,
  query,
  selectedObjectId,
  relatedObjectIds,
  onKindFilterChange,
  onSubtypeFilterChange,
  onQueryChange,
  onSelectObject,
}: {
  objects: CaseObject[];
  kinds: readonly DirectoryObjectKind[];
  kindFilter: DirectoryObjectKind | "all";
  subtypeFilter: string | "all";
  query: string;
  selectedObjectId: string | null;
  relatedObjectIds: string[];
  onKindFilterChange: (kind: DirectoryObjectKind | "all") => void;
  onSubtypeFilterChange: (subtype: string | "all") => void;
  onQueryChange: (query: string) => void;
  onSelectObject: (objectId: string) => void;
}) {
  const normalizedQuery = normalizeQuery(query);
  const queryMatches = useMemo(
    () => objects.filter((object) => matchesQuery(object, normalizedQuery)),
    [normalizedQuery, objects],
  );
  const counts = useMemo(
    () =>
      Object.fromEntries(
        kinds.map((kind) => [
          kind,
          queryMatches.filter(
            (object) => directoryObjectKind(object.kind) === kind,
          ).length,
        ]),
      ) as Partial<Record<DirectoryObjectKind, number>>,
    [kinds, queryMatches],
  );
  const subtypeOptions = useMemo(() => {
    if (kindFilter === "all") return [];
    const order = subtypeOrder[kindFilter];
    return Array.from(
      new Set(
        objects
          .filter(
            (object) => directoryObjectKind(object.kind) === kindFilter,
          )
          .map(objectSubtype),
      ),
    ).sort((left, right) => {
      const leftIndex = order.indexOf(left);
      const rightIndex = order.indexOf(right);
      if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    });
  }, [kindFilter, objects]);
  const subtypeCounts = useMemo(() => {
    if (kindFilter === "all") return {};
    return Object.fromEntries(
      subtypeOptions.map((subtype) => [
        subtype,
        queryMatches.filter(
          (object) =>
            directoryObjectKind(object.kind) === kindFilter &&
            objectSubtype(object) === subtype,
        ).length,
      ]),
    ) as Record<string, number>;
  }, [kindFilter, queryMatches, subtypeOptions]);
  const visibleObjects = useMemo(
    () =>
      kindFilter === "all"
        ? queryMatches
        : queryMatches.filter(
            (object) =>
              directoryObjectKind(object.kind) === kindFilter &&
              (subtypeFilter === "all" ||
                objectSubtype(object) === subtypeFilter),
          ),
    [kindFilter, queryMatches, subtypeFilter],
  );
  const filtered =
    Boolean(normalizedQuery) ||
    kindFilter !== "all" ||
    subtypeFilter !== "all";

  function selectKind(kind: DirectoryObjectKind | "all") {
    onKindFilterChange(kind);
    onSubtypeFilterChange("all");
  }

  function clearFilters() {
    onQueryChange("");
    onKindFilterChange("all");
    onSubtypeFilterChange("all");
  }

  return (
    <section className={styles.objectCatalog}>
      <div className={styles.catalogHeading}>
        <div>
          <span>对象目录</span>
          <small>
            {filtered ? `${queryMatches.length} / ` : ""}
            {objects.length} OBJECTS
          </small>
        </div>
        <button
          aria-label="清除对象筛选"
          disabled={!filtered}
          onClick={clearFilters}
          type="button"
        >
          清
        </button>
      </div>

      <label className={styles.objectSearch}>
        <WorkbenchIcon name="search" />
        <span className={styles.srOnly}>搜索对象名称或编号</span>
        <input
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索名称或编号"
          value={query}
        />
      </label>

      <div className={styles.kindFilters} aria-label="对象类型筛选">
        <button
          className={styles.allKindButton}
          aria-label={`全部对象，${queryMatches.length} 个匹配`}
          aria-pressed={kindFilter === "all"}
          onClick={() => selectKind("all")}
          type="button"
        >
          <span><small>范围</small>全部对象</span>
          <b>{queryMatches.length}</b>
        </button>
        <div className={styles.primaryKindIndex}>
          {kinds.map((kind) => (
            <button
              aria-label={`${objectKindLabels[kind]}，${counts[kind] ?? 0} 个匹配`}
              aria-pressed={kindFilter === kind}
              key={kind}
              onClick={() => selectKind(kind)}
              type="button"
            >
              <span>{objectKindLabels[kind]}</span>
              <b>{counts[kind] ?? 0}</b>
            </button>
          ))}
        </div>
        {kindFilter !== "all" ? (
          <div
            aria-label={`${objectKindLabels[kindFilter]}子类型筛选`}
            className={styles.subtypePanel}
          >
            <div className={styles.subtypeHeading}>
              <span>{objectKindLabels[kindFilter]} / 子类型</span>
              <small>{subtypeOptions.length} TYPES</small>
            </div>
            <div className={styles.subtypeOptions}>
              <button
                aria-label={`全部${objectKindLabels[kindFilter]}，${counts[kindFilter] ?? 0} 个匹配`}
                aria-pressed={subtypeFilter === "all"}
                onClick={() => onSubtypeFilterChange("all")}
                type="button"
              >
                全部 <b>{counts[kindFilter] ?? 0}</b>
              </button>
              {subtypeOptions.map((subtype) => (
                <button
                  aria-label={`${subtypeLabel(subtype)}，${subtypeCounts[subtype] ?? 0} 个匹配`}
                  aria-pressed={subtypeFilter === subtype}
                  key={subtype}
                  onClick={() => onSubtypeFilterChange(subtype)}
                  type="button"
                >
                  {subtypeLabel(subtype)} <b>{subtypeCounts[subtype] ?? 0}</b>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div
        aria-label="对象目录结果"
        className={styles.objectList}
        role="region"
      >
        {visibleObjects.map((object) => {
          const selected = object.id === selectedObjectId;
          const related = relatedObjectIds.includes(object.id);
          return (
            <button
              aria-pressed={selected}
              data-related={related}
              key={object.id}
              onClick={() => onSelectObject(object.id)}
              type="button"
            >
              <span className={styles.objectKindMark} data-kind={object.kind}>
                {objectKindLabels[object.kind].slice(0, 1)}
              </span>
              <span className={styles.objectCopy}>
                <strong>{object.label}</strong>
                <small>
                  <span>{subtypeLabel(objectSubtype(object))}</span>
                  <code>{object.id}</code>
                </small>
              </span>
              {related ? <i aria-label="与当前事件相关" /> : null}
            </button>
          );
        })}
        {visibleObjects.length === 0 ? (
          <div className={styles.emptyState} role="status">
            <strong>{objects.length ? "当前条件没有匹配对象" : "当前工作稿还没有卷宗对象"}</strong>
            <p>
              {objects.length
                ? "清除筛选后查看完整目录。"
                : "对象会在采用并加载工作稿后显示在这里。"}
            </p>
            {filtered ? (
              <button onClick={clearFilters} type="button">
                清除筛选
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}
