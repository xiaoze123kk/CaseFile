import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { useWorkbenchCollaboration } from "@/features/analyst-workbench/use-workbench-collaboration";
import { initialCollaborationState } from "@/features/analyst-workbench/workbench-collaboration-state";

it("does not hide the old composer slot while an IME composition is active", () => {
  const { result } = renderHook(() => useWorkbenchCollaboration(initialCollaborationState));
  act(() => result.current.compositionStart());
  act(() => {
    result.current.dispatch({ type: "open_agent", mode: "analysis" });
    result.current.dispatch({ type: "selection", selection: { objectId: "next" } });
  });
  expect(result.current.state.agentSurface).toBe("dock");
  expect(result.current.state.objectId).toBe("next");
  act(() => result.current.compositionEnd());
  expect(result.current.state.agentSurface).toBe("side");
});
