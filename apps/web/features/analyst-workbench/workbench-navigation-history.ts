import type { WorkbenchView } from "./workbench-views";

/**
 * Browser-like history for the right-hand object context panel.
 *
 * One frame per intentional object focus change. View is recorded so going
 * back also restores the main-canvas perspective the user had when they
 * selected that object. Temporary UI state (filters, drawers, edit buffers)
 * is intentionally not part of a frame.
 */

export interface ObjectFocusFrame {
  objectId: string | null;
  view: WorkbenchView;
}

export interface ObjectNavigationHistory {
  frames: ObjectFocusFrame[];
  cursor: number;
  limit: number;
}

export const DEFAULT_OBJECT_HISTORY_LIMIT = 50;

export function createObjectNavigationHistory(
  initial: ObjectFocusFrame,
  limit = DEFAULT_OBJECT_HISTORY_LIMIT,
): ObjectNavigationHistory {
  return {
    frames: [{ objectId: initial.objectId, view: initial.view }],
    cursor: 0,
    limit,
  };
}

export function currentObjectFocus(history: ObjectNavigationHistory) {
  return history.frames[history.cursor] ?? null;
}

export function objectFocusBackTarget(history: ObjectNavigationHistory) {
  return history.frames[history.cursor - 1] ?? null;
}

export function objectFocusForwardTarget(history: ObjectNavigationHistory) {
  return history.frames[history.cursor + 1] ?? null;
}

export function recordObjectFocus(
  history: ObjectNavigationHistory,
  frame: ObjectFocusFrame,
): ObjectNavigationHistory {
  const current = history.frames[history.cursor];
  if (current && current.objectId === frame.objectId) return history;

  const frames = [...history.frames.slice(0, history.cursor + 1), frame];
  if (frames.length > history.limit) {
    frames.splice(0, frames.length - history.limit);
  }
  return {
    frames,
    cursor: frames.length - 1,
    limit: history.limit,
  };
}

export function moveObjectHistoryBack(history: ObjectNavigationHistory) {
  if (history.cursor <= 0) return history;
  return { ...history, cursor: history.cursor - 1 };
}

export function moveObjectHistoryForward(history: ObjectNavigationHistory) {
  if (history.cursor >= history.frames.length - 1) return history;
  return { ...history, cursor: history.cursor + 1 };
}
