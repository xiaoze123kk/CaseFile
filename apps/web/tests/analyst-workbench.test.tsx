import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildPrototypeDraftCandidates,
} from "@/features/analyst-workbench/analyst-fixture";
import { AnalystWorkbench } from "@/features/analyst-workbench/analyst-workbench";
import {
  DemoPrototypeProvider,
  useDemoPrototype,
} from "@/features/demo-prototype/demo-prototype-provider";

afterEach(cleanup);

function renderWorkbench(children = <AnalystWorkbench />) {
  return render(<DemoPrototypeProvider>{children}</DemoPrototypeProvider>);
}

const generatedCandidates = buildPrototypeDraftCandidates(
  {
    creativeIntent: "一名档案修复师追查三份共同失真的记录。",
    reasoningProposition: "三份可靠记录为何共同证明不存在的时间？",
    authorAnswer: "共享校准层在封存前改写了索引。",
    constraints: ["不得用梦境解释记录冲突。"],
  },
  1,
);

function CandidateSeedHarness() {
  const { patchState, previewCandidate } = useDemoPrototype();

  function loadCandidate(index: number, frozenBriefVersion = 1) {
    patchState({
      draftCandidates: generatedCandidates,
      frozenBriefVersion,
    });
    previewCandidate(generatedCandidates[index].id);
  }

  return (
    <>
      <button onClick={() => loadCandidate(0)} type="button">
        载入结构候选
      </button>
      <button onClick={() => loadCandidate(2)} type="button">
        载入推理候选
      </button>
      <button onClick={() => loadCandidate(1, 2)} type="button">
        载入旧简报候选
      </button>
      <AnalystWorkbench />
    </>
  );
}

