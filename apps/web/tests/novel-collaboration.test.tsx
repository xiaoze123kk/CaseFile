import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { NovelEditorView } from "@casefile/contracts";
import {
  createNovelDraft,
  type NovelDraft,
} from "@/features/novel-workspace/novel-document";
import { novelTextDiff, novelParagraphDiff } from "@/features/novel-workspace/novel-diff";
import { NovelParagraphReview } from "@/features/novel-workspace/novel-paragraph-review";
import { NovelEditorialSummary } from "@/features/novel-workspace/novel-editorial-summary";
import {
  codePointOffset,
  utf16Offset,
} from "@/features/novel-workspace/novel-selection";
import {
  useNovelEditor,
  remoteDraft,
} from "@/features/novel-workspace/use-novel-editor";
import { novelEditorApi as api } from "@/features/novel-workspace/novel-editor-api";
import { NovelAssistant } from "@/features/novel-workspace/novel-assistant";
import { NovelDiffReview, NovelServerHistory } from "@/features/novel-workspace/novel-editor-review";
import { NovelVersionPreview } from "@/features/novel-workspace/novel-version-preview";
vi.mock("@/features/novel-workspace/novel-editor-api", () => ({
  novelEditorApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    save: vi.fn(),
    submit: vi.fn(),
    stream: vi.fn(),
    cancel: vi.fn(),
    decide: vi.fn(),
    restore: vi.fn(),
    history: vi.fn(),
    checkpoint: vi.fn(),
    version: vi.fn(),
  },
}));
const scope = { projectId: 1, draftId: 2, revision: 1 };
const view: NovelEditorView = {
  id: 7,
  source_key: "local-1",
  source_label: "导入初稿",
  revision: 1,
  title: "雨夜",
  chapters: [{ id: "c1", title: "第一章", text: "雨落了。😀门开了。" }],
  original_chapters: [
    { id: "c1", title: "第一章", text: "雨落了。😀门开了。" },
  ],
  exchanges: [],
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.list).mockResolvedValue([]);
  vi.mocked(api.create).mockResolvedValue(view);
  vi.mocked(api.get).mockResolvedValue(view);
  vi.mocked(api.stream).mockResolvedValue();
});
afterEach(cleanup);
it.each([false, true])("按建议直接启动改写，保留输入草稿；提交失败：%s", async (failed) => {
  const reply = {
    id: 1, task_id: 9, revision: 1, mode: "discuss" as const,
    chapter_id: "c1", instruction: "这句怎么调整？",
    anchor: { chapter_id: "c1", start: 0, end: 4, text: "雨落了。", original: false },
    message: "建议缩短停顿，保留人物动作。",
    status: "succeeded", error: null, usage: {}, edits: [],
  };
  if (failed) vi.mocked(api.submit).mockRejectedValue(new Error("提交失败"));
  const editor = {
    view: { ...view, exchanges: [reply] },
    flush: vi.fn().mockResolvedValue(view),
    refresh: vi.fn().mockResolvedValue(undefined),
  } as unknown as ReturnType<typeof useNovelEditor>;
  render(<NovelAssistant project={1} editor={editor} chapterId="other"
    mode="discuss" onMode={vi.fn()} anchor={null} onAnchor={vi.fn()}
    onReview={vi.fn()} onLocate={vi.fn()} />);
  const input = screen.getByRole("textbox", { name: "小说修改指令" });
  fireEvent.change(input, { target: { value: "尚未发送的其他想法" } });
  const button = screen.getByRole("button", { name: "按建议改写" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(api.submit).toHaveBeenCalledTimes(1));
  expect(api.submit).toHaveBeenCalledWith(1, 7, expect.objectContaining({
    mode: "rewrite", chapter_id: "c1", scope: "selection", anchor: reply.anchor,
    instruction: `请按以下建议改写：\n${reply.message}`,
  }));
  await waitFor(() => expect(button).not.toBeDisabled());
  expect(input).toHaveValue("尚未发送的其他想法");
  if (failed) {
    expect(screen.getByRole("alert")).toHaveTextContent("提交失败");
    fireEvent.click(button);
    await waitFor(() => expect(api.submit).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.submit).mock.calls[1][2].request_key)
      .toBe(vi.mocked(api.submit).mock.calls[0][2].request_key);
  } else {
    expect(editor.refresh).toHaveBeenCalledTimes(1);
  }
});
it.each([
  ["重复。😀重复。", "重复。😀另一句。"],
  ["雨落了。门开了。", "雨落下来。木门开了。"],
  ["", "新文"],
  ["旧文", ""],
])("差异可精确重建原文与新文", (before, after) => {
  const diff = novelTextDiff(before, after);
  expect(
    diff
      .filter((d) => d.kind !== "insert")
      .map((d) => d.text)
      .join(""),
  ).toBe(before);
  expect(
    diff
      .filter((d) => d.kind !== "delete")
      .map((d) => d.text)
      .join(""),
  ).toBe(after);
});
it("选区的中文与 emoji 位置在两端一致", () => {
  const text = "雨😀门";
  expect(codePointOffset(text, 3)).toBe(2);
  expect(utf16Offset(text, 2)).toBe(3);
});
function useEditor(initial: NovelDraft | null) {
  const [draft, setDraft] = useState(initial);
  const editor = useNovelEditor(scope, draft, setDraft);
  return { draft, setDraft, editor };
}
it("旧本地稿迁移后获得服务器版本，保留原始稿", async () => {
  const initial = createNovelDraft({
    id: view.source_key,
    title: view.title,
    sourceLabel: view.source_label,
    chapters: view.chapters,
  });
  const { result } = renderHook(() => useEditor(initial));
  await waitFor(() => expect(result.current.draft?.remote?.id).toBe(7));
  expect(api.create).toHaveBeenCalledTimes(1);
  expect(result.current.draft?.original.chapters).toEqual(
    view.original_chapters,
  );
});
it("保存返回时保留请求期间的新输入", async () => {
  const { result } = renderHook(() => useEditor(remoteDraft(view)));
  await waitFor(() => expect(result.current.editor.loading).toBe(false));
  let finish!: (v: NovelEditorView) => void;
  vi.mocked(api.save).mockImplementation(
    () =>
      new Promise((r) => {
        finish = r;
      }),
  );
  act(() =>
    result.current.setDraft((d) => ({
      ...d!,
      revision: d!.revision + 1,
      chapters: [{ ...d!.chapters[0], text: "修改一" }],
    })),
  );
  let pending!: Promise<NovelEditorView | null>;
  act(() => {
    pending = result.current.editor.flush();
  });
  act(() =>
    result.current.setDraft((d) => ({
      ...d!,
      revision: d!.revision + 1,
      chapters: [{ ...d!.chapters[0], text: "修改二" }],
    })),
  );
  await act(async () => {
    finish({
      ...view,
      revision: 2,
      chapters: [{ ...view.chapters[0], text: "修改一" }],
    });
    await pending;
  });
  expect(result.current.draft?.chapters[0].text).toBe("修改二");
  expect(result.current.draft?.remote?.revision).toBe(2);
});
it("服务器出现新版本时保留未同步的本地稿", async () => {
  vi.mocked(api.get).mockResolvedValue({ ...view, revision: 2 });
  const draft = {
    ...remoteDraft(view),
    chapters: [{ ...view.chapters[0], text: "本地尚未保存" }],
  };
  const { result } = renderHook(() => useEditor(draft));
  await waitFor(() =>
    expect(result.current.editor.error).toContain("本地稿与服务器版本不同"),
  );
  expect(result.current.draft?.chapters[0].text).toBe("本地尚未保存");
  expect(api.save).not.toHaveBeenCalled();
});
it("选段发送绑定引用位置，不传整本正文", async () => {
  const editor = {
    view,
    loading: false,
    saving: false,
    error: "",
    flush: vi.fn().mockResolvedValue(view),
    refresh: vi.fn().mockResolvedValue(undefined),
  } as unknown as ReturnType<typeof useNovelEditor>;
  render(
    <NovelAssistant
      project={1}
      editor={editor}
      chapterId="c1"
      mode="rewrite"
      onMode={vi.fn()}
      anchor={{
        chapter_id: "c1",
        start: 5,
        end: 9,
        text: "门开了。",
        original: false,
        localRevision: 3,
        manuscriptKey: view.source_key,
      }}
      onAnchor={vi.fn()}
      onReview={vi.fn()}
      onLocate={vi.fn()}
      localRevision={3}
    />,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "小说修改指令" }), {
    target: { value: "慢一点" },
  });
  const input = screen.getByRole("textbox", { name: "小说修改指令" });
  expect(fireEvent.keyDown(input, { key: "Enter", shiftKey: true })).toBe(true);
  expect(fireEvent.keyDown(input, { key: "Enter", isComposing: true })).toBe(true);
  expect(fireEvent.keyDown(input, { key: "Enter", keyCode: 229 })).toBe(true);
  expect(editor.flush).not.toHaveBeenCalled();
  expect(fireEvent.keyDown(input, { key: "Enter" })).toBe(false);
  await waitFor(() =>
    expect(api.submit).toHaveBeenCalledWith(
      1,
      7,
      expect.objectContaining({
        mode: "rewrite",
        scope: "selection",
        expected_revision: 1,
        anchor: expect.objectContaining({ start: 5, end: 9 }),
      }),
    ),
  );
  expect(vi.mocked(api.submit).mock.calls[0][2]).not.toHaveProperty(
    "manuscript",
  );
});
it("差异理由和单组采纳相绑定", async () => {
  const exchange = {
    id: 1,
    task_id: 9,
    revision: 1,
    mode: "rewrite" as const,
    chapter_id: "c1",
    instruction: "调整",
    anchor: null,
    message: "修改说明",
    status: "succeeded",
    error: null,
    usage: {},
    edits: [
      {
        id: 2,
        start: 0,
        end: 4,
        before: "雨落了。",
        after: "雨落下来。",
        reason: "动作更连贯",
        status: "pending" as const,
      },
    ],
  };
  const editor = {
    view,
    flush: vi.fn().mockResolvedValue(view),
    decide: vi.fn().mockResolvedValue(undefined),
  } as unknown as ReturnType<typeof useNovelEditor>;
  render(
    <NovelDiffReview editor={editor} exchange={exchange} onClose={vi.fn()} />,
  );
  expect(screen.getByText("动作更连贯")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "采纳这组" }));
  await waitFor(() =>
    expect(editor.decide).toHaveBeenCalledWith(1, [2], "accept"),
  );
});


