import { act, cleanup, render } from "@testing-library/react";
import { StrictMode, useLayoutEffect } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { CaseSessionProvider, useCaseSession } from "@/features/case-session/case-session-provider";
import type { BriefIntakeView, TaskView } from "@/lib/api-client";
import { createBriefReview } from "@/features/intake/intake-model";

const mocks = vi.hoisted(() => ({
  fetchCaseIntake: vi.fn(), fetchLatestTask: vi.fn(), waitForRecoveredTask: vi.fn(),
  fetchBrief: vi.fn(), fetchCaseDraft: vi.fn(), resumeDraftGenerationTask: vi.fn(),
  waitForTask: vi.fn(), cancelTask: vi.fn(),
  createCaseProject: vi.fn(), persistCaseSource: vi.fn(), startQuestionsTask: vi.fn(),
  runTaskWithProviderFallback: vi.fn(), answerQuestion: vi.fn(), startSynthesizeTask: vi.fn(),
  createBriefCandidate: vi.fn(), adoptBriefCandidate: vi.fn(), updateBrief: vi.fn(),
  fetchDraftCandidates: vi.fn(), startDraftGenerationTask: vi.fn(), startStrategyOptionsTask: vi.fn(),
  adoptDraftCandidateWithReconciliation: vi.fn(),
}));
vi.mock("@/features/case-session/case-session-api", async (original) => ({
  ...(await original<typeof import("@/features/case-session/case-session-api")>()),
  ...mocks,
}));

let session: ReturnType<typeof useCaseSession>;
function Probe() {
  const current = useCaseSession();
  useLayoutEffect(() => { session = current; }, [current]);
  return null;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function intake(id: number): BriefIntakeView {
  return {
    brief_intake_id: id, project_id: id, revision: 1, stage: "idea",
    current_source: null, current_questions_task_run_id: null, questions: [],
    hard_questions_resolved: false, current_candidate_id: null, adopted_candidate_id: null,
    candidates: [], pending_decisions: [],
    brief: { brief_id: id, draft_revision: 1, current_version_id: null, has_content: false },
    updated_at: null,
  };
}
beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/");
  mocks.fetchLatestTask.mockResolvedValue(null);
});
afterEach(cleanup);

it.each(["success", "failure"])("ignores superseded load %s", async (outcome) => {
  const old = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => id === 1 ? old.promise : intake(id));
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  let pending!: Promise<void>;
  act(() => { pending = session.loadProject(1).catch(() => undefined); });
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => {
    if (outcome === "success") old.resolve(intake(1));
    else old.reject(new Error("old load failed"));
    await pending;
  });
  expect(session.activeProjectId).toBe(2);
  expect(session.state).toBe(current);
  expect(window.location.search).toBe("?project=2");
});

