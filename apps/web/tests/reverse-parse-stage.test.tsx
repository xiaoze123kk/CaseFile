import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ReverseParseStage from "@/features/intake/reverse-parse-stage";
import type {
  ReverseParseDocumentView,
  ReverseParseItemView,
} from "@/features/case-session/case-session-api";

function buildFakeReverseParseBackend() {
  let documents: ReverseParseDocumentView[] = [];
  const itemsByDoc = new Map<number, ReverseParseItemView[]>();
  const blocksByDoc = new Map<number, Array<{ block_no: number; text: string }>>();

  return {
    reset: () => {
      documents = [];
      itemsByDoc.clear();
      blocksByDoc.clear();
    },
    setDocuments: (docs: ReverseParseDocumentView[]) => {
      documents = docs;
    },
    setItems: (docId: number, items: ReverseParseItemView[]) => {
      itemsByDoc.set(docId, items);
    },
    setBlocks: (docId: number, blocks: Array<{ block_no: number; text: string }>) => {
      blocksByDoc.set(docId, blocks);
    },
    createCaseProject: vi.fn(async () => ({
      id: 99,
      title: "已有内容反向解析",
      description: null,
      profile: {},
      status: "active" as const,
      archived_at: null,
      created_at: null,
      updated_at: null,
      casefile_id: 99,
      draft: { id: 99, revision: 1, schema_version: "v1", status: "open" as const },
    })),
    uploadReverseParseDocument: vi.fn<() => Promise<{ document: ReverseParseDocumentView; task: { task_run_id: number } }>>(async () => {
      throw new Error("本用例不执行上传");
    }),
    fetchReverseParseDocuments: vi.fn(async () => ({ documents })),
    fetchReverseParseDocument: vi.fn(async (_projectId: number, docId: number) => ({
      document: documents.find((doc) => doc.id === docId)!,
      items: itemsByDoc.get(docId) ?? [],
    })),
    fetchReverseParseBlocks: vi.fn(async (_projectId: number, docId: number) => ({
      blocks: blocksByDoc.get(docId) ?? [],
    })),
    confirmReverseParseItem: vi.fn(
      async (_projectId: number, itemId: number, action: "confirm" | "reject") => {
        for (const [docId, docItems] of itemsByDoc.entries()) {
          const index = docItems.findIndex((item) => item.id === itemId);
          if (index < 0) continue;
          const updated: ReverseParseItemView = {
            ...docItems[index],
            confirm_status: action === "confirm" ? "confirmed" : "rejected",
          };
          const next = [...docItems];
          next[index] = updated;
          itemsByDoc.set(docId, next);
          return updated;
        }
        throw new Error("item not found");
      },
    ),
    retryReverseParse: vi.fn<() => Promise<{ task: { task_run_id: number } }>>(async () => {
      throw new Error("本用例不执行重试");
    }),
    formBriefFromReverseParse: vi.fn(async () => ({ stage: "confirmation" })),
    waitForTask: vi.fn<(...args: [number, number, unknown?, AbortSignal?]) => Promise<{ task_run_id: number; status: "succeeded" }>>(async () => ({
      task_run_id: 1,
      status: "succeeded" as const,
    })),
  };
}

const fake = vi.hoisted(() => ({
  backend: buildFakeReverseParseBackend(),
  loadProject: vi.fn(),
  epoch: 0,
  projectId: 9 as number | null,
  hydrating: false,
}));

vi.mock("@/features/case-session/case-session-api", () => fake.backend);

function getSessionEpoch() { return fake.epoch; }

vi.mock("@/features/case-session/case-session-provider", () => ({
  useCaseSession: () => ({
    activeProjectId: fake.projectId,
    getSessionEpoch,
    state: { hydration: { status: fake.hydrating ? "loading" : "ready" } },
    loadProject: fake.loadProject,
  }),
}));

function makeDoc(
  id: number,
  overrides: Partial<ReverseParseDocumentView> = {},
): ReverseParseDocumentView {
  return {
    id,
    filename: `doc-${id}.txt`,
    media_type: "text/plain",
    parse_status: "succeeded",
    current_task_run_id: null,
    created_at: null,
    ...overrides,
  };
}

function makeItem(overrides: Partial<ReverseParseItemView>): ReverseParseItemView {
  return {
    id: 1,
    item_type: "entity_alias",
    content: { name: "林晚" },
    grading: "explicit",
    grading_label: "原文明示",
    source_block_refs: [1],
    source_quote: "档案修复师林晚",
    confirm_status: "unconfirmed",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  fake.backend.reset();
  fake.loadProject.mockReset();
  fake.epoch = 0;
  fake.projectId = 9;
  fake.hydrating = false;
  vi.clearAllMocks();
});

