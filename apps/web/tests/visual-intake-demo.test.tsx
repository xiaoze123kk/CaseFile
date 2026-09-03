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
  fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
  fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
  fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));
}

async function freezeVersionOne() {
  reachConfirmation();
  fireEvent.click(screen.getByRole("button", { name: /确认建案并继续/u }));
  await waitFor(
    () => expect(screen.getByRole("heading", { name: "这一版建案已经归档。" })).toBeInTheDocument(),
    { timeout: 2500 },
  );
}

function expectFlowSpineAtTop() {
  const spine = screen.getByRole("navigation", { name: "建案依赖进度" });
  expect(spine.parentElement?.firstElementChild).toBe(spine);
  expect(
    within(spine).getByRole("button", { name: "返回视觉 Demo 首页" }),
  ).toBeInTheDocument();
  expect(
    within(spine).getByRole("button", { name: "重置演示" }),
  ).toBeInTheDocument();
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
          screen.getByRole("heading", { level: 2, name: "创意方向" }),
        ).toBeInTheDocument();
        expect(
          screen.getByText(
            "可按时代、场景、氛围与关键词自由组合，或留空由 Agent 自主发挥。",
          ),
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "古代" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "末日废土" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "热血" })).toBeInTheDocument();
        expect(
          screen.getByRole("textbox", { name: "关键词" }),
        ).toHaveAttribute("placeholder", "例如：时间循环、双胞胎（用逗号分隔）");
        expect(
          screen.getByRole("button", { name: /继续关键追问/u }),
        ).toBeDisabled();
        expect(
          screen.queryByRole("group", {
            name: "选择一个值得继续追问的方向",
          }),
        ).not.toBeInTheDocument();

        const nearFuture = screen.getByRole("button", { name: "近未来" });
        const modern = screen.getByRole("button", { name: "现代" });
        fireEvent.click(nearFuture);
        fireEvent.click(modern);
        fireEvent.change(screen.getByRole("textbox", { name: "关键词" }), {
          target: { value: "时间循环、双胞胎" },
        });
        expect(nearFuture).toHaveAttribute("aria-pressed", "true");
        expect(modern).toHaveAttribute("aria-pressed", "true");

        fireEvent.click(screen.getByRole("button", { name: "生成创意候选" }));
        expect(
          screen.getByRole("group", {
            name: "选择一个值得继续追问的方向",
          }),
        ).toBeInTheDocument();
        expect(
          screen.getByRole("button", {
            name: "选择此方向：被删除的第十三分钟",
          }),
        ).toHaveAttribute("aria-pressed", "true");
        const selectedCard = screen.getByRole("article", {
          name: /^创意方向 2：被删除的第十三分钟$/u,
        });
        expect(within(selectedCard).getByText("核心悬念")).toBeInTheDocument();
        expect(within(selectedCard).getByText("混合推理")).toBeInTheDocument();
        expect(within(selectedCard).getByText("按作者底牌展开")).toBeInTheDocument();
        expect(within(selectedCard).getByText("目标体验")).toBeInTheDocument();
        expect(within(selectedCard).getByText("设计风险")).toBeInTheDocument();
        expect(within(selectedCard).getByText("预计规模")).toBeInTheDocument();
        expect(
          within(selectedCard).getByRole("button", {
            name: /重新生成：被删除的第十三分钟/u,
          }),
        ).toBeInTheDocument();
        expect(
          screen.getByRole("button", { name: /继续关键追问/u }),
        ).toBeEnabled();
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
    expect(
      screen.getByRole("button", { name: "返回视觉 Demo 首页" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Source.*起案/u }),
    ).toHaveAttribute("aria-current", "step");
    verify();

    fireEvent.click(
      screen.getByRole("button", { name: /更改起案方式|重新选择起点/u }),
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "从哪里开始？" }),
    ).toBeInTheDocument();
  });

  it("keeps generated path B preferences and the selected direction across the question flow", () => {
    render(<VisualIntakeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /帮我想一个/u }));

    fireEvent.click(screen.getByRole("button", { name: "远未来" }));
    fireEvent.click(screen.getByRole("button", { name: "海洋" }));
    fireEvent.change(screen.getByRole("textbox", { name: "关键词" }), {
      target: { value: "潮汐、遗嘱" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成创意候选" }));
    fireEvent.click(
      screen.getByRole("button", { name: "选择此方向：潮汐带回了第二份遗嘱" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));

    expect(
      screen.getByRole("heading", { name: "真相应该怎样收束？" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/两份笔迹相同的遗嘱/u)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^返回起案$/u }));
    expect(screen.getByRole("button", { name: "远未来" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("textbox", { name: "关键词" })).toHaveValue(
      "潮汐、遗嘱",
    );
    expect(
      screen.getByRole("button", { name: "选择此方向：潮汐带回了第二份遗嘱" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps the formal inspiration assets disclosure folded until the author opens it", () => {
    render(<VisualIntakeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /帮我想一个/u }));
    fireEvent.click(screen.getByRole("button", { name: "生成创意候选" }));

    const assetSummary = screen.getByText("灵感资产（3 个历史候选）");
    const assetDetails = assetSummary.closest("details");
    expect(assetDetails).not.toHaveAttribute("open");
    expect(
      screen.getAllByRole("article", { name: /^历史创意方向/u }),
    ).toHaveLength(3);

    fireEvent.click(assetSummary);
    expect(assetDetails).toHaveAttribute("open");
    const batchSummary = screen.getByText("第 1 批（3 个候选）");
    expect(batchSummary.closest("details")).not.toHaveAttribute("open");

    fireEvent.click(batchSummary);
    expect(batchSummary.closest("details")).toHaveAttribute("open");
    expect(
      screen.getAllByRole("article", { name: /^历史创意方向/u }),
    ).toHaveLength(3);
  });

  it("keeps path A progress visible with a separate polish control and bottom-right primary action", () => {
    render(<VisualIntakeDemo />);
    openPathA();

    expect(
      screen.getByRole("button", { name: /Source.*起案/u }),
    ).toHaveAttribute("aria-current", "step");
    const control = screen.getByRole("region", {
      name: "需要 Agent 帮你整理表达吗？",
    });
    expect(
      within(control).getByRole("radio", { name: /轻度校对/u }),
    ).toBeChecked();
    expect(
      within(control).getByRole("button", { name: "生成润色校样" }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: /整理这个想法/u })).toBeEnabled();
    expect(
      screen.queryByRole("region", { name: "逐字确认 Agent 改了什么。" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the branded progress spine as the first row on every flow page", () => {
    render(<VisualIntakeDemo />);
    openPathA();
    expectFlowSpineAtTop();

    fireEvent.click(screen.getByRole("button", { name: /整理这个想法/u }));
    expectFlowSpineAtTop();
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));
    expectFlowSpineAtTop();

    fireEvent.click(screen.getByRole("button", { name: /确认建案并继续/u }));
    expectFlowSpineAtTop();
  });

  it("reviews an Agent polish candidate before explicitly adopting it", () => {
    render(<VisualIntakeDemo />);
    openPathA();

    const source = screen.getByRole("textbox", { name: "最初想法" });
    const original = (source as HTMLTextAreaElement).value;
    fireEvent.click(screen.getByRole("radio", { name: /表达优化/u }));
    fireEvent.click(screen.getByRole("button", { name: "生成润色校样" }));

    const panel = screen.getByRole("region", {
      name: "逐字确认 Agent 改了什么。",
    });
    expect(within(panel).getByText("表达优化")).toBeInTheDocument();
    expect(within(panel).getByLabelText("当前作者原稿")).toHaveValue(original);
    expect(panel).toHaveTextContent("修改了 3 处表达");

    const draft = within(panel).getByLabelText("编辑 Agent 润色工作稿");
    fireEvent.change(draft, { target: { value: "采用后的独立校样。" } });
    fireEvent.click(within(panel).getByRole("button", { name: "采用这版" }));

    expect(screen.getByRole("textbox", { name: "最初想法" })).toHaveValue(
      "采用后的独立校样。",
    );
    expect(
      screen.queryByRole("region", { name: "逐字确认 Agent 改了什么。" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("已采用 Agent 润色校样");
  });

  it("can close the polish proof without changing the source", async () => {
    render(<VisualIntakeDemo />);
    openPathA();
    const source = screen.getByRole("textbox", { name: "最初想法" });
    const original = (source as HTMLTextAreaElement).value;

    fireEvent.click(screen.getByRole("button", { name: "生成润色校样" }));
    fireEvent.click(screen.getByRole("button", { name: "保留原文" }));

    expect(source).toHaveValue(original);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "生成润色校样" }),
      ).toHaveFocus(),
    );
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

    fireEvent.click(
      screen.getByRole("radio", { name: /^作者心中已有唯一真相/u }),
    );
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));

    expect(
      screen.getByRole("heading", { level: 1, name: "建案确认" }),
    ).toBeInTheDocument();
    expect(screen.getByText("作者提供答案")).toBeInTheDocument();
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

  it("moves through one follow-up at a time and preserves every answer when going back", () => {
    const revisedAnswer = "确认是谁让三份独立记录同时指向不存在的时间。";
    render(<VisualIntakeDemo />);
    enterQuestions();

    expect(
      screen.getByRole("heading", { name: "真相应该怎样收束？" }),
    ).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("radio", { name: /作者心中已有唯一真相/u }),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "真相收束补充" }), {
      target: { value: "答案必须能解释三份记录为何同时失真。" },
    });

    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    expect(
      screen.getByRole("heading", {
        name: "这份作品最终要回答哪一个核心问题？",
      }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "核心问题答案" }), {
      target: { value: revisedAnswer },
    });

    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    expect(
      screen.getByRole("heading", {
        name: "这次建案应该先聚焦到什么范围？",
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /多人物并行/u }));

    fireEvent.click(screen.getByRole("button", { name: /^← 上一题$/u }));
    expect(screen.getByRole("textbox", { name: "核心问题答案" })).toHaveValue(
      revisedAnswer,
    );
    fireEvent.click(screen.getByRole("button", { name: /^← 上一题$/u }));
    expect(
      screen.getByRole("radio", { name: /作者心中已有唯一真相/u }),
    ).toBeChecked();
    expect(screen.getByRole("textbox", { name: "真相收束补充" })).toHaveValue(
      "答案必须能解释三份记录为何同时失真。",
    );

    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    expect(screen.getByRole("radio", { name: /多人物并行/u })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));
    expect(screen.getByText("创作边界").closest("details")).toHaveTextContent("多人物并行");
  });

  it("keeps the Case Brief compact, expands creative boundaries on demand, and confirms through a visible transition", async () => {
    render(<VisualIntakeDemo />);
    reachConfirmation();

    expect(screen.getByText(/Agent 已经把你的起案内容与回答整理/u)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /保持开放/u })).toBeChecked();
    expect(screen.getByRole("radio", { name: /作者提供答案/u })).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /让 Agent 在深稿中提出答案/u }),
    ).toBeInTheDocument();
    const outline = screen.getByRole("heading", { name: "内容骨架" }).closest("section");
    expect(within(outline as HTMLElement).getByRole("list").children).toHaveLength(4);

    const boundarySummary = screen.getByText("创作边界");
    const boundaryDetails = boundarySummary.closest("details");
    expect(boundaryDetails).not.toHaveAttribute("open");
    fireEvent.click(boundarySummary);
    expect(boundaryDetails).toHaveAttribute("open");
    expect(within(boundaryDetails as HTMLElement).getByText("必须保留")).toBeInTheDocument();
    expect(within(boundaryDetails as HTMLElement).getByText("禁止出现")).toBeInTheDocument();
    expect(within(boundaryDetails as HTMLElement).getAllByText("必须")).toHaveLength(2);
    expect(within(boundaryDetails as HTMLElement).getAllByText("偏好")).toHaveLength(2);
    expect(boundaryDetails).not.toHaveTextContent(/hard|soft|constraint|atomic/iu);

    fireEvent.click(screen.getByRole("button", { name: /确认建案并继续/u }));
    expect(screen.getByRole("heading", { name: "正在确认建案" })).toBeInTheDocument();
    expect(screen.getByText("正在整理创作边界与生成依据……")).toBeInTheDocument();
    await screen.findByRole("heading", { name: "建案完成" }, { timeout: 1500 });
    expect(screen.getByText("CaseFile 已准备好进入深稿阶段。")).toBeInTheDocument();
    await screen.findByRole(
      "heading",
      { name: "这一版建案已经归档。" },
      { timeout: 1500 },
    );
    expect(screen.getByLabelText("Brief V1 已冻结")).toBeInTheDocument();
  });

  it("returns each editable Brief field to its source step", () => {
    render(<VisualIntakeDemo />);
    reachConfirmation();

    fireEvent.click(screen.getByRole("button", { name: "返回第 2 题编辑" }));
    expect(
      screen.getByRole("heading", { name: "这份作品最终要回答哪一个核心问题？" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));
    fireEvent.click(screen.getByRole("button", { name: "返回起案编辑" }));
    expect(screen.getByRole("heading", { name: "把你的想法告诉我" })).toBeInTheDocument();
  });

  it("keeps the old Brief until a changed answer is explicitly refreshed", () => {
    const revisedAnswer = "判断档案修复师是否伪造了共同的时间缺口。";
    render(<VisualIntakeDemo />);
    reachConfirmation();

    expect(screen.getByText(initialAnswer)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /返回关键追问/u }),
    );
    fireEvent.click(screen.getByRole("button", { name: /^← 上一题$/u }));
    fireEvent.change(screen.getByRole("textbox", { name: "核心问题答案" }), {
      target: { value: revisedAnswer },
    });
    fireEvent.click(screen.getByRole("button", { name: /下一题/u }));
    fireEvent.click(screen.getByRole("button", { name: /查看建案确认/u }));

    expect(screen.getByRole("alert")).toHaveTextContent("关键回答已经变化");
    expect(screen.getByText(initialAnswer)).toBeInTheDocument();
    expect(screen.getByText(`新回答待同步：${revisedAnswer}`)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /确认建案并继续/u }),
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
      screen.getByRole("button", { name: /确认建案并继续/u }),
    ).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Brief 已依据新的回答重新整理",
    );
  });

  it("freezes V1 and creates an editable V2 without losing version history", async () => {
    render(<VisualIntakeDemo />);
    await freezeVersionOne();

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

  it("requires restart confirmation when a frozen lineage returns home and chooses a new path", async () => {
    render(<VisualIntakeDemo />);
    await freezeVersionOne();

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
    await freezeVersionOne();

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