it("does not resurrect a reset session", async () => {
  const old = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockReturnValue(old.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  let pending!: Promise<void>;
  act(() => { pending = session.loadProject(1); });
  act(() => { session.resetSession(); });
  const current = session.state;
  await act(async () => { old.resolve(intake(1)); await pending; });
  expect(session.activeProjectId).toBeNull();
  expect(session.state).toBe(current);
  expect(window.location.search).toBe("");
});

it("ignores an older load of the same project", async () => {
  const old = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockReturnValueOnce(old.promise).mockResolvedValueOnce(intake(1));
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  let pending!: Promise<void>;
  act(() => { pending = session.loadProject(1); });
  await act(async () => { await session.loadProject(1); });
  const current = session.state;
  await act(async () => { old.resolve({ ...intake(1), stage: "questions" }); await pending; });
  expect(session.state).toBe(current);
  expect(session.state.step).toBe("idea");
});

it("does not update the URL after unmount", async () => {
  const old = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockReturnValue(old.promise);
  const view = render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  let pending!: Promise<void>;
  act(() => { pending = session.loadProject(1); });
  view.unmount();
  await act(async () => { old.resolve(intake(1)); await pending; });
  expect(window.location.search).toBe("");
});

it("preserves a restored stash when an outstanding load finishes", async () => {
  const old = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockResolvedValueOnce({ ...intake(1), stage: "questions" })
    .mockReturnValueOnce(old.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  act(() => { session.patchState({ sourceText: "暂存的想法" }); });
  act(() => { session.stashCurrentSession(); });
  let pending!: Promise<void>;
  act(() => { pending = session.loadProject(2); });
  act(() => { session.restoreStashedSession(); });
  const current = session.state;
  await act(async () => { old.resolve(intake(2)); await pending; });
  expect(session.activeProjectId).toBe(1);
  expect(session.state).toBe(current);
  expect(window.location.search).toBe("?project=1");
});

it("owns recovered callbacks by load even when the project and task are unchanged", async () => {
  const pending = [deferred<TaskView>(), deferred<TaskView>()];
  const callbacks: Array<(task: TaskView) => void> = [];
  const task = { task_run_id: 11, task_type: "brief_polish", status: "running" } as TaskView;
  mocks.fetchCaseIntake.mockResolvedValue(intake(1));
  mocks.fetchLatestTask.mockImplementation((_id, type) => type === "brief_polish" ? task : null);
  mocks.waitForRecoveredTask.mockImplementation((_id, _task, callback) => {
    callbacks.push(callback);
    return pending[callbacks.length - 1].promise;
  });
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  await act(async () => { await session.loadProject(1); });
  expect(callbacks).toHaveLength(2);
  expect(mocks.waitForRecoveredTask.mock.calls[0][3].aborted).toBe(true);
  expect(mocks.waitForRecoveredTask.mock.calls[1][3].aborted).toBe(false);
  const current = session.state;
  await act(async () => {
    const old = { ...task, status: "failed" as const };
    callbacks[0](old);
    pending[0].resolve(old);
    await pending[0].promise;
  });
  expect(session.state).toBe(current);
  await act(async () => {
    const latest = { ...task, status: "succeeded" as const };
    callbacks[1](latest);
    pending[1].resolve(latest);
    await pending[1].promise;
  });
  expect(session.state.latestTasks.brief_polish?.status).toBe("succeeded");
});

it("loads the URL project through a StrictMode effect replay", async () => {
  window.history.replaceState(null, "", "/?project=1");
  mocks.fetchCaseIntake.mockResolvedValue(intake(1));
  vi.useFakeTimers();
  try {
    render(<StrictMode><CaseSessionProvider><Probe /></CaseSessionProvider></StrictMode>);
    await act(async () => { await vi.runAllTimersAsync(); });
    expect(session.activeProjectId).toBe(1);
    expect(session.state.hydration.status).toBe("ready");
    expect(mocks.fetchCaseIntake).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});

async function loadWithGenerationSlot() {
  await act(async () => { await session.loadProject(1); });
  const task = {
    task_run_id: 11, task_type: "brief_to_draft", status: "running",
    candidate_strategy: "structure_first",
  } as TaskView;
  act(() => { session.patchState({ generation: {
    ...session.state.generation,
    slots: { ...session.state.generation.slots, structure_first: {
      ...session.state.generation.slots.structure_first, taskRunId: 11, latestTask: task,
    } },
  } }); });
  return task;
}

it("does not reload an old project after a raced cancellation succeeds", async () => {
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  const old = deferred<TaskView>();
  mocks.cancelTask.mockReturnValue(old.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  const task = await loadWithGenerationSlot();
  let pending!: Promise<TaskView | null>;
  act(() => { pending = session.cancelGeneration("structure_first"); });
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => { old.resolve({ ...task, status: "succeeded" }); await pending; });
  expect(session.activeProjectId).toBe(2);
  expect(session.state).toBe(current);
  expect(mocks.fetchCaseIntake.mock.calls.map(([id]) => id)).toEqual([1, 2]);
});

it("still reports a current resume failure during its final reload", async () => {
  mocks.fetchCaseIntake.mockResolvedValueOnce(intake(1)).mockRejectedValueOnce(new Error("reload failed"));
  mocks.fetchBrief.mockResolvedValue({ draft_revision: 1 });
  mocks.fetchCaseDraft.mockResolvedValue({ draft_id: 3, revision: 1 });
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  const task = await loadWithGenerationSlot();
  mocks.resumeDraftGenerationTask.mockResolvedValue(task);
  mocks.waitForTask.mockResolvedValue({ ...task, status: "succeeded" });
  await act(async () => {
    await expect(session.resumeGeneration("structure_first")).rejects.toThrow("reload failed");
  });
  expect(session.state.hydration.status).toBe("error");
  expect(session.state.generation.slots.structure_first.error).toBe("reload failed");
});

it.each(["reset", "restore", "unmount"])("stops recovered reads on %s", async (action) => {
  const pending = deferred<TaskView | null>();
  const task = { task_run_id: 11, task_type: "brief_polish", status: "running" } as TaskView;
  mocks.fetchCaseIntake.mockResolvedValue(intake(1));
  mocks.fetchLatestTask.mockImplementation((_id, type) => type === "brief_polish" ? task : null);
  mocks.waitForRecoveredTask.mockReturnValue(pending.promise);
  const view = render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  const signal = mocks.waitForRecoveredTask.mock.calls[0][3] as AbortSignal;
  expect(signal.aborted).toBe(false);
  if (action === "reset") act(() => { session.resetSession(); });
  if (action === "restore") {
    act(() => { session.patchState({ sourceText: "暂存想法" }); });
    act(() => { session.stashCurrentSession(); });
    act(() => { session.restoreStashedSession(); });
  }
  if (action === "unmount") view.unmount();
  expect(signal.aborted).toBe(true);
  expect(mocks.cancelTask).not.toHaveBeenCalled();
  await act(async () => { pending.resolve(null); await pending.promise; });
});

it("does not adopt a late project creation or send subsequent writes", async () => {
  const created = deferred<{ id: number }>();
  mocks.createCaseProject.mockReturnValue(created.promise);
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  act(() => { session.patchState({ sourceText: "旧想法" }); });
  let pending!: Promise<unknown>;
  act(() => { pending = session.continueToQuestions().catch((error) => error); });
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => { created.resolve({ id: 1 }); await pending; });
  expect(session.activeProjectId).toBe(2);
  expect(session.state).toBe(current);
  expect(mocks.fetchCaseIntake.mock.calls.map(([id]) => id)).toEqual([2]);
  expect(mocks.persistCaseSource).not.toHaveBeenCalled();
});

it("stops source-to-question progression after the source response loses ownership", async () => {
  const saved = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  mocks.persistCaseSource.mockReturnValue(saved.promise);
  mocks.runTaskWithProviderFallback.mockImplementation(async (operation) => ({
    provider: "deepseek", result: await operation("deepseek"),
  }));
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  act(() => { session.patchState({ sourceText: "旧想法" }); });
  let pending!: Promise<unknown>;
  act(() => { pending = session.continueToQuestions().catch((error) => error); });
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => { saved.resolve(intake(1)); await pending; });
  expect(mocks.persistCaseSource.mock.calls[0][0]).toBe(1);
  expect(mocks.startQuestionsTask).not.toHaveBeenCalled();
  expect(session.state).toBe(current);
});

it("does not save the next answer or synthesize after an in-flight answer becomes stale", async () => {
  const saved = deferred<BriefIntakeView>();
  const original: BriefIntakeView = { ...intake(1), stage: "questions", questions: [1, 2].map((n) => ({
    question_key: `q${n}`, ordinal: n, prompt: `问题${n}`, impact: "影响", required: true,
    suggestions: [], answer_status: "unanswered", answer_text: null, answer_source: null,
  })) };
  mocks.fetchCaseIntake.mockImplementation((id: number) => id === 1 ? original : intake(id));
  mocks.answerQuestion.mockReturnValue(saved.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  act(() => { session.patchState({ answers: {
    q1: { text: "答案1", source: "user_confirmed", pending: false },
    q2: { text: "答案2", source: "user_confirmed", pending: false },
  } }); });
  let pending!: Promise<unknown>;
  act(() => { pending = session.generateBriefFromAnswers().catch((error) => error); });
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => { saved.resolve({ ...original, revision: 2 }); await pending; });
  expect(mocks.answerQuestion).toHaveBeenCalledTimes(1);
  expect(mocks.answerQuestion.mock.calls[0][0]).toBe(1);
  expect(mocks.startSynthesizeTask).not.toHaveBeenCalled();
  expect(session.state).toBe(current);
});

it("does not adopt or freeze an old confirmation after candidate creation finishes", async () => {
  const created = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => ({ ...intake(id), stage: "confirmation" }));
  mocks.createBriefCandidate.mockReturnValue(created.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  let pending!: Promise<unknown>;
  await act(async () => { pending = session.confirmBriefAndContinue().catch((error) => error); });
  expect(mocks.createBriefCandidate).toHaveBeenCalledTimes(1);
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => {
    created.resolve({ ...intake(1), stage: "confirmation", current_candidate_id: 55 });
    await pending;
  });
  expect(mocks.adoptBriefCandidate).not.toHaveBeenCalled();
  expect(mocks.updateBrief).not.toHaveBeenCalled();
  expect(session.state).toBe(current);
});

