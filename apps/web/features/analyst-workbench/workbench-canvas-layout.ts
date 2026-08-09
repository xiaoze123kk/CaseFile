import dagre from "@dagrejs/dagre";
import type { Viewport } from "@xyflow/react";

export type WorkbenchCanvasView = "relations" | "reasoning";
export type WorkbenchCanvasDirection = "LR" | "BT";

export interface WorkbenchCanvasLayoutIdentity {
  scope: string;
  revision: string;
  view: WorkbenchCanvasView;
}

export interface WorkbenchCanvasLayoutNode {
  id: string;
  width: number;
  height: number;
}

export interface WorkbenchCanvasLayoutEdge {
  id: string;
  source: string;
  target: string;
}

export interface WorkbenchCanvasPoint {
  x: number;
  y: number;
}

export interface WorkbenchCanvasLayoutSnapshot {
  version: 1;
  identity: WorkbenchCanvasLayoutIdentity;
  positions: Record<string, WorkbenchCanvasPoint>;
  viewport: Viewport;
  updatedAt: number;
}

export interface WorkbenchCanvasStorage {
  readonly length: number;
  getItem(key: string): string | null;
  key(index: number): string | null;
  setItem(key: string, value: string): void;
}

export interface RestoredWorkbenchCanvasLayout {
  positions: Record<string, WorkbenchCanvasPoint>;
  viewport: Viewport | null;
  source: "automatic" | "current" | "previous-revision";
  warning: string | null;
}

const STORAGE_PREFIX = "casefile.canvas-layout.v1";

function finitePoint(value: unknown): value is WorkbenchCanvasPoint {
  if (!value || typeof value !== "object") return false;
  const point = value as Partial<WorkbenchCanvasPoint>;
  return Number.isFinite(point.x) && Number.isFinite(point.y);
}

function finiteViewport(value: unknown): value is Viewport {
  if (!value || typeof value !== "object") return false;
  const viewport = value as Partial<Viewport>;
  return (
    Number.isFinite(viewport.x) &&
    Number.isFinite(viewport.y) &&
    Number.isFinite(viewport.zoom) &&
    (viewport.zoom ?? 0) > 0
  );
}

function parseSnapshot(value: string): WorkbenchCanvasLayoutSnapshot | null {
  let parsed: Partial<WorkbenchCanvasLayoutSnapshot>;
  try {
    parsed = JSON.parse(value) as Partial<WorkbenchCanvasLayoutSnapshot>;
  } catch {
    return null;
  }
  if (
    parsed.version !== 1 ||
    !parsed.identity ||
    typeof parsed.identity.scope !== "string" ||
    typeof parsed.identity.revision !== "string" ||
    (parsed.identity.view !== "relations" &&
      parsed.identity.view !== "reasoning") ||
    !parsed.positions ||
    typeof parsed.positions !== "object" ||
    !finiteViewport(parsed.viewport) ||
    !Number.isFinite(parsed.updatedAt)
  ) {
    return null;
  }

  const positions = Object.fromEntries(
    Object.entries(parsed.positions).filter((entry) => finitePoint(entry[1])),
  );
  return {
    version: 1,
    identity: parsed.identity,
    positions,
    viewport: parsed.viewport,
    updatedAt: parsed.updatedAt as number,
  };
}

function encoded(value: string) {
  return encodeURIComponent(value);
}

export function workbenchCanvasLayoutFamilyPrefix(
  identity: WorkbenchCanvasLayoutIdentity,
) {
  return `${STORAGE_PREFIX}:${encoded(identity.scope)}:${identity.view}:`;
}

export function workbenchCanvasLayoutStorageKey(
  identity: WorkbenchCanvasLayoutIdentity,
) {
  return `${workbenchCanvasLayoutFamilyPrefix(identity)}${encoded(identity.revision)}`;
}