describe("reverse parse stage", () => {
  it("renders upload area when no documents", async () => {
    fake.backend.setDocuments([]);
    render(<ReverseParseStage />);

    expect(
      await screen.findByRole("button", { name: /上传文档并解析/u }),
    ).toBeEnabled();
    expect(screen.getByText(/还没有上传任何内容/u)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /形成创作简报/u })).not.toBeInTheDocument();
    expect(fake.backend.fetchReverseParseDocuments).toHaveBeenCalledWith(9);
  });

  it("groups items by type and shows grading badges", async () => {
    const doc = makeDoc(1);
    fake.backend.setDocuments([doc]);
    fake.backend.setBlocks(1, [
      { block_no: 1, text: "档案修复师林晚在深夜发现三份记录。" },
    ]);
    fake.backend.setItems(1, [
      makeItem({
        id: 11,
        item_type: "entity_alias",
        grading: "explicit",
        grading_label: "原文明示",
        content: { name: "林晚", aliases: ["档案修复师"] },
      }),
      makeItem({
        id: 12,
        item_type: "event",
        grading: "inferred",
        grading_label: "推断",
        content: { title: "发现异常记录" },
      }),
      makeItem({
        id: 13,
        item_type: "candidate_question",
        grading: "needs_confirmation",
        grading_label: "需确认",
        content: { question: "是谁改写了记录？" },
      }),
      makeItem({
        id: 14,
        item_type: "relationship_causality",
        grading: "conflicting",
        grading_label: "前后冲突",
        content: { statement: "时间被改写" },
      }),
      makeItem({
        id: 15,
        item_type: "information_unit",
        grading: "missing_important",
        grading_label: "缺失但可能重要",
        content: { summary: "缺失的封存记录" },
      }),
    ]);
    render(<ReverseParseStage />);

    expect(
      await screen.findByRole("heading", { name: "实体与别名" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "事件" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "候选目标问题" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "关系与因果" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "信息单元" }),
    ).toBeInTheDocument();

    for (const label of ["原文明示", "推断", "需确认", "前后冲突", "缺失但可能重要"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }

    const entityGroup = screen
      .getByRole("heading", { name: "实体与别名" })
      .closest("section")!;
    expect(within(entityGroup).getAllByRole("article")).toHaveLength(1);
    expect(within(entityGroup).getByText("林晚")).toBeInTheDocument();
    expect(within(entityGroup).getByText("“档案修复师林晚”")).toBeInTheDocument();

    expect(
      screen.getByText("档案修复师林晚在深夜发现三份记录。"),
    ).toBeInTheDocument();
  });

  it("confirm button flips item status", async () => {
    const doc = makeDoc(1);
    fake.backend.setDocuments([doc]);
    fake.backend.setItems(1, [
      makeItem({
        id: 21,
        item_type: "candidate_question",
        grading: "needs_confirmation",
        grading_label: "需确认",
        content: { question: "是谁改写了记录？" },
      }),
    ]);
    render(<ReverseParseStage />);

    const card = await screen.findByRole("article");
    expect(within(card).getByRole("button", { name: "确认" })).toBeEnabled();
    expect(within(card).getByRole("button", { name: "驳回" })).toBeEnabled();

    fireEvent.click(within(card).getByRole("button", { name: "确认" }));

    await waitFor(() => {
      expect(fake.backend.confirmReverseParseItem).toHaveBeenCalledWith(9, 21, "confirm");
    });
    expect(await within(card).findByText("已确认")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "确认" })).not.toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "驳回" })).not.toBeInTheDocument();
  });

  it("form brief disabled while high-risk items unconfirmed", async () => {
    const doc = makeDoc(1);
    fake.backend.setDocuments([doc]);
    fake.backend.setItems(1, [
      makeItem({
        id: 31,
        item_type: "candidate_question",
        grading: "needs_confirmation",
        grading_label: "需确认",
        content: { question: "是谁改写了记录？" },
        confirm_status: "confirmed",
      }),
      makeItem({
        id: 32,
        item_type: "relationship_causality",
        grading: "conflicting",
        grading_label: "前后冲突",
        content: { statement: "时间被改写" },
      }),
    ]);
    render(<ReverseParseStage />);

    const formBtn = await screen.findByRole("button", { name: /形成创作简报/u });
    expect(formBtn).toBeDisabled();
    expect(
      screen.getByText(/还有 1 项高风险条目未处理/u),
    ).toBeInTheDocument();

    const highRiskCard = screen.getByText("前后冲突").closest("article")!;
    fireEvent.click(within(highRiskCard).getByRole("button", { name: "确认" }));

    expect(
      await screen.findByRole("button", { name: /形成创作简报/u }),
    ).toBeEnabled();
    expect(screen.getByText(/高风险条目已全部处理/u)).toBeInTheDocument();
  });
});


