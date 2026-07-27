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

import motionStyles from "./reasoning-motion.module.css";
import styles from "./reasoning-lab.module.css";
import sourceStyles from "./reasoning-source-preview.module.css";

export interface ReasoningFlowNodeData extends Record<string, unknown> {
  node: ReasoningNodeModel;
  sequence: number;
  expanded: boolean;
  activeSourceId?: string;
  onToggleBundle: (id: string) => void;
  onPreviewSource: (
    sourceId: string,
    trigger?: HTMLButtonElement,
  ) => void;
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
  const {
    node,
    sequence,
    expanded,
    activeSourceId,
    onToggleBundle,
    onPreviewSource,
  } = data;
  const sources = node.sourceIds
    .map(getReasoningSource)
    .filter((source) => source !== undefined);

  return (
    <article
      aria-label={`${kindLabels[node.kind]}：${node.label}，${statusLabels[node.status]}`}
      className={`${styles.graphNode} ${styles[`graphNode_${node.kind.replace("-", "_")}`]} ${
        styles[`graphNode_${node.status}`]
      } ${motionStyles.graphNode} ${
        selected
          ? `${styles.graphNodeSelected} ${motionStyles.graphNodeSelected}`
          : ""
      } ${
        node.kind === "source-bundle" && expanded
          ? sourceStyles.expandedNode
          : ""
      }`}
      style={{ animationDelay: `${80 + sequence * 70}ms` }}
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
            aria-controls={`sources-${node.id}`}
            aria-expanded={expanded}
            className={`${styles.bundleToggle} ${sourceStyles.bundleToggle} ${
              expanded ? sourceStyles.bundleToggleExpanded : ""
            }`}
            onClick={(event) => {
              event.stopPropagation();
              onToggleBundle(node.id);
            }}
            type="button"
          >
            <span>
              <strong>{expanded ? "来源已展开" : "来源清单"}</strong>
              <small>
                {expanded
                  ? "选择来源，在右侧检查器查看"
                  : `${sources.length} 个来源对象`}
              </small>
            </span>
            <b>{expanded ? "收起 −" : "展开 +"}</b>
          </button>
          {expanded ? (
            <div
              className={`${styles.sourceList} ${sourceStyles.sourceList}`}
              id={`sources-${node.id}`}
            >
              {sources.map((source) => (
                <button
                  aria-controls="reasoning-source-detail"
                  aria-label={`快速查看来源：${source.label}`}
                  aria-pressed={source.id === activeSourceId}
                  className={`${sourceStyles.sourceButton} ${
                    source.id === activeSourceId
                      ? sourceStyles.sourceButtonActive
                      : ""
                  }`}
                  data-source-trigger-key={`canvas:${node.id}:${source.id}`}
                  key={source.id}
                  onClick={(event) => {
                    event.stopPropagation();
                    onPreviewSource(source.id, event.currentTarget);
                  }}
                  type="button"
                >
                  <span>
                    <b>{source.id}</b>
                    <strong>{source.label}</strong>
                    <small>{source.meta}</small>
                  </span>
                  <i>查看 →</i>
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
