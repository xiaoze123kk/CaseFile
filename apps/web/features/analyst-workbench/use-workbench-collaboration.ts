import { useCallback, useReducer, useRef } from "react";
import { collaborationReducer, type CollaborationAction, type CollaborationState } from "./workbench-collaboration-state";

/** Defer layout changes, not selection, until the current IME composition commits. */
export function useWorkbenchCollaboration(initial: CollaborationState) {
  const [state, reduce] = useReducer(collaborationReducer, initial);
  const composing = useRef(false);
  const deferred = useRef<CollaborationAction[]>([]);
  const dispatch = useCallback((action: CollaborationAction) => {
    if (composing.current && action.type !== "selection") deferred.current.push(action);
    else reduce(action);
  }, []);
  const compositionStart = useCallback(() => { composing.current = true; }, []);
  const compositionEnd = useCallback(() => {
    composing.current = false;
    const actions = deferred.current;
    deferred.current = [];
    actions.forEach(reduce);
  }, []);
  return { state, dispatch, compositionStart, compositionEnd };
}
