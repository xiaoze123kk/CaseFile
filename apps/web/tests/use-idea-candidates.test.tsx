import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useIdeaCandidates } from "@/features/intake/use-idea-candidates";

const mocks = vi.hoisted(() => ({
  epoch: 0,
  createCaseProject: vi.fn(async () => ({ id: 99 })),
  fetchIdeas: vi.fn(async () => ({ batches: {} })),
  generateIdeas: vi.fn(async () => ({ ideas: [{ id: 1, batch_id: "batch", content: {} }] })),
  bookmarkIdea: vi.fn(),
  selectIdea: vi.fn(),
  loadProject: vi.fn(async () => {}),
}));
function getSessionEpoch() { return mocks.epoch; }
vi.mock("@/features/case-session/case-session-provider", () => ({
  useCaseSession: () => ({ getSessionEpoch }),
}));
vi.mock("@/features/case-session/case-session-api", () => mocks);

function renderIdeas(projectId: number | null) {
  return renderHook(({ projectId }) => useIdeaCandidates({
    activeProjectId: projectId, hydrating: false, loadProject: mocks.loadProject,
    setActivePath: vi.fn(), setShowIdeaGeneration: vi.fn(), setShowReverseParse: vi.fn(), setError: vi.fn(),
  }), { initialProps: { projectId } });
}

afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.epoch = 0; });

it("uses the restored project and discards candidates belonging to the previous project", async () => {
  const view = renderIdeas(1);
  await act(async () => { await view.result.current.generateAll(); });
  expect(view.result.current.ideaCandidates).toHaveLength(1);
  mocks.epoch += 1;
  view.rerender({ projectId: 2 });
  expect(view.result.current.ideaCandidates).toEqual([]);
  await act(async () => { await view.result.current.handleBookmarkIdea(1); });
  expect(mocks.bookmarkIdea).not.toHaveBeenCalled();
  await act(async () => { await view.result.current.generateAll(); });
  expect(mocks.generateIdeas).toHaveBeenLastCalledWith(2, undefined);
});

it("retains the temporary project across path reentry and adoption into the same project", async () => {
  const view = renderIdeas(null);
  await act(async () => { await view.result.current.enterPathB(); });
  await act(async () => { await view.result.current.generateAll(); });
  view.rerender({ projectId: null });
  await act(async () => { await view.result.current.enterPathB(); });
  expect(mocks.createCaseProject).toHaveBeenCalledTimes(1);
  expect(mocks.generateIdeas).toHaveBeenCalledWith(99, undefined);
  await act(async () => { await view.result.current.handleSelectIdea(1); });
  view.rerender({ projectId: 99 });
  expect(view.result.current.ideaCandidates[0]?.status).toBe("selected");
  expect(mocks.loadProject).toHaveBeenCalledWith(99);
});

it("releases old pending generation on project change and ignores its late result", async () => {
  let finish!: (result: { ideas: [] }) => void;
  mocks.generateIdeas.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
  const view = renderIdeas(1);
  let old!: Promise<void>;
  await act(async () => { old = view.result.current.generateAll(); });
  expect(view.result.current.ideaGenerating).toBe(true);
  mocks.epoch += 1;
  view.rerender({ projectId: 2 });
  expect(view.result.current.ideaGenerating).toBe(false);
  await act(async () => { await view.result.current.generateAll(); });
  await act(async () => { finish({ ideas: [] }); await old; });
  expect(mocks.generateIdeas).toHaveBeenLastCalledWith(2, undefined);
  expect(view.result.current.ideaCandidates).toHaveLength(1);
});
