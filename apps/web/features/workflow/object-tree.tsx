"use client";

import { useMemo, useState } from "react";

import type { CaseFileDocument } from "@/lib/api-client";

import styles from "./real-workbench.module.css";
import {
  collectionObjects,
  objectDescription,
  objectHeadline,
  WORKBENCH_COLLECTIONS,
  type WorkbenchCollectionKey,
  type WorkbenchSelection,
} from "./workbench-model";

export function ObjectTree({
  document,
  selected,
  onSelect,
}: {
  document: CaseFileDocument;
  selected: WorkbenchSelection | null;
  onSelect: (selection: WorkbenchSelection) => void;
}) {
  const initialExpanded = useMemo(() => {
    const keys = WORKBENCH_COLLECTIONS.filter(
      ({ key }) => collectionObjects(document, key).length > 0,
    )
      .slice(0, 2)
      .map(({ key }) => key);
    if (selected && !keys.includes(selected.collection)) {
      keys.unshift(selected.collection);
    }
    return new Set<WorkbenchCollectionKey>(keys);
  }, [document, selected]);
  const [expanded, setExpanded] =
    useState<Set<WorkbenchCollectionKey>>(initialExpanded);

  function toggleCollection(collection: WorkbenchCollectionKey) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(collection)) next.delete(collection);
      else next.add(collection);
      return next;
    });
  }

  return (
    <nav aria-label="卷宗对象树" className={styles.objectTree}>
      {WORKBENCH_COLLECTIONS.map(({ key, label }, index) => {
        const objects = collectionObjects(document, key);
        const open = expanded.has(key) || selected?.collection === key;
        const activeCollection = selected?.collection === key;
        const groupId = `workbench-object-group-${key}`;
        return (
          <section
            className={`${styles.objectGroup} ${
              activeCollection ? styles.activeObjectGroup : ""
            }`}
            key={key}
          >
            <button
              aria-controls={groupId}
              aria-expanded={open}
              className={styles.objectGroupToggle}
              onClick={() => toggleCollection(key)}
              type="button"
            >
              <span className={styles.objectGroupIndex}>
                {String(index + 1).padStart(2, "0")}
              </span>
              <strong>{label}</strong>
              <small>{objects.length}</small>
              <i aria-hidden="true">{open ? "−" : "+"}</i>
            </button>
            <div
              className={styles.objectChildren}
              hidden={!open}
              id={groupId}
              role="group"
            >
              {objects.length ? (
                objects.map((object) => {
                  const active =
                    selected?.collection === key &&
                    selected.objectId === object.id;
                  return (
                    <button
                      aria-current={active ? "true" : undefined}
                      className={active ? styles.activeObjectLeaf : undefined}
                      key={object.id}
                      onClick={() =>
                        onSelect({ collection: key, objectId: object.id })
                      }
                      type="button"
                    >
                      <span aria-hidden="true" />
                      <span>
                        <strong>{objectHeadline(object)}</strong>
                        <small>{objectDescription(object)}</small>
                      </span>
                    </button>
                  );
                })
              ) : (
                <p className={styles.emptyObjectGroup}>暂无对象</p>
              )}
            </div>
          </section>
        );
      })}
    </nav>
  );
}
