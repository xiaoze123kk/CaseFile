import {
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
    uploadReverseParseDocument: vi.fn(async () => {
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
    retryReverseParse: vi.fn(async () => {
      throw new Error("本用例不执行重试");
    }),
    formBriefFromReverseParse: vi.fn(async () => ({ stage: "confirmation" })),
    waitForTask: vi.fn(async () => ({
      task_run_id: 1,
      status: "succeeded" as const,
    })),
  };
}

const fake = vi.hoisted(() => ({
  backend: buildFakeReverseParseBackend(),
  loadProject: vi.fn(),
}));

vi.mock("@/features/case-session/case-session-api", () => fake.backend);

vi.mock("@/features/case-session/case-session-provider", () => ({
  useCaseSession: () => ({
    activeProjectId: 9,
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
