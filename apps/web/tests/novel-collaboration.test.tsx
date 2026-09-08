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
import { novelTextDiff } from "@/features/novel-workspace/novel-diff";
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
import { NovelDiffReview } from "@/features/novel-workspace/novel-editor-review";
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