describe("analyst workbench prototype", () => {
  it("moves from an S0 issue to evidence comparison and explicit patch approval", () => {
    renderWorkbench();

    expect(screen.getByText("雾港失联前 34 分钟")).toBeInTheDocument();
    fireEvent.click(
      screen.getAllByRole("button", {
        name: /角色提前知道“第五人权限”/,
      })[0],
    );

    expect(
      screen.getAllByRole("heading", {
        name: "角色提前知道“第五人权限”",
      }),
    ).toHaveLength(2);
    expect(screen.getByText("事件前已知")).toBeInTheDocument();
    expect(screen.getByText("证据实际进入")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "请求 Agent 补丁" }));
    expect(screen.getByText("Agent 建议")).toBeInTheDocument();
    expect(screen.getByText("等待批准")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "批准并局部重算" }),
    );

    expect(
      screen.getByRole("button", { name: /验证1 个问题/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("批准 Agent 补丁")).toBeInTheDocument();
  });

  it("opens the command palette with Ctrl K and restores trigger focus", async () => {
    renderWorkbench();
    const trigger = screen.getByRole("button", { name: "打开命令面板" });
    trigger.focus();

    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    const dialog = await screen.findByRole("dialog", {
      name: "定位对象、视图或问题",
    });

    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("textbox", { name: "搜索命令或对象" })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "关闭命令面板" }));

    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("provides text alternatives for the graph and direct access to source evidence", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    expect(screen.getByText("查看关系表与文字摘要")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /转写文本/ }));
    expect(
      screen.getByText(/该术语第一次进入秦彻可用的知识状态/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "对照验证问题" }),
    ).toBeInTheDocument();
  });

  it("renders the reasoning canvas with evidence steps, outcome, and table alternative", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /推理图/ }));
    expect(screen.getByText("证据如何收束到假设")).toBeInTheDocument();
    expect(
      screen.getByText(/推理表 · 第五人权限如何进入码头/),
    ).toBeInTheDocument();
    expect(screen.getByText("复听")).toBeInTheDocument();
    expect(screen.getAllByText("解释竞争").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "证据：07 号门禁记录" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "结论：内部接应者" }),
    ).toBeInTheDocument();
  });

  it("exposes three competing reasoning paths and links evidence to the object rail", () => {
    const { container } = renderWorkbench(<CandidateSeedHarness />);
    fireEvent.click(screen.getByRole("button", { name: "载入推理候选" }));
    fireEvent.click(screen.getByRole("tab", { name: /推理图/ }));

    expect(screen.getByText(/推理表 · 第七码由谁写入/)).toBeInTheDocument();
    expect(screen.getByText(/推理表 · 黎衡能否被完全排除/)).toBeInTheDocument();
    expect(
      screen.getByText(/推理表 · 三份记录是否彼此独立互证/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("已排除").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "结论：三份记录彼此独立" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getAllByRole("button", { name: "证据：互证机房运算带" })[0],
    );
    expect(
      container.querySelector('[data-workbench-seed="brief-1-reasoning"]'),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: /互证机房运算带/ }).some(
        (button) => button.getAttribute("aria-pressed") === "true",
      ),
    ).toBe(true);
  });

  it("resizes the timeline panel by dragging the split handle", () => {
    const { container } = renderWorkbench();

    const handle = container.querySelector(
      '[data-testid="timeline-resize-handle"]',
    ) as HTMLElement;
    const overview = handle.parentElement as HTMLElement;
    expect(overview.style.getPropertyValue("--timeline-width")).toBe("340px");

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 180, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });

    expect(overview.style.getPropertyValue("--timeline-width")).toBe("420px");
  });

  it("resizes the inspector by dragging the split handle", () => {
    const { container } = renderWorkbench();

    const handle = container.querySelector(
      '[data-testid="inspector-resize-handle"]',
    ) as HTMLElement;
    const body = handle.parentElement as HTMLElement;
    expect(body.style.getPropertyValue("--inspector-width")).toBe("350px");

    // 检查器在右侧：向右拖 80px → 宽度减小
    fireEvent.pointerDown(handle, { clientX: 200, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 280, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });

    expect(body.style.getPropertyValue("--inspector-width")).toBe("270px");
  });

  it("resizes the object rail by dragging the split handle", () => {
    const { container } = renderWorkbench();

    const handle = container.querySelector(
      '[data-testid="rail-resize-handle"]',
    ) as HTMLElement;
    const body = handle.parentElement as HTMLElement;
    expect(body.style.getPropertyValue("--rail-width")).toBe("254px");

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 220, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });

    expect(body.style.getPropertyValue("--rail-width")).toBe("374px");
  });

  it("drags a relation graph node across the canvas", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    const board = container.querySelector(
      '[aria-label="事件关系图"]',
    ) as HTMLElement;
    vi.spyOn(board, "getBoundingClientRect").mockReturnValue({
      bottom: 400,
      height: 400,
      left: 0,
      right: 500,
      top: 0,
      width: 500,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    const node = within(board).getByRole("button", {
      name: /内部接应者/,
    });

    fireEvent.pointerDown(node, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(board, { clientX: 250, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(board, { pointerId: 1 });

    expect(node.style.getPropertyValue("--node-x")).toBe("50%");
    expect(node.style.getPropertyValue("--node-y")).toBe("50%");
  });

  it("drags a reasoning node across the canvas", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /推理图/ }));
    const board = container.querySelector(
      '[aria-label="推理画布"]',
    ) as HTMLElement;
    vi.spyOn(board, "getBoundingClientRect").mockReturnValue({
      bottom: 400,
      height: 400,
      left: 0,
      right: 500,
      top: 0,
      width: 500,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    const node = screen.getByRole("button", {
      name: "证据：07 号门禁记录",
    });

    fireEvent.pointerDown(node, { clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(board, { clientX: 250, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(board, { pointerId: 1 });

    expect(node.style.getPropertyValue("--node-x")).toBe("50%");
    expect(node.style.getPropertyValue("--node-y")).toBe("50%");
  });

  it("switches the complete workbench seed and exposes preview, current, and stale states", () => {
    const { container } = renderWorkbench(<CandidateSeedHarness />);

    expect(
      screen.queryByRole("region", { name: "工作稿接力状态" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "载入结构候选" }));

    expect(container.querySelector('[data-workbench-seed="brief-1-structure"]')).toBeTruthy();
    expect(screen.getAllByText("缺页校准案").length).toBeGreaterThan(0);
    expect(screen.getByText("封存前 39 分钟的校准链")).toBeInTheDocument();
    expect(screen.getByText("交接台口述记录 C-07")).toBeInTheDocument();
    expect(screen.getByText("预览稿")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "采用为当前工作稿" }),
    );
    expect(screen.getByText("当前工作稿")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "载入推理候选" }));
    expect(container.querySelector('[data-workbench-seed="brief-1-reasoning"]')).toBeTruthy();
    expect(screen.getAllByText("第七码互证案").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "载入旧简报候选" }));
    expect(screen.getByText("旧简报")).toBeInTheDocument();
    expect(
      screen.getByText("旧简报候选仅供预览，不可采用"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "采用为当前工作稿" }),
    ).not.toBeInTheDocument();
  });
});
