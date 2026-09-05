import type { AgentChatFocus } from "@/lib/api-client";

export type ContextKind = "object" | "event" | "validation_issue" | "view";
export interface ContextItem { kind: ContextKind; id: string; label: string }
export interface ComposerEntry {
  text: string;
  candidate: AgentChatFocus;
  pinned: ContextItem[];
  excluded: string[];
}
export type ComposerEntries = Record<number, ComposerEntry>;
export type ComposerAction = {
  threadId: number;
  candidate: AgentChatFocus;
} & (
  | { type: "candidate" }
  | { type: "initialize" }
  | { type: "text"; text: string }
  | { type: "sent"; text: string }
  | { type: "add"; item: ContextItem }
  | { type: "remove"; item: ContextItem }
);

export const contextKey = (item: Pick<ContextItem, "kind" | "id">) => `${item.kind}:${item.id}`;
export const emptyFocus = (): AgentChatFocus => ({ object_ids: [], event_ids: [], validation_issue_ids: [], view: null });
export function newComposerEntry(candidate: AgentChatFocus): ComposerEntry {
  return { text: "", candidate, pinned: [], excluded: [] };
}
export function focusItems(focus: AgentChatFocus): ContextItem[] {
  return [
    ...focus.object_ids.map((id): ContextItem => ({ kind: "object", id, label: id })),
    ...focus.event_ids.map((id): ContextItem => ({ kind: "event", id, label: id })),
    ...focus.validation_issue_ids.map((id): ContextItem => ({ kind: "validation_issue", id, label: id })),
    ...(focus.view ? [{ kind: "view" as const, id: focus.view, label: focus.view }] : []),
  ];
}
export function composerItems(entry: ComposerEntry): ContextItem[] {
  const items = new Map<string, ContextItem>();
  for (const item of [...focusItems(entry.candidate), ...entry.pinned]) {
    if (!entry.excluded.includes(contextKey(item))) items.set(contextKey(item), item);
  }
  return [...items.values()];
}
export function composerFocus(entry: ComposerEntry): AgentChatFocus {
  const result = emptyFocus();
  for (const item of composerItems(entry)) {
    if (item.kind === "object") result.object_ids.push(item.id);
    if (item.kind === "event") result.event_ids.push(item.id);
    if (item.kind === "validation_issue") result.validation_issue_ids.push(item.id);
    if (item.kind === "view") result.view = item.id;
  }
  return result;
}
export function composerReducer(state: ComposerEntries, action: ComposerAction): ComposerEntries {
  const previous = state[action.threadId] ?? newComposerEntry(action.candidate);
  let entry = previous;
  if (action.type === "initialize") {
    if (state[action.threadId]) return state;
  } else if (action.type === "candidate") {
    if (JSON.stringify(previous.candidate) === JSON.stringify(action.candidate)) return state;
    const retained = new Set([...focusItems(action.candidate), ...previous.pinned].map(contextKey));
    entry = { ...previous, candidate: action.candidate, excluded: previous.excluded.filter((key) => retained.has(key)) };
  } else if (action.type === "sent") {
    entry = { ...previous, text: previous.text === action.text ? "" : previous.text };
  } else if (action.type === "text") {
    entry = { ...previous, text: action.text };
  } else if (action.type === "add") {
    const key = contextKey(action.item);
    entry = { ...previous, pinned: [...previous.pinned.filter((item) => contextKey(item) !== key), action.item], excluded: previous.excluded.filter((value) => value !== key) };
  } else {
    const key = contextKey(action.item);
    entry = { ...previous, pinned: previous.pinned.filter((item) => contextKey(item) !== key), excluded: [...new Set([...previous.excluded, key])] };
  }
  return { ...state, [action.threadId]: entry };
}