it.each(["succeeded", "failed"])("ignores old generation %s callbacks and completion", async (status) => {
  const done = deferred<TaskView>();
  const task = { task_run_id: 11, task_type: "brief_to_draft", status: "running",
    stage: "generating", candidate_strategy: "structure_first" } as TaskView;
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  mocks.fetchBrief.mockResolvedValue({ current_version_id: 5, draft_revision: 1 });
  mocks.fetchCaseDraft.mockResolvedValue({ draft_id: 3, revision: 1 });
  mocks.fetchDraftCandidates.mockResolvedValue([]);
  mocks.startDraftGenerationTask.mockResolvedValue(task);
  mocks.runTaskWithProviderFallback.mockImplementation(async (operation) => ({
    provider: "deepseek", result: await operation("deepseek"),
  }));
  mocks.waitForTask.mockReturnValue(done.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  act(() => { session.patchState({
    review: createBriefReview(session.state.brief, {}), frozenBriefVersion: 1,
    selectedStrategy: "structure_first",
  }); });
  let pending!: Promise<unknown>;
  await act(async () => { pending = session.generateCandidates().catch((error) => error); });
  expect(mocks.waitForTask).toHaveBeenCalledTimes(1);
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => {
    mocks.waitForTask.mock.calls[0][2]({ ...task, status, stage: "completed" });
    if (status === "failed") done.reject(new Error("old generation failed"));
    else done.resolve({ ...task, status: "succeeded" });
    await pending;
  });
  expect(session.state).toBe(current);
  expect(mocks.fetchDraftCandidates).toHaveBeenCalledTimes(1);
});

