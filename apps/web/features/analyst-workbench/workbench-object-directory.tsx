"use client";

import { useMemo, useState } from "react";

import {
  type CaseObject,
  type ObjectKind,
  objectKindLabels,
} from "./analyst-fixture";
import { WorkbenchIcon } from "./workbench-icon";
import styles from "./workbench-object-directory.module.css";
import { objectSubtypeLabel } from "./workbench-presenters";

export const productionObjectKinds = [
  "resolution_spec",
  "entity",
  "information",
  "event",
  "location",
  "hypothesis",
] as const satisfies readonly ObjectKind[];

export type DirectoryObjectKind = (typeof productionObjectKinds)[number];

export const fixtureObjectKinds = productionObjectKinds;

const subtypeOrder: Record<DirectoryObjectKind, string[]> = {
  resolution_spec: ["confirmed", "proposed", "missing"],
  entity: ["person", "organization", "object", "system", "faction", "rule_actor", "other"],
  information: ["evidence", "observation", "dialogue", "document", "system_log", "rule", "environment", "feedback", "other"],
  event: ["canon_true", "reported", "disputed", "false_belief", "unknown"],
  location: ["schematic", "wgs84", "topology"],
  hypothesis: ["active", "supported", "eliminated", "accepted", "rejected", "undetermined"],
};

const directoryKindIcons = {
  resolution_spec: "focus",
  entity: "entity",
  information: "document",
  event: "event",
  location: "location",
  hypothesis: "hypothesis",
} as const;

export function directoryObjectKind(kind: ObjectKind): DirectoryObjectKind {
  if (kind === "person") return "entity";
  if (kind === "evidence") return "information";
  return kind;
}

function objectSubtype(object: CaseObject) {
  return object.subtype ?? object.code.split("·")[0]?.trim() ?? "other";
}

function normalizeQuery(value: string) {
  return value.trim().toLocaleLowerCase("zh-CN");
}