it.each([false, true])("整章重写显式选择范围，超限不提交：%s", async (oversized) => {
  const current = { ...view, chapters: [{ ...view.chapters[0], text: oversized ? "字".repeat(12001) : "雨落了。" }] };
  const editor = { view: current, flush: vi.fn().mockResolvedValue(current),
    refresh: vi.fn().mockResolvedValue(undefined) } as unknown as ReturnType<typeof useNovelEditor>;
  render(<NovelAssistant project={1} editor={editor} chapterId="c1"
    mode="rewrite" onMode={vi.fn()} anchor={null} onAnchor={vi.fn()}
    onReview={vi.fn()} onLocate={vi.fn()} />);
  expect(screen.getByRole("combobox", { name: "修改范围" })).toHaveValue("chapter");
  fireEvent.change(screen.getByRole("combobox", { name: "修改范围" }), { target: { value: "chapter_rewrite" } });
  expect(screen.getByText("改写边界（选填）").closest("details")).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("改写边界（选填）"));
  fireEvent.change(screen.getByRole("textbox", { name: "必须保留" }), { target: { value: "保留人物动机" } });
  fireEvent.change(screen.getByRole("textbox", { name: "允许调整" }), { target: { value: "调整对白" } });
  fireEvent.change(screen.getByRole("textbox", { name: "小说修改指令" }), { target: { value: "重写整章，让悬念更集中" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  if (oversized) {
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("12,000"));
    expect(api.submit).not.toHaveBeenCalled();
  } else {
    await waitFor(() => expect(api.submit).toHaveBeenCalledWith(1, 7, expect.objectContaining({
      mode: "rewrite", scope: "chapter_rewrite", chapter_id: "c1", anchor: null,
      requirements: { preserve: "保留人物动机", allow_changes: "调整对白" },
    })));
  }
});

it("整章候选作为一组审阅和采纳", async () => {
  const exchange = { id: 1, task_id: 9, revision: 1, mode: "rewrite" as const,
    scope: "chapter_rewrite" as const, chapter_id: "c1", instruction: "重写", anchor: null,
    message: "整章候选", status: "succeeded", error: null, usage: {},
    edits: [{ id: 2, start: 0, end: 4, before: "雨落了。", after: "雨点敲窗。", reason: "重排节奏", status: "pending" as const }] };
  const editor = { view, flush: vi.fn().mockResolvedValue(view), decide: vi.fn().mockResolvedValue(undefined) } as unknown as ReturnType<typeof useNovelEditor>;
  render(<NovelDiffReview editor={editor} exchange={exchange} onClose={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "整章重写审阅" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "修改后" }));
  expect(screen.getByText("雨点敲窗。")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "采纳这组" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "采纳整章" }));
  await waitFor(() => expect(editor.decide).toHaveBeenCalledWith(1, [2], "accept"));
});