it("does not report a stale strategy failure in the new project", async () => {
  const done = deferred<TaskView>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  mocks.fetchBrief.mockResolvedValue({ current_version_id: 5, draft_revision: 1 });
  mocks.startStrategyOptionsTask.mockResolvedValue({ task_run_id: 11 });
  mocks.runTaskWithProviderFallback.mockImplementation(async (operation) => ({
    provider: "deepseek", result: await operation("deepseek"),
  }));
  mocks.waitForTask.mockReturnValue(done.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  let pending!: Promise<unknown>;
  await act(async () => { pending = session.analyzeStrategies().catch((error) => error); });
  expect(mocks.waitForTask).toHaveBeenCalledTimes(1);
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => { done.reject(new Error("old strategy failed")); await pending; });
  expect(session.state).toBe(current);
});

it("does not refresh or apply a late durable adoption to another session", async () => {
  const done = deferred<{ adoption: { draft_id: number }; facts: null; error: null }>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  mocks.fetchCaseDraft.mockResolvedValue({ draft_id: 3 });
  mocks.adoptDraftCandidateWithReconciliation.mockReturnValue(done.promise);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  act(() => { session.patchState({ draftCandidates: [{
    id: "draft-11", candidateState: { isCurrent: false, canAdopt: true },
  } as (typeof session.state.draftCandidates)[number]] }); });
  let pending!: Promise<unknown>;
  await act(async () => { pending = session.adoptCandidate("draft-11").catch((error) => error); });
  expect(mocks.adoptDraftCandidateWithReconciliation).toHaveBeenCalledWith(1, 11, 3);
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => { done.resolve({ adoption: { draft_id: 44 }, facts: null, error: null }); await pending; });
  expect(session.state).toBe(current);
  expect(mocks.fetchBrief).not.toHaveBeenCalled();
});

