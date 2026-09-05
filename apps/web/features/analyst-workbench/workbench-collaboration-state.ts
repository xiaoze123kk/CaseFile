import type { WorkspaceMode, WorkbenchView } from "./workbench-views";

export type AgentSurface = "dock" | "center" | "side";
export type SidePanelBase = "object" | "agent";
export type DetailHost = "center" | "side";

export type CollaborationDetail =
  | { kind: "patch"; patchId: number }
  | { kind: "validation"; findingId: string }
  | { kind: "provenance"; objectId: string }
  | { kind: "relation"; relationId: string; objectId: string };

export interface CollaborationState {
  mode: WorkspaceMode;
  view: WorkbenchView;
  objectId: string | null;
  eventId: string | null;
  issueId: string | null;
  directContext: { kind: "object" | "event" | "validation_issue"; id: string } | null;
  inspectorOpen: boolean;
  activeDetailHost: DetailHost | null;
  agentSurface: AgentSurface;
  sideBase: SidePanelBase;
  centerStack: CollaborationDetail[];
  sideStack: CollaborationDetail[];
}

export type CollaborationAction =
  | { type: "selection"; selection: Partial<Pick<CollaborationState, "objectId" | "eventId" | "issueId" | "view" | "directContext">> }
  | { type: "inspector"; open: boolean }
  | { type: "escape" }
  | { type: "open_agent"; mode: WorkspaceMode }
  | { type: "close_agent" }
  | { type: "switch_side_base"; base: SidePanelBase }
  | { type: "mode_changed"; mode: WorkspaceMode }
  | { type: "push_detail"; mode: WorkspaceMode; detail: CollaborationDetail }
  | { type: "pop_detail"; host: DetailHost }
  | { type: "clear_details"; host?: DetailHost };

export const initialCollaborationState: CollaborationState = {
  mode: "workbench", view: "timeline", objectId: null, eventId: null, issueId: null,
  directContext: null,
  inspectorOpen: true, activeDetailHost: null,
  agentSurface: "dock",
  sideBase: "object",
  centerStack: [],
  sideStack: [],
};

export function isCentralCollaborationMode(mode: WorkspaceMode) {
  return mode === "workbench" || mode === "dossier";
}

export function collaborationDetailHost(
  mode: WorkspaceMode,
  detail: CollaborationDetail,
): DetailHost {
  return detail.kind === "patch" && isCentralCollaborationMode(mode)
    ? "center"
    : "side";
}

export function collaborationReducer(
  state: CollaborationState,
  action: CollaborationAction,
): CollaborationState {
  if (action.type === "selection") return { ...state, ...action.selection };
  if (action.type === "inspector") return { ...state, inspectorOpen: action.open,
    ...(!action.open && state.agentSurface === "side" ? { agentSurface: "dock" as const, sideBase: "object" as const } : {}) };
  if (action.type === "escape") {
    const host = state.activeDetailHost;
    if (host && state[host === "center" ? "centerStack" : "sideStack"].length) return collaborationReducer(state, { type: "pop_detail", host });
    if (state.sideStack.length) return collaborationReducer(state, { type: "pop_detail", host: "side" });
    if (state.centerStack.length) return collaborationReducer(state, { type: "pop_detail", host: "center" });
    return collaborationReducer(state, { type: "close_agent" });
  }
  if (action.type === "open_agent") {
    const agentSurface = isCentralCollaborationMode(action.mode) ? "center" : "side";
    return {
      ...state,
      agentSurface,
      inspectorOpen: agentSurface === "side" ? true : state.inspectorOpen,
      sideBase: agentSurface === "side" ? "agent" : state.sideBase,
      centerStack: agentSurface === "center" ? [] : state.centerStack,
      sideStack: agentSurface === "side" ? [] : state.sideStack,
    };
  }
  if (action.type === "close_agent") {
    return { ...state, agentSurface: "dock", sideBase: "object", centerStack: [] };
  }
  if (action.type === "switch_side_base") {
    return {
      ...state,
      agentSurface: action.base === "agent" ? "side" : "dock",
      sideBase: action.base,
      sideStack: [],
    };
  }
  if (action.type === "mode_changed") {
    const central = isCentralCollaborationMode(action.mode);
    const agentSurface = state.agentSurface === "dock" ? "dock" : central ? "center" : "side";
    const patches = state.sideStack.filter((detail) => detail.kind === "patch");
    return {
      ...state,
      mode: action.mode,
      agentSurface,
      inspectorOpen: agentSurface === "side" ? true : state.inspectorOpen,
      sideBase: agentSurface === "side" ? "agent" : "object",
      centerStack: central ? [...state.centerStack, ...patches] : [],
      sideStack: central ? state.sideStack.filter((detail) => detail.kind !== "patch") : [...state.sideStack, ...state.centerStack],
      activeDetailHost: central && patches.length ? "center" : !central && state.centerStack.length ? "side" : state.activeDetailHost,
    };
  }
  if (action.type === "push_detail") {
    const host = collaborationDetailHost(action.mode, action.detail);
    return host === "center"
      ? { ...state, activeDetailHost: host, centerStack: [...state.centerStack, action.detail] }
      : { ...state, activeDetailHost: host, inspectorOpen: true, sideStack: [...state.sideStack, action.detail] };
  }
  if (action.type === "pop_detail") {
    return action.host === "center"
      ? { ...state, centerStack: state.centerStack.slice(0, -1) }
      : { ...state, sideStack: state.sideStack.slice(0, -1) };
  }
  if (action.host === "center") return { ...state, centerStack: [] };
  if (action.host === "side") return { ...state, sideStack: [] };
  return { ...state, centerStack: [], sideStack: [] };
}
