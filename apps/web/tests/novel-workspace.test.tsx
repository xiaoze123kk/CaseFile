import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultWorkbenchSeed } from "@/features/analyst-workbench/analyst-fixture";
import { CompileCenterView } from "@/features/analyst-workbench/compile-center-view";
import { NovelWorkspace } from "@/features/novel-workspace/novel-workspace";
import {
  applyNovelChanges,
  createNovelDraft,
  exportNovel,
  manuscriptFromText,
  parseSavedDraft,
  type NovelManuscript,
} from "@/features/novel-workspace/novel-document";

const manuscript: NovelManuscript = {
  id: "compiled-1",
  title: "星夜时间表",
  sourceLabel: "编译初稿",
  chapters: [
    { id: "one", title: "第一章 · 约定", text: "他把时间表留在窗边。" },
    { id: "two", title: "第二章 · 改动", text: "窗边只剩下一道折痕。" },
  ],
};
const setup = (extra = {}) =>
  render(
    <NovelWorkspace
      scopeKey="project:49:draft:1"
      seed={defaultWorkbenchSeed}
      onBack={vi.fn()}
      {...extra}
    />,
  );

beforeEach(() => {
  HTMLDialogElement.prototype.showModal ??= function () {};
  HTMLDialogElement.prototype.close ??= function () {};
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(
    function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  );
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.removeAttribute("open");
  });
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("novel manuscript boundaries", () => {
  it("round trips exported Markdown without inventing a title chapter", () => {
    const imported = manuscriptFromText(
      exportNovel(manuscript.title, manuscript.chapters),
      "当前项目名",
    );
    expect(imported.title).toBe(manuscript.title);
    expect(
      imported.chapters.map(({ title, text }) => ({ title, text })),
    ).toEqual(manuscript.chapters.map(({ title, text }) => ({ title, text })));
  });
  it("retains preambles and splits Chinese and Markdown chapters", () => {
    const parsed = manuscriptFromText(
      "序言文字\r\n第一章 · 约定\r\n第一段\r\n\r\n第二段\r\n## 第二章\r\n结尾",
      "小说",
    );
    expect(parsed.chapters.map((chapter) => chapter.text)).toEqual([
      "序言文字",
      "第一段\n\n第二段",
      "结尾",
    ]);
  });
  it("refuses stale, unknown and duplicate changes without overwriting the original", () => {
    const draft = createNovelDraft(manuscript);
    const change = {
      chapterId: "one",
      before: manuscript.chapters[0].text,
      after: "新段落",
    };
    expect(applyNovelChanges(draft, [change], 2)).toBeNull();
    expect(
      applyNovelChanges(draft, [{ ...change, chapterId: "missing" }], 1),
    ).toBeNull();
    expect(applyNovelChanges(draft, [change, change], 1)).toBeNull();
    const next = applyNovelChanges(draft, [change], 1);
    expect(next?.chapters[0].text).toBe("新段落");
    expect(next?.original.chapters[0].text).toBe(manuscript.chapters[0].text);
    expect(() => parseSavedDraft('{"chapters":[]}')).toThrow();
  });
});