it("历史记录明确当前版本，查看内容不会恢复正文", async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.mocked(api.history).mockResolvedValue([
    { revision: 2, title: "雨夜", reason: "ai_accept", created_at: "2026-09-08T10:00:00Z" },
    { revision: 1, title: "雨夜", reason: "original", created_at: "2026-09-08T09:00:00Z" },
  ]);
  vi.mocked(api.version).mockResolvedValue({ revision: 2, title: "雨夜", previous_title: "雨夜",
    chapters: [{ id: "c1", title: "第一章", text: "雨点敲窗。" }], previous_chapters: view.chapters });
  const editor = { view: { ...view, revision: 2 }, restore: vi.fn() } as unknown as ReturnType<typeof useNovelEditor>;
  render(<NovelServerHistory project={1} draftId={2} editor={editor} onClose={vi.fn()} />);
  await screen.findByText("当前版本");
  expect(screen.getByRole("button", { name: "正在使用" })).toBeDisabled();
  expect(screen.queryByText("其他小说稿件")).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "查看内容与改动" })[0]);
  await screen.findByText("雨点敲窗。");
  expect(screen.getByText("相较版本 1，1 章有变化")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "与上一版对比" }));
  expect(editor.restore).not.toHaveBeenCalled();
  expect(api.version).toHaveBeenCalledWith(1, 7, 2);
});