it("keeps a retry's latest-task refresh owned by its original session", async () => {
  const refreshed = deferred<TaskView>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  mocks.fetchBrief.mockResolvedValue({ current_version_id: 5, draft_revision: 1 });
  mocks.startStrategyOptionsTask.mockResolvedValue({ task_run_id: 11 });
  mocks.runTaskWithProviderFallback.mockImplementation(async (operation) => ({
    provider: "deepseek", result: await operation("deepseek"),
  }));
  mocks.waitForTask.mockResolvedValue({ result: {
    options: ["structure_first", "atmosphere_first", "reasoning_first"].map((strategy) => ({ strategy })),
    recommended_strategy: "structure_first", recommendation_reason: "先核对结构",
  } });
  mocks.fetchLatestTask.mockImplementation((id, type) =>
    id === 1 && type === "brief_strategy_options" && mocks.waitForTask.mock.calls.length
      ? refreshed.promise : null);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  let pending!: Promise<unknown>;
  await act(async () => { pending = session.retryTask("brief_strategy_options").catch((error) => error); });
  expect(mocks.waitForTask).toHaveBeenCalledTimes(1);
  await act(async () => { await session.loadProject(2); });
  const current = session.state;
  await act(async () => {
    refreshed.resolve({ task_run_id: 11, task_type: "brief_strategy_options", status: "succeeded" } as TaskView);
    await pending;
  });
  expect(session.state).toBe(current);
  expect(session.state.latestTasks).toEqual({});
});

it("does not submit the previous form while a different project is loading", async () => {
  const loading = deferred<BriefIntakeView>();
  mocks.fetchCaseIntake.mockImplementation((id: number) => id === 1 ? intake(1) : loading.promise);
  mocks.persistCaseSource.mockResolvedValue(intake(1));
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  await act(async () => { await session.loadProject(1); });
  let pending!: Promise<void>;
  act(() => { pending = session.loadProject(2); });
  await act(async () => {
    await expect(session.adoptPolish("旧表单", null)).rejects.toMatchObject({ failureCode: "session_changed" });
  });
  expect(mocks.persistCaseSource).not.toHaveBeenCalled();
  await act(async () => { loading.resolve(intake(2)); await pending; });
});

it("shares an in-flight project creation between concurrent entry actions", async () => {
  const created = deferred<{ id: number }>();
  mocks.createCaseProject.mockReturnValue(created.promise);
  const existingQuestions: BriefIntakeView = { ...intake(1), questions: [{
    question_key: "q1", ordinal: 1, prompt: "问题", impact: "影响", required: false,
    suggestions: [], answer_status: "unanswered", answer_text: null, answer_source: null,
  }] };
  mocks.fetchCaseIntake.mockResolvedValue(existingQuestions);
  mocks.persistCaseSource.mockResolvedValue(existingQuestions);
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  act(() => { session.patchState({ sourceText: "同一个想法" }); });
  let first!: Promise<void>, second!: Promise<void>;
  act(() => { first = session.continueToQuestions(); second = session.continueToQuestions(); });
  await act(async () => { created.resolve({ id: 1 }); await Promise.all([first, second]); });
  expect(mocks.createCaseProject).toHaveBeenCalledTimes(1);
  expect(session.activeProjectId).toBe(1);
});

it("does not let old creation cleanup clear a new session's pending creation", async () => {
  const old = deferred<{ id: number }>(), current = deferred<{ id: number }>();
  mocks.createCaseProject.mockReturnValueOnce(old.promise).mockReturnValue(current.promise);
  mocks.fetchCaseIntake.mockImplementation((id: number) => intake(id));
  mocks.persistCaseSource.mockImplementation((id: number) => intake(id));
  render(<CaseSessionProvider><Probe /></CaseSessionProvider>);
  act(() => { session.patchState({ sourceText: "旧想法" }); });
  let previous!: Promise<unknown>, first!: Promise<unknown>, second!: Promise<unknown>;
  act(() => { previous = session.continueToQuestions().catch((error) => error); });
  act(() => { session.resetSession(); });
  act(() => { session.patchState({ sourceText: "新想法" }); });
  act(() => { first = session.continueToQuestions().catch((error) => error); });
  await act(async () => { old.resolve({ id: 1 }); await previous; });
  act(() => { second = session.continueToQuestions().catch((error) => error); });
  expect(mocks.createCaseProject).toHaveBeenCalledTimes(2);
  await act(async () => { current.resolve({ id: 2 }); await Promise.all([first, second]); });
  expect(session.activeProjectId).toBe(2);
  expect(mocks.fetchCaseIntake.mock.calls.every(([id]) => id === 2)).toBe(true);
});
