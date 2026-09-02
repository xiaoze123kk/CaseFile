import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { VisualIntakeDemo } from "@/features/intake/visual-intake-demo";

const initialAnswer =
  "找出是谁制造了那段不存在的时间，以及三份可靠记录为什么会同时失真。";

afterEach(cleanup);

function openPathA() {
  fireEvent.click(screen.getByRole("button", { name: /我有一个想法/u }));
}

function enterQuestions() {
  openPathA();
  fireEvent.click(screen.getByRole("button", { name: /整理这个想法/u }));
}

function reachConfirmation() {
  enterQuestions();
  fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));
}

function freezeVersionOne() {
  reachConfirmation();
  fireEvent.click(screen.getByRole("button", { name: /确认 Brief V1/u }));
}

describe("visual intake demo", () => {
  it.each([
    {
      entrance: /我有一个想法/u,
      heading: "把你的想法告诉我",
      verify: () => {
        const source = screen.getByRole("textbox", { name: "最初想法" });
        expect((source as HTMLTextAreaElement).value).toContain("档案修复师");
        expect(
          screen.getByRole("button", { name: /整理这个想法/u }),
        ).toBeEnabled();
      },
    },
    {
      entrance: /帮我想一个/u,
      heading: "帮我想一个",
      verify: () => {
        expect(
          screen.getByRole("group", {
            name: "选择一个值得继续追问的方向",
          }),
        ).toBeInTheDocument();
        expect(
          screen.getByRole("radio", { name: /被删除的第十三分钟/u }),
        ).toBeChecked();
        const nearFuture = screen.getByRole("button", { name: "近未来" });
        const modern = screen.getByRole("button", { name: "现代" });
        fireEvent.click(modern);
        expect(modern).toHaveAttribute("aria-pressed", "true");
        expect(nearFuture).toHaveAttribute("aria-pressed", "false");
      },
    },
    {
      entrance: /我有已有内容/u,
      heading: "我有已有内容",
      verify: () => {
        expect(screen.getByText("雪夜来信.txt")).toBeInTheDocument();
        expect(
          screen.getByRole("button", { name: "移除示例" }),
        ).toBeInTheDocument();
      },
    },
  ])("opens the $heading first screen and can return home", ({ entrance, heading, verify }) => {
    render(<VisualIntakeDemo />);

    fireEvent.click(screen.getByRole("button", { name: entrance }));

    expect(
      screen.getByRole("heading", { level: 1, name: heading }),
    ).toBeInTheDocument();
    verify();

    fireEvent.click(
      screen.getByRole("button", { name: /更改起案方式|重新选择起点/u }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "从哪里开始？" }),
    ).toBeInTheDocument();
  });

  it("shares the question and confirmation flow and resets all local edits", () => {
    render(<VisualIntakeDemo />);
    openPathA();

    const source = screen.getByRole("textbox", { name: "最初想法" });
    fireEvent.change(source, {
      target: { value: "港口档案里多出了一班不存在的船。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /整理这个想法/u }));

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "只回答会改变方向的问题。",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("港口档案里多出了一班不存在的船。"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Decisions.*关键追问/u }),
    ).toHaveAttribute("aria-current", "step");

    fireEvent.click(screen.getByRole("radio", { name: /唯一真相/u }));
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));

    expect(
      screen.getByRole("heading", { level: 1, name: "建案确认" }),
    ).toBeInTheDocument();
    expect(screen.getByText("唯一真相")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Brief.*建案确认/u }),
    ).toHaveAttribute("aria-current", "step");

    fireEvent.click(screen.getByRole("button", { name: "重置演示" }));
    expect(
      screen.getByRole("heading", { level: 1, name: "从哪里开始？" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("演示已重置。");

    openPathA();
    expect(
      (screen.getByRole("textbox", { name: "最初想法" }) as HTMLTextAreaElement)
        .value,
    ).toContain("档案修复师");
  });

  it("keeps the old Brief until a changed answer is explicitly refreshed", () => {
    const revisedAnswer = "判断档案修复师是否伪造了共同的时间缺口。";
    render(<VisualIntakeDemo />);
    reachConfirmation();

    expect(screen.getByText(initialAnswer)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /返回关键追问/u }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "核心问题答案" }), {
      target: { value: revisedAnswer },
    });
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));

    expect(screen.getByRole("alert")).toHaveTextContent("关键回答已经变化");
    expect(screen.getByText(initialAnswer)).toBeInTheDocument();
    expect(screen.getByText(`新回答待同步：${revisedAnswer}`)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /确认 Brief V1/u }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /Brief.*需要更新/u }),
    ).toHaveAttribute("aria-current", "step");

    fireEvent.click(
      screen.getByRole("button", { name: /重新整理简报/u }),
    );

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(revisedAnswer)).toBeInTheDocument();
    expect(screen.queryByText(initialAnswer)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /确认 Brief V1/u }),
    ).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Brief 已依据新的回答重新整理",
    );
  });

  it("freezes V1 and creates an editable V2 without losing version history", async () => {
    render(<VisualIntakeDemo />);
    freezeVersionOne();

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "这一版建案已经归档。",
      }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Brief V1 已冻结")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    const frozenRail = screen.getByLabelText("Brief 版本");
    expect(within(frozenRail).getByText("Brief V1")).toBeInTheDocument();
    expect(within(frozenRail).getByText("已确认 · 冻结")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /修改建案/u }));
    const dialog = screen.getByRole("dialog", { name: "创建建案修订" });
    const createV2 = within(dialog).getByRole("button", { name: /创建 V2/u });
    await waitFor(() => expect(createV2).toHaveFocus());
    fireEvent.click(createV2);

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "审阅建案修订 V2",
      }),
    ).toBeInTheDocument();
    const revisionRail = screen.getByLabelText("Brief 版本");
    expect(within(revisionRail).getByText("Brief V1")).toBeInTheDocument();
    expect(within(revisionRail).getByText("Brief V2")).toBeInTheDocument();
    expect(within(revisionRail).getByText("已确认 · 冻结")).toBeInTheDocument();
    expect(within(revisionRail).getByText("编辑中")).toBeInTheDocument();
  });

  it("requires restart confirmation when a frozen lineage returns home and chooses a new path", () => {
    render(<VisualIntakeDemo />);
    freezeVersionOne();

    fireEvent.click(
      screen.getByRole("button", { name: "返回视觉 Demo 首页" }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "从哪里开始？" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /我有一个想法/u }));

    expect(
      screen.getByRole("dialog", { name: "从新的方向重新起案？" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "从哪里开始？" }),
    ).toBeInTheDocument();
  });

  it("restores restart focus on cancel and keeps a retained-version notice on confirm", async () => {
    render(<VisualIntakeDemo />);
    freezeVersionOne();

    const restart = screen.getByRole("button", { name: "重新起案" });
    fireEvent.click(restart);
    const dialog = screen.getByRole("dialog", {
      name: "从新的方向重新起案？",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    await waitFor(() =>
      expect(
        within(dialog).getByRole("button", { name: /保留旧版并重新起案/u }),
      ).toHaveFocus(),
    );

    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(restart).toHaveFocus());

    fireEvent.click(restart);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: /保留旧版并重新起案/u,
      }),
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "从哪里开始？" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "原建案 Brief V1 已保留",
    );
    expect(screen.getByText("已归档")).toBeInTheDocument();
  });
});
