"use client";

import {
  Handle,
  Position,
  type Node,
  type NodeProps,
} from "@xyflow/react";

import {
  getReasoningSource,
  type ReasoningNode as ReasoningNodeModel,
} from "@/lib/reasoning-prototype";

import styles from "./reasoning-lab.module.css";

export interface ReasoningFlowNodeData extends Record<string, unknown> {
  node: ReasoningNodeModel;
  expanded: boolean;
  onToggleBundle: (id: string) => void;
  onOpenSource: (sourceId: string) => void;
}

export type ReasoningFlowNode = Node<
  ReasoningFlowNodeData,
  "reasoning-node"
>;

const kindLabels: Record<ReasoningNodeModel["kind"], string> = {
  "source-bundle": "SOURCE BUNDLE",
  claim: "CLAIM",
  hypothesis: "HYPOTHESIS",
  conclusion: "RESOLUTION",
  gap: "OPEN GAP",
};

const statusLabels: Record<ReasoningNodeModel["status"], string> = {
  existing: "已有对象",
  candidate: "AI 候选",
  confirmed: "已确认",
  excluded: "已排除",
  conflict: "待求证",
};

export function ReasoningCanvasNode({
  data,
  selected,
}: NodeProps<ReasoningFlowNode>) {
  const { node, expanded, onToggleBundle, onOpenSource } = data;
  const sources = node.sourceIds
    .map(getReasoningSource)
    .filter((source) => source !== undefined);

  return (
    <article
      aria-label={`${kindLabels[node.kind]}：${node.label}，${statusLabels[node.status]}`}
      className={`${styles.graphNode} ${styles[`graphNode_${node.kind.replace("-", "_")}`]} ${
        styles[`graphNode_${node.status}`]
      } ${selected ? styles.graphNodeSelected : ""}`}
    >
      <Handle
        className={styles.graphHandle}
        isConnectable={node.kind !== "source-bundle"}
        position={Position.Left}
        type="target"
      />

      <header className={styles.graphNodeHeader}>
        <span>{kindLabels[node.kind]}</span>
        <b>{statusLabels[node.status]}</b>
      </header>

      <div className={styles.graphNodeBody}>
        <strong>{node.label}</strong>
        <p>{node.statement}</p>
      </div>

      {node.kind === "source-bundle" ? (
        <div className={`${styles.sourceBundle} nodrag nowheel`}>
          <button
            aria-expanded={expanded}
            className={styles.bundleToggle}
            onClick={(event) => {
              event.stopPropagation();
              onToggleBundle(node.id);
            }}
            type="button"
          >
            <span>{expanded ? "收起来源" : `展开 ${sources.length} 个来源`}</span>
            <b>{expanded ? "−" : "+"}</b>
          </button>
          {expanded ? (
            <div className={styles.sourceList}>
              {sources.map((source) => (
                <button
                  disabled={!source.targetEventId}
                  key={source.id}
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenSource(source.id);
                  }}
                  type="button"
                >
                  <span>
                    <b>{source.id}</b>
                    <strong>{source.label}</strong>
                  </span>
                  <i>{source.targetEventId ? "工作台 ↗" : source.type}</i>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : (
        <footer className={styles.graphNodeFooter}>
          <span>{node.tags.slice(0, 2).join(" · ")}</span>
          <b>
            {node.confidence === undefined
              ? "—"
              : `${Math.round(node.confidence * 100)}%`}
          </b>
        </footer>
      )}

      <Handle
        className={styles.graphHandle}
        isConnectable={node.kind !== "conclusion" && node.kind !== "gap"}
        position={Position.Right}
        type="source"
      />
    </article>
  );
}