export function layoutWorkbenchCanvas(
  nodes: WorkbenchCanvasLayoutNode[],
  edges: WorkbenchCanvasLayoutEdge[],
  direction: WorkbenchCanvasDirection,
): Record<string, WorkbenchCanvasPoint> {
  const graph = new dagre.graphlib.Graph({ directed: true, multigraph: true });
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: direction,
    ranker: "network-simplex",
    nodesep: direction === "LR" ? 42 : 34,
    ranksep: direction === "LR" ? 88 : 72,
    edgesep: 20,
    marginx: 48,
    marginy: 48,
  });

  [...nodes]
    .sort((left, right) => left.id.localeCompare(right.id))
    .forEach((node) => {
      graph.setNode(node.id, { width: node.width, height: node.height });
    });
  [...edges]
    .sort((left, right) => left.id.localeCompare(right.id))
    .forEach((edge) => {
      if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
        graph.setEdge(edge.source, edge.target, {}, edge.id);
      }
    });

  dagre.layout(graph);
  return Object.fromEntries(
    nodes.map((node) => {
      const point = graph.node(node.id) as
        | { x: number; y: number }
        | undefined;
      return [
        node.id,
        point
          ? {
              x: point.x - node.width / 2,
              y: point.y - node.height / 2,
            }
          : { x: 0, y: 0 },
      ];
    }),
  );
}

function mergePositions(
  automatic: Record<string, WorkbenchCanvasPoint>,
  snapshot: WorkbenchCanvasLayoutSnapshot,
) {
  return Object.fromEntries(
    Object.entries(automatic).map(([id, point]) => [
      id,
      finitePoint(snapshot.positions[id]) ? snapshot.positions[id] : point,
    ]),
  );
}

export function restoreWorkbenchCanvasLayout(
  storage: WorkbenchCanvasStorage | null,
  identity: WorkbenchCanvasLayoutIdentity,
  automatic: Record<string, WorkbenchCanvasPoint>,
): RestoredWorkbenchCanvasLayout {
  if (!storage) {
    return {
      positions: automatic,
      viewport: null,
      source: "automatic",
      warning: null,
    };
  }

  const exactKey = workbenchCanvasLayoutStorageKey(identity);
  let warning: string | null = null;
  try {
    const exact = storage.getItem(exactKey);
    if (exact) {
      const snapshot = parseSnapshot(exact);
      if (snapshot) {
        return {
          positions: mergePositions(automatic, snapshot),
          viewport: snapshot.viewport,
          source: "current",
          warning: null,
        };
      }
      warning = "浏览器中的画布布局已损坏，已恢复自动布局。";
    }

    const prefix = workbenchCanvasLayoutFamilyPrefix(identity);
    let previous: WorkbenchCanvasLayoutSnapshot | null = null;
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key || key === exactKey || !key.startsWith(prefix)) continue;
      const value = storage.getItem(key);
      if (!value) continue;
      const snapshot = parseSnapshot(value);
      if (
        snapshot &&
        snapshot.identity.scope === identity.scope &&
        snapshot.identity.view === identity.view &&
        (!previous || snapshot.updatedAt > previous.updatedAt)
      ) {
        previous = snapshot;
      }
    }
    if (previous) {
      return {
        positions: mergePositions(automatic, previous),
        viewport: previous.viewport,
        source: "previous-revision",
        warning,
      };
    }
  } catch {
    return {
      positions: automatic,
      viewport: null,
      source: "automatic",
      warning: "无法读取浏览器画布布局，已使用自动布局。",
    };
  }

  return {
    positions: automatic,
    viewport: null,
    source: "automatic",
    warning,
  };
}

export function saveWorkbenchCanvasLayout(
  storage: WorkbenchCanvasStorage | null,
  identity: WorkbenchCanvasLayoutIdentity,
  positions: Record<string, WorkbenchCanvasPoint>,
  viewport: Viewport,
  updatedAt = Date.now(),
) {
  if (!storage) return null;
  const snapshot: WorkbenchCanvasLayoutSnapshot = {
    version: 1,
    identity,
    positions,
    viewport,
    updatedAt,
  };
  try {
    storage.setItem(
      workbenchCanvasLayoutStorageKey(identity),
      JSON.stringify(snapshot),
    );
    return null;
  } catch {
    return "浏览器无法保存画布布局；本次调整仅在当前页面保留。";
  }
}