describe("reverse parse operation ownership", () => {
  it.each(["switched", "unmounted"])("does not reload an old project after forming when %s", async (invalidation) => {
    fake.backend.setDocuments([makeDoc(1)]);
    let finish!: (value: { stage: string }) => void;
    const pending = new Promise<{ stage: string }>((resolve) => { finish = resolve; });
    fake.backend.formBriefFromReverseParse.mockReturnValueOnce(pending);
    const onFormed = vi.fn();
    const view = render(<ReverseParseStage onFormed={onFormed} />);
    fireEvent.click(await screen.findByRole("button", { name: "形成创作简报" }));
    if (invalidation === "switched") fake.epoch += 1;
    else view.unmount();
    await act(async () => { finish({ stage: "confirmation" }); });
    expect(fake.loadProject).not.toHaveBeenCalled();
    expect(onFormed).not.toHaveBeenCalled();
  });

  it.each([false, true])("owns its own project reload, unless a later session replaces it (%s)", async (switchAgain) => {
    fake.backend.setDocuments([makeDoc(1)]);
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    fake.loadProject.mockImplementationOnce(() => { fake.epoch += 1; fake.hydrating = true; return pending; });
    const onFormed = vi.fn();
    const view = render(<ReverseParseStage onFormed={onFormed} />);
    fireEvent.click(await screen.findByRole("button", { name: "形成创作简报" }));
    await waitFor(() => expect(fake.loadProject).toHaveBeenCalledWith(9));
    view.rerender(<ReverseParseStage onFormed={onFormed} />);
    if (switchAgain) fake.epoch += 1;
    fake.hydrating = false;
    view.rerender(<ReverseParseStage onFormed={onFormed} />);
    await act(async () => { finish(); });
    expect(onFormed).toHaveBeenCalledTimes(switchAgain ? 0 : 1);
  });

  it("does not upload after project creation returns to an unmounted page", async () => {
    fake.projectId = null;
    const project = await fake.backend.createCaseProject();
    let finish!: (value: typeof project) => void;
    fake.backend.createCaseProject.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const view = render(<ReverseParseStage />);
    fireEvent.change(screen.getByLabelText("选择要解析的文件"), {
      target: { files: [new File(["story"], "story.txt", { type: "text/plain" })] },
    });
    view.unmount();
    await act(async () => { finish(project); });
    expect(fake.backend.uploadReverseParseDocument).not.toHaveBeenCalled();
  });

  it("shows project creation errors and releases upload pending", async () => {
    fake.projectId = null;
    fake.backend.createCaseProject.mockRejectedValueOnce(new Error("项目创建失败"));
    render(<ReverseParseStage />);
    fireEvent.change(screen.getByLabelText("选择要解析的文件"), {
      target: { files: [new File(["story"], "story.txt", { type: "text/plain" })] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("项目创建失败");
    expect(screen.getByRole("button", { name: "上传文档并解析" })).toBeEnabled();
    expect(fake.backend.uploadReverseParseDocument).not.toHaveBeenCalled();
  });

  it("does not show a late confirmation failure in a new session", async () => {
    fake.backend.setDocuments([makeDoc(1)]);
    fake.backend.setItems(1, [makeItem({ id: 21 })]);
    let fail!: (error: Error) => void;
    fake.backend.confirmReverseParseItem.mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }));
    render(<ReverseParseStage />);
    fireEvent.click(await screen.findByRole("button", { name: "确认" }));
    fake.epoch += 1;
    await act(async () => { fail(new Error("旧确认失败")); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

});


describe("reverse parse read lifetime", () => {
  it.each(["switch document", "unmount"])("aborts parsing reads on %s", async (change) => {
    fake.backend.setDocuments([makeDoc(1, { parse_status: "running", current_task_run_id: 7 }), makeDoc(2)]);
    let finish!: (task: { task_run_id: number; status: "succeeded" }) => void;
    fake.backend.waitForTask.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const view = render(<ReverseParseStage />);
    fireEvent.click(await screen.findByRole("button", { name: /doc-1.txt/ }));
    await waitFor(() => expect(fake.backend.waitForTask).toHaveBeenCalled());
    const signal = fake.backend.waitForTask.mock.calls[0][3];
    if (change === "unmount") view.unmount();
    else fireEvent.click(screen.getByRole("button", { name: /doc-2.txt/ }));
    expect(signal?.aborted).toBe(true);
    await act(async () => { finish({ task_run_id: 7, status: "succeeded" }); });
    expect(fake.backend.fetchReverseParseDocument.mock.calls.some(([, id]) => id === 1)).toBe(false);
  });

  it("does not fetch old item details after its source blocks return to a new session", async () => {
    fake.backend.setDocuments([makeDoc(1)]);
    let finish!: (data: { blocks: Array<{ block_no: number; text: string }> }) => void;
    fake.backend.fetchReverseParseBlocks.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    render(<ReverseParseStage />);
    await waitFor(() => expect(fake.backend.fetchReverseParseBlocks).toHaveBeenCalled());
    fake.epoch += 1;
    await act(async () => { finish({ blocks: [{ block_no: 1, text: "旧来源" }] }); });
    expect(fake.backend.fetchReverseParseDocument).not.toHaveBeenCalled();
    expect(screen.queryByText("旧来源")).not.toBeInTheDocument();
  });
});


describe("reverse parse project state", () => {
  it("replaces old document selection and items when changing projects", async () => {
    fake.backend.setDocuments([makeDoc(1)]);
    fake.backend.setItems(1, [makeItem({ content: { name: "旧项目条目" } })]);
    const view = render(<ReverseParseStage />);
    await screen.findByText("旧项目条目");
    fake.backend.setDocuments([makeDoc(2)]);
    fake.backend.setItems(2, [makeItem({ id: 2, content: { name: "新项目条目" } })]);
    fake.projectId = 10;
    fake.epoch += 1;
    view.rerender(<ReverseParseStage />);
    expect(await screen.findByText("新项目条目")).toBeInTheDocument();
    expect(screen.queryByText("旧项目条目")).not.toBeInTheDocument();
    expect(screen.queryByText("doc-1.txt")).not.toBeInTheDocument();
  });

  it.each(["reload", "unmount"])("stops upload waiting on %s without treating it as failure", async (change) => {
    fake.backend.uploadReverseParseDocument.mockResolvedValueOnce({ document: makeDoc(1), task: { task_run_id: 7 } });
    let finish!: (task: { task_run_id: number; status: "succeeded" }) => void;
    fake.backend.waitForTask.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const view = render(<ReverseParseStage />);
    fireEvent.change(screen.getByLabelText("选择要解析的文件"), {
      target: { files: [new File(["story"], "story.txt", { type: "text/plain" })] },
    });
    await waitFor(() => expect(fake.backend.waitForTask).toHaveBeenCalled());
    const signal = fake.backend.waitForTask.mock.calls[0][3];
    if (change === "unmount") view.unmount();
    else {
      fake.epoch += 1;
      fake.hydrating = true;
      view.rerender(<ReverseParseStage />);
    }
    expect(signal?.aborted).toBe(true);
    await act(async () => { finish({ task_run_id: 7, status: "succeeded" }); });
    expect(fake.backend.fetchReverseParseDocument).not.toHaveBeenCalled();
    if (change === "reload") {
      fake.hydrating = false;
      view.rerender(<ReverseParseStage />);
      expect(screen.getByRole("button", { name: "上传文档并解析" })).toBeEnabled();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    }
  });
});


it.each([false, true])("retry preserves the latest selection (switched: %s)", async (switchDocument) => {
  fake.backend.setDocuments([makeDoc(1, { parse_status: "failed" }), makeDoc(2)]);
  fake.backend.setItems(2, [makeItem({ content: { name: "正在查看的第二份文档" } })]);
  let finish!: (result: { task: { task_run_id: number } }) => void;
  fake.backend.retryReverseParse.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
  if (!switchDocument) fake.backend.waitForTask.mockReturnValueOnce(new Promise(() => {}));
  render(<ReverseParseStage />);
  await screen.findByText("正在查看的第二份文档");
  fireEvent.click(screen.getByRole("button", { name: /doc-1.txt/ }));
  fireEvent.click(screen.getByRole("button", { name: "重新解析" }));
  if (switchDocument) {
    fireEvent.click(screen.getByRole("button", { name: /doc-2.txt/ }));
    await screen.findByText("正在查看的第二份文档");
  }
  const reads = fake.backend.fetchReverseParseDocument.mock.calls.length;
  await act(async () => { finish({ task: { task_run_id: 8 } }); });
  expect(screen.getByRole("button", { name: /doc-1.txt/ })).toHaveTextContent("解析中");
  if (switchDocument) {
    expect(screen.getByText("正在查看的第二份文档")).toBeInTheDocument();
    expect(fake.backend.fetchReverseParseDocument).toHaveBeenCalledTimes(reads);
    expect(fake.backend.waitForTask).not.toHaveBeenCalled();
  } else {
    expect(fake.backend.waitForTask).toHaveBeenCalledWith(9, 8, undefined, expect.any(AbortSignal));
  }
});