it("历史预览失败可重试，已删除章节仍可查看差异", async () => {
  vi.mocked(api.version).mockRejectedValueOnce(new Error("暂时断线")).mockResolvedValue({
    revision: 2, title: "雨夜", previous_title: "雨夜", chapters: [], previous_chapters: view.chapters,
  });
  render(<NovelVersionPreview project={1} manuscript={7} revision={2} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时断线");
  fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
  await screen.findByText("此章节在该版本中已删除。");
  fireEvent.click(screen.getByRole("button", { name: "与上一版对比" }));
  expect(screen.getByText(view.chapters[0].text).tagName).toBe("DEL");
});


it.each([
  ["第一段。\n\n第二段。", "第一段。\n\n新增。\n\n第二段。"],
  ["重复。\r\n重复。\r\n😀结束。", "重复。\r\n新段。\r\n😀结束。"],
  ["首段\n中段\n尾段", "尾段\n首段\n中段"],
  ["", "新段。"], ["旧段。", ""], ["\n\n前。\n", "前。\n\n"],
  ["甲。\n".repeat(600), "乙。\n".repeat(601)],
])("段落对齐保留原文与新文的全部内容和换行", (before, after) => {
  const rows = novelParagraphDiff(before, after);
  expect(rows.map((row) => row.before).join("")).toBe(before);
  expect(rows.map((row) => row.after).join("")).toBe(after);
});

it("段落差异可查看未修改段落，不提供单段采纳", () => {
  render(<NovelParagraphReview before={"保留段。\n\n旧段。"} after={"保留段。\n\n新段。"} />);
  expect(screen.queryByText("保留段。")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "只看变化段落" }));
  expect(screen.getByText("保留段。")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /采纳/ })).not.toBeInTheDocument();
});

it("旧候选的审阅不能显示为新候选的审阅结论", () => {
  render(<NovelEditorialSummary review={{ status: "incomplete", message: "复核未完成",
    candidate_hash: "b".repeat(64), revision_count: 1, reports: [{ candidate_hash: "a".repeat(64), round: 0,
      review: { summary: "旧稿仍有遗漏", action: "revise", revision_plan: "补回遗漏", findings: [] } }] }} />);
  expect(screen.getByText("编辑核对尚未完成")).toBeInTheDocument();
  expect(screen.getByText("旧稿仍有遗漏").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByText("编辑已核对候选")).not.toBeInTheDocument();
});


