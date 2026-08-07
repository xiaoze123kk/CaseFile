"use client";

import { type DragEvent, useState } from "react";

import styles from "./intake-early-stages.module.css";

const MAX_ITEMS = 8;

type ListEditorProps = {
  onChange: (value: string) => void;
  value: string;
};

type OutlineStage = {
  description: string;
  title: string;
};

function padItems(items: string[], minimum: number) {
  return [...items, ...Array(Math.max(0, minimum - items.length)).fill("")].slice(
    0,
    MAX_ITEMS,
  );
}

function parseList(value: string, minimum: number) {
  return padItems(value ? value.split(/\r?\n/u) : [], minimum);
}

function moveItem<T>(items: T[], from: number, to: number) {
  if (to < 0 || to >= items.length || from === to) return items;
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

function parseOutline(value: string) {
  return parseList(value, 4).map((line) => {
    const match = line.match(/^(.{1,24}?)[：:]\s*(.*)$/u);
    return match
      ? { title: match[1].trim(), description: match[2].trim() }
      : { title: "", description: line };
  });
}

function serializeOutline(stages: OutlineStage[]) {
  return stages
    .map(({ title, description }) => {
      const cleanTitle = title.trim();
      const cleanDescription = description.trim();
      if (cleanTitle && cleanDescription) return `${cleanTitle}：${cleanDescription}`;
      return cleanTitle || cleanDescription;
    })
    .join("\n");
}

function RowActions({
  index,
  itemCount,
  minimum,
  onDelete,
  onMove,
}: {
  index: number;
  itemCount: number;
  minimum: number;
  onDelete: () => void;
  onMove: (to: number) => void;
}) {
  return (
    <div className={styles.structuredRowActions}>
      <button
        aria-label={`上移第 ${index + 1} 项`}
        disabled={index === 0}
        onClick={() => onMove(index - 1)}
        title="上移"
        type="button"
      >
        ↑
      </button>
      <button
        aria-label={`下移第 ${index + 1} 项`}
        disabled={index === itemCount - 1}
        onClick={() => onMove(index + 1)}
        title="下移"
        type="button"
      >
        ↓
      </button>
      <button
        aria-label={`删除第 ${index + 1} 项`}
        disabled={itemCount <= minimum}
        onClick={onDelete}
        title="删除"
        type="button"
      >
        ×
      </button>
    </div>
  );
}

export function SellingPointsEditor({ value, onChange }: ListEditorProps) {
  const items = parseList(value, 3);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  function commit(next: string[]) {
    onChange(next.join("\n"));
  }

  function dropAt(event: DragEvent<HTMLDivElement>, index: number) {
    event.preventDefault();
    if (draggedIndex !== null) commit(moveItem(items, draggedIndex, index));
    setDraggedIndex(null);
  }

  return (
    <div className={styles.structuredEditor} data-kind="selling-points">
      <div className={styles.structuredRows}>
        {items.map((item, index) => (
          <div
            className={styles.structuredRow}
            key={index}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => dropAt(event, index)}
          >
            <span
              aria-hidden="true"
              className={styles.dragHandle}
              draggable
              onDragEnd={() => setDraggedIndex(null)}
              onDragStart={() => setDraggedIndex(index)}
            >
              ⠿
            </span>
            <b>{String(index + 1).padStart(2, "0")}</b>
            <input
              aria-label={`核心卖点第 ${index + 1} 项`}
              onChange={(event) => {
                const next = [...items];
                next[index] = event.target.value;
                commit(next);
              }}
              placeholder="写下一个独立、可辨识的亮点"
              value={item}
            />
            <RowActions
              index={index}
              itemCount={items.length}
              minimum={3}
              onDelete={() => commit(items.filter((_, itemIndex) => itemIndex !== index))}
              onMove={(to) => commit(moveItem(items, index, to))}
            />
          </div>
        ))}
      </div>
      <button
        className={styles.addStructuredItem}
        disabled={items.length >= MAX_ITEMS}
        onClick={() => commit([...items, ""])}
        type="button"
      >
        ＋ 添加一条卖点
      </button>
    </div>
  );
}

export function OutlineStagesEditor({ value, onChange }: ListEditorProps) {
  const stages = parseOutline(value);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  function commit(next: OutlineStage[]) {
    onChange(serializeOutline(next));
  }

  function dropAt(event: DragEvent<HTMLDivElement>, index: number) {
    event.preventDefault();
    if (draggedIndex !== null) commit(moveItem(stages, draggedIndex, index));
    setDraggedIndex(null);
  }

  return (
    <div className={styles.structuredEditor} data-kind="outline">
      <div className={styles.structuredRows}>
        {stages.map((stage, index) => (
          <div
            className={styles.structuredRow}
            key={index}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => dropAt(event, index)}
          >
            <span
              aria-hidden="true"
              className={styles.dragHandle}
              draggable
              onDragEnd={() => setDraggedIndex(null)}
              onDragStart={() => setDraggedIndex(index)}
            >
              ⠿
            </span>
            <b>{String(index + 1).padStart(2, "0")}</b>
            <div className={styles.stageInputs}>
              <input
                aria-label={`阶段 ${index + 1} 名称`}
                onChange={(event) => {
                  const next = [...stages];
                  next[index] = { ...stage, title: event.target.value };
                  commit(next);
                }}
                placeholder="阶段名称"
                value={stage.title}
              />
              <input
                aria-label={`阶段 ${index + 1} 描述`}
                onChange={(event) => {
                  const next = [...stages];
                  next[index] = { ...stage, description: event.target.value };
                  commit(next);
                }}
                placeholder="这一阶段发生什么，推进或验证什么"
                value={stage.description}
              />
            </div>
            <RowActions
              index={index}
              itemCount={stages.length}
              minimum={4}
              onDelete={() => commit(stages.filter((_, stageIndex) => stageIndex !== index))}
              onMove={(to) => commit(moveItem(stages, index, to))}
            />
          </div>
        ))}
      </div>
      <button
        className={styles.addStructuredItem}
        disabled={stages.length >= MAX_ITEMS}
        onClick={() => commit([...stages, { title: "", description: "" }])}
        type="button"
      >
        ＋ 添加一个阶段
      </button>
    </div>
  );
}