function matchesQuery(object: CaseObject, query: string) {
  if (!query) return true;
  return `${object.label} ${object.id} ${object.code} ${object.meta} ${objectSubtype(object)} ${objectSubtypeLabel(objectSubtype(object))}`
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
  const [expandedKinds, setExpandedKinds] = useState<Set<DirectoryObjectKind>>(
    () => new Set(kindFilter === "all" ? [] : [kindFilter]),
  );
  const [expandedSubtype, setExpandedSubtype] = useState<{
    kind: DirectoryObjectKind;
    subtype: string | "all";
  } | null>(() =>
    kindFilter === "all" ? null : { kind: kindFilter, subtype: subtypeFilter },
  );
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
  const filtered =
    Boolean(normalizedQuery) ||
    kindFilter !== "all" ||
    subtypeFilter !== "all";

  function selectKind(kind: DirectoryObjectKind | "all") {
    onKindFilterChange(kind);
    onSubtypeFilterChange("all");
  }

  function showAllKinds() {
    selectKind("all");
    setExpandedKinds(new Set());
    setExpandedSubtype(null);
  }

  function toggleKind(kind: DirectoryObjectKind) {
    const closing = expandedKinds.has(kind) && kindFilter === kind;
    setExpandedKinds(closing ? new Set() : new Set([kind]));
    setExpandedSubtype(null);
    selectKind(closing ? "all" : kind);
  }

  function toggleSubtype(kind: DirectoryObjectKind, subtype: string | "all") {
    const closing =
      expandedSubtype?.kind === kind && expandedSubtype.subtype === subtype;
    setExpandedSubtype(closing ? null : { kind, subtype });
    onSubtypeFilterChange(closing ? "all" : subtype);
  }

  function clearFilters() {
    onQueryChange("");
    onKindFilterChange("all");
    onSubtypeFilterChange("all");
    setExpandedKinds(new Set());
    setExpandedSubtype(null);
  }

  function renderObjectButton(object: CaseObject) {
    const selected = object.id === selectedObjectId;
    const related = relatedObjectIds.includes(object.id);
    return (
      <button
        aria-pressed={selected}
        data-related={related}
        data-agent-object-id={object.id}
        key={object.id}
        onClick={() => onSelectObject(object.id)}
        type="button"
      >
        <span className={styles.objectKindMark} data-kind={object.kind}>
          <WorkbenchIcon
            name={directoryKindIcons[directoryObjectKind(object.kind)]}
          />
        </span>
        <span className={styles.objectCopy}>
          <strong>{object.label}</strong>
          <small>
            <span>{objectSubtypeLabel(objectSubtype(object))}</span>
          </small>
        </span>
        {related ? <span className={styles.srOnly}>与当前事件相关</span> : null}
        <span className={styles.objectRowChevron} aria-hidden="true">
          <WorkbenchIcon name="chevron" />
        </span>
      </button>
    );
  }

  return (
    <section className={styles.objectCatalog}>
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
          aria-label={`全部对象，${queryMatches.length} 个匹配`}
          aria-pressed={kindFilter === "all"}
          className={styles.allKindButton}
          onClick={showAllKinds}
          type="button"
        >
          <span className={styles.kindButtonCopy}>
            <span className={styles.kindGlyph} data-kind="all">
              <WorkbenchIcon name="archive" />
            </span>
            <span className={styles.kindLabel}>
              <span>全部对象</span>
            </span>
          </span>
          <b>{queryMatches.length}</b>
        </button>
        <div
          aria-label="对象目录结果"
          className={styles.primaryKindIndex}
          role="region"
        >
          {kinds.map((kind) => {
            const expanded = expandedKinds.has(kind);
            return (
              <div
                className={styles.kindGroup}
                data-kind-group={kind}
                key={kind}
              >
                <button
                  aria-expanded={expanded}
                  aria-label={`${objectKindLabels[kind]}，${counts[kind] ?? 0} 个匹配`}
                  aria-pressed={kindFilter === kind}
                  className={styles.kindButton}
                  onClick={() => toggleKind(kind)}
                  type="button"
                >
                  <span className={styles.kindButtonCopy}>
                    <span className={styles.kindChevron} data-open={expanded}>
                      <WorkbenchIcon name="chevron" />
                    </span>
                    <span className={styles.kindGlyph} data-kind={kind}>
                      <WorkbenchIcon name={directoryKindIcons[kind]} />
                    </span>
                    <span className={styles.kindLabel}>
                      {objectKindLabels[kind]}
                    </span>
                  </span>
                  <b>{counts[kind] ?? 0}</b>
                </button>
                {expanded && kindFilter === kind ? (
                  <div
                    aria-label={`${objectKindLabels[kindFilter]}子类型筛选`}
                    className={styles.subtypePanel}
                  >
                    <div className={styles.subtypeOptions}>
                      {["all", ...subtypeOptions].map((subtype) => {
                        const branchOpen =
                          expandedSubtype?.kind === kindFilter &&
                          expandedSubtype.subtype === subtype;
                        const branchObjects = queryMatches.filter(
                          (object) =>
                            directoryObjectKind(object.kind) === kindFilter &&
                            (subtype === "all" ||
                              objectSubtype(object) === subtype),
                        );
                        const label =
                          subtype === "all"
                            ? `全部${objectKindLabels[kindFilter]}`
                            : objectSubtypeLabel(subtype);
                        const count =
                          subtype === "all"
                            ? (counts[kindFilter] ?? 0)
                            : (subtypeCounts[subtype] ?? 0);
                        return (
                          <div className={styles.subtypeBranch} key={subtype}>
                            <button
                              aria-expanded={branchOpen}
                              aria-label={`${label}，${count} 个匹配`}
                              aria-pressed={branchOpen}
                              onClick={() => toggleSubtype(kindFilter, subtype)}
                              type="button"
                            >
                              <span
                                aria-hidden="true"
                                className={styles.subtypeChevron}
                                data-open={branchOpen}
                              >
                                <WorkbenchIcon name="chevron" />
                              </span>
                              <span>{label}</span>
                              <b>{count}</b>
                            </button>
                            {branchOpen && branchObjects.length > 0 ? (
                              <div
                                aria-label={`${label}对象`}
                                className={styles.objectList}
                                data-nested="true"
                                role="group"
                              >
                                {branchObjects.map(renderObjectButton)}
                              </div>
                            ) : null}
                            {branchOpen && branchObjects.length === 0 ? (
                              <div className={styles.emptyState} role="status">
                                <strong>当前条件没有匹配对象</strong>
                                <p>选择其他子类型，或清除筛选查看完整目录。</p>
                                <button onClick={clearFilters} type="button">
                                  清除筛选
                                </button>
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                {expanded && kindFilter === kind && (counts[kind] ?? 0) === 0 ? (
                  <div className={styles.emptyState} role="status">
                    <strong>当前条件没有匹配对象</strong>
                    <p>选择其他类型，或清除筛选查看完整目录。</p>
                    <button onClick={clearFilters} type="button">
                      清除筛选
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
        {kindFilter === "all" && queryMatches.length === 0 ? (
          <div className={styles.emptyState} role="status">
            <strong>
              {objects.length
                ? "当前条件没有匹配对象"
                : "当前工作稿还没有卷宗对象"}
            </strong>
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