describe("novel workspace", () => {
  it("can recover an edited manuscript after importing a new one", () => {
    setup({ manuscript });
    fireEvent.change(screen.getByLabelText("第一章 · 约定正文"), {
      target: { value: "需要保留的作者修改" },
    });
    fireEvent.click(screen.getByRole("button", { name: "导入初稿" }));
    fireEvent.change(screen.getByLabelText("整篇小说初稿"), {
      target: { value: "第一章 · 新稿\n另一份初稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "打开初稿" }));
    expect(screen.getByLabelText("第一章 · 新稿正文")).toHaveValue(
      "另一份初稿",
    );
    fireEvent.click(screen.getByRole("button", { name: "历史稿" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复此稿" }));
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      "需要保留的作者修改",
    );
  });
  it("opens the dedicated workspace from the novel target", () => {
    const onOpenNovel = vi.fn();
    render(
      <CompileCenterView
        title={defaultWorkbenchSeed.caseMeta.title}
        onOpenNovel={onOpenNovel}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^小说/ }));
    expect(onOpenNovel).toHaveBeenCalledOnce();
  });
  it("does not present event summaries as generated prose and imports a full book", () => {
    setup();
    expect(screen.getByText("等待故事落笔")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "发送小说修改指令" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "导入全文" }));
    fireEvent.change(screen.getByLabelText("整篇小说初稿"), {
      target: { value: "第一章 · 约定\n他来了。\n第二章 · 改动\n他离开了。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "打开初稿" }));
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue("他来了。");
    fireEvent.change(screen.getByLabelText("当前章节"), {
      target: { value: "chapter-2" },
    });
    expect(screen.getByLabelText("第二章 · 改动正文")).toHaveValue(
      "他离开了。",
    );
  });
  it("saves edits separately, restores them on reopening, and keeps scopes isolated", () => {
    const view = setup({ manuscript });
    fireEvent.change(screen.getByLabelText("第一章 · 约定正文"), {
      target: { value: "他把改过的时间表留在窗边。" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "原始初稿" }));
    expect(screen.getByText("他把时间表留在窗边。")).toBeInTheDocument();
    view.unmount();
    const restored = setup({ manuscript });
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      "他把改过的时间表留在窗边。",
    );
    restored.unmount();
    setup({ manuscript, scopeKey: "project:50:draft:1" });
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      "他把时间表留在窗边。",
    );
  });
  it("shows a save failure without losing editable text", () => {
    setup({ manuscript });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    fireEvent.change(screen.getByLabelText("第一章 · 约定正文"), {
      target: { value: "还在当前页面的正文" },
    });
    expect(screen.getByRole("status")).toHaveTextContent("本地保存失败");
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      "还在当前页面的正文",
    );
  });
  it("freezes the whole manuscript and reviews changes before applying", async () => {
    const collaborate = vi.fn().mockResolvedValue({
      message: "建议缩短首句。",
      changes: [
        {
          chapterId: "one",
          before: manuscript.chapters[0].text,
          after: "时间表留在窗边。",
        },
      ],
    });
    setup({ manuscript, collaborate });
    fireEvent.change(screen.getByLabelText("小说修改指令"), {
      target: { value: "缩短首句" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送小说修改指令" }));
    await screen.findByText("建议缩短首句。");
    expect(collaborate.mock.calls[0][0].manuscript.chapters).toHaveLength(2);
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      manuscript.chapters[0].text,
    );
    fireEvent.click(screen.getByRole("button", { name: "查看 1 章修改" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "采纳全部修改",
      }),
    );
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      "时间表留在窗边。",
    );
  });
  it("retains failed instructions and blocks stale suggestions", async () => {
    const collaborate = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({
        message: "建议",
        changes: [
          {
            chapterId: "one",
            before: manuscript.chapters[0].text,
            after: "建议正文",
          },
        ],
      });
    setup({ manuscript, collaborate });
    fireEvent.change(screen.getByLabelText("小说修改指令"), {
      target: { value: "改写" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送小说修改指令" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("协作请求失败"),
    );
    expect(screen.getByLabelText("小说修改指令")).toHaveValue("改写");
    fireEvent.click(screen.getByRole("button", { name: "发送小说修改指令" }));
    await screen.findByText("建议");
    fireEvent.change(screen.getByLabelText("第一章 · 约定正文"), {
      target: { value: "作者刚刚改写的正文" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查看 1 章修改" }));
    fireEvent.click(screen.getByRole("button", { name: "采纳全部修改" }));
    expect(screen.getByRole("alert")).toHaveTextContent("正文已发生变化");
    expect(screen.getByLabelText("第一章 · 约定正文")).toHaveValue(
      "作者刚刚改写的正文",
    );
  });
});