it("只有主动保存才新增版本，展示编号不使用自动保存修订号", async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.mocked(api.history).mockResolvedValue([
    { revision: 9, title: "雨夜", reason: "checkpoint", created_at: "2026-09-08T10:00:00Z" },
    { revision: 1, title: "雨夜", reason: "original", created_at: "2026-09-08T09:00:00Z" },
  ]);
  const checkpoint = vi.fn().mockRejectedValue(new Error("保存暂不可用"));
  const editor = { view: { ...view, revision: 12 }, checkpoint } as unknown as ReturnType<typeof useNovelEditor>;
  render(<NovelServerHistory project={1} draftId={2} editor={editor} onClose={vi.fn()} />);
  await screen.findByText("版本 2");
  expect(screen.queryByText("版本 9")).not.toBeInTheDocument();
  expect(checkpoint).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("保存暂不可用");
  expect(screen.getByRole("button", { name: "保存为新版本" })).toBeEnabled();
});


it("整章润色提交独立模式与作者边界", async () => {
  const editor = { view, flush: vi.fn().mockResolvedValue(view), refresh: vi.fn() } as unknown as ReturnType<typeof useNovelEditor>;
  render(<NovelAssistant project={1} editor={editor} chapterId="c1" mode="polish" onMode={vi.fn()} anchor={null} onAnchor={vi.fn()} onReview={vi.fn()} onLocate={vi.fn()} />);
  fireEvent.change(screen.getByRole("combobox", { name: "修改范围" }), { target: { value: "chapter_rewrite" } });
  expect(screen.getByRole("option", { name: "当前章节 · 整章润色" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "小说修改指令" }), { target: { value: "改善文笔与节奏" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(api.submit).toHaveBeenCalledWith(1, 7, expect.objectContaining({mode: "polish", scope: "chapter_rewrite", requirements: {preserve: "", allow_changes: ""}})));
});

it("展示重链路的阶段结果和真实修订次数", () => {
  render(<NovelEditorialSummary review={{status: "completed", message: "供你决定", candidate_hash: "a".repeat(64), revision_count: 2, reports: [], stages: [{phase: "judge", label: "Judge 逐项审核", status: "completed", summary: "发现重复", findings: ["开场仍重复"], candidate_hash: "a".repeat(64)}]}} />);
  expect(screen.getByText("已进行 2 次自动修订。")).toBeInTheDocument();
  expect(screen.getByText("Judge 逐项审核 · 已完成")).toBeInTheDocument();
  expect(screen.getByText("开场仍重复")).toBeInTheDocument();
});

it("新对话清空历史和引用，发送携带新起点，并保留历史入口", async () => {
  localStorage.clear();
  const reply = { id: 5, task_id: 9, revision: 1, mode: "discuss" as const,
    chapter_id: "c1", instruction: "旧要求", message: "旧答复", anchor: null,
    status: "succeeded", error: null, usage: {}, edits: [] };
  const current = {...view, exchanges: [reply]};
  const editor = {view: current, flush: vi.fn().mockResolvedValue(current), refresh: vi.fn()} as unknown as ReturnType<typeof useNovelEditor>;
  const onAnchor = vi.fn();
  render(<NovelAssistant project={1} editor={editor} chapterId="c1" mode="discuss"
    onMode={vi.fn()} anchor={null} onAnchor={onAnchor} onReview={vi.fn()} onLocate={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", {name: "新对话"}));
  expect(screen.queryByText("旧答复")).not.toBeInTheDocument();
  expect(onAnchor).toHaveBeenCalledWith(null);
  fireEvent.click(screen.getByRole("button", {name: "历史对话"}));
  expect(screen.getByText("旧答复")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "返回当前对话"}));
  fireEvent.change(screen.getByRole("textbox", {name: "小说修改指令"}), {target: {value: "重新开始"}});
  fireEvent.submit(screen.getByRole("textbox", {name: "小说修改指令"}).closest("form")!);
  await waitFor(() => expect(api.submit).toHaveBeenCalledWith(1, 7, expect.objectContaining({history_after_exchange_id: 5})));
  cleanup();
  render(<NovelAssistant project={1} editor={editor} chapterId="c1" mode="discuss"
    onMode={vi.fn()} anchor={null} onAnchor={onAnchor} onReview={vi.fn()} onLocate={vi.fn()} />);
  expect(screen.queryByText("旧答复")).not.toBeInTheDocument();
  localStorage.clear();
});
