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
  buildWorkbenchCandidates,
  defaultWorkbenchSeed,
} from "@/features/analyst-workbench/analyst-fixture";
import { AnalystWorkbench } from "@/features/analyst-workbench/analyst-workbench";
import { WorkbenchCanvasKernel } from "@/features/analyst-workbench/workbench-canvas-kernel";
import {
  workbenchCanvasLayoutStorageKey,
  type WorkbenchCanvasLayoutIdentity,
} from "@/features/analyst-workbench/workbench-canvas-layout";
import {
  CaseSessionProvider,
  useCaseSession,
} from "@/features/case-session/case-session-provider";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

function renderWorkbench(children = <AnalystWorkbench />) {
  return render(<CaseSessionProvider>{children}</CaseSessionProvider>);
}

const generatedCandidates = buildWorkbenchCandidates(
  {
    creativeIntent: "一名档案修复师追查三份共同失真的记录。",
    reasoningProposition: "三份可靠记录为何共同证明不存在的时间？",
    authorAnswer: "共享校准层在封存前改写了索引。",
    constraints: ["不得用梦境解释记录冲突。"],
  },
  1,
);

function CandidateSeedHarness() {
  const { patchState, previewCandidate } = useCaseSession();

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

describe("analyst workbench", () => {
  it("keeps the left rail scoped to work-draft objects", () => {
    renderWorkbench();
    expect(
      screen.getByRole("complementary", { name: "卷宗对象导航" }),
    ).toBeInTheDocument();
    expect(screen.getByText("卷宗对象导航")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /切换项目/u }),
    ).not.toBeInTheDocument();
  });

  it("keeps all eight workbench views available, including direct evidence comparison", () => {
    const { container } = renderWorkbench();
    const canvas = container.querySelector("#analyst-canvas") as HTMLElement;
    const tablist = screen.getByRole("tablist", { name: "主画布视图" });

    expect(within(tablist).getAllByRole("tab")).toHaveLength(8);
    expect(
      within(tablist).getByRole("tab", { name: /证据对比/ }),
    ).toBeInTheDocument();

    fireEvent.click(
      within(tablist).getByRole("tab", { name: /证据对比/ }),
    );

    expect(canvas).toHaveAttribute("data-workbench-view", "evidence");
    expect(
      within(tablist).getByRole("tab", { name: /证据对比/ }),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("事件前已知")).toBeInTheDocument();
  });

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

  it("keeps the timeline focused on events without a synchronized graph", () => {
    const { container } = renderWorkbench();

    expect(screen.getByRole("heading", { name: "雾港失联前 34 分钟" })).toBeInTheDocument();
    expect(screen.queryByText("同步关系图")).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-testid="timeline-resize-handle"]'),
    ).not.toBeInTheDocument();
    expect(
      container.querySelector('[aria-label="事件关系图"]'),
    ).not.toBeInTheDocument();
  });

  it("limits ArrowUp and ArrowDown timeline navigation to the timeline view", () => {
    const { container } = renderWorkbench();
    const canvas = container.querySelector("#analyst-canvas") as HTMLElement;
    const selectedBefore = canvas.getAttribute("data-selected-object-id");

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    fireEvent.keyDown(canvas, { key: "ArrowDown" });

    expect(canvas).toHaveAttribute("data-workbench-view", "relations");
    expect(canvas).toHaveAttribute("data-selected-object-id", selectedBefore);
  });

  it("compiles the dossier into novel and script formats with gate gating", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /编译中心/ }));
    expect(screen.getByText("同一份卷宗，多种形式")).toBeInTheDocument();
    // 默认选中小说：章节预览来自事件序列
    expect(screen.getByText(/第一章 · 码头监控断帧/)).toBeInTheDocument();
    // 有未解决问题时编译按钮禁用并提示
    expect(
      screen.getByRole("button", { name: "先处理验证问题" }),
    ).toBeDisabled();

    // 切换到剧本
    fireEvent.click(screen.getByRole("button", { name: /剧本/ }));
    expect(screen.getByText(/剧本杀手册 · 雾港失联案/)).toBeInTheDocument();
    expect(screen.getByText(/角色：秦彻、林岚、唐默/)).toBeInTheDocument();
  });

  it("compiles freely once validation issues are resolved", () => {
    renderWorkbench(<CandidateSeedHarness />);
    fireEvent.click(screen.getByRole("button", { name: "载入结构候选" }));
    fireEvent.click(screen.getByRole("tab", { name: /编译中心/ }));

    // 结构候选仍有两条问题待处理，先解决
    fireEvent.click(
      screen.getAllByRole("button", { name: /沈砚提前认出/ })[0],
    );
    fireEvent.click(screen.getByRole("button", { name: "请求 Agent 补丁" }));
    fireEvent.click(screen.getByRole("button", { name: "批准并局部重算" }));
    fireEvent.click(screen.getByRole("tab", { name: /验证问题/ }));
    fireEvent.click(
      screen.getAllByRole("button", { name: /顾遥在两个权限门内同时签名/ })[0],
    );
    fireEvent.click(screen.getByRole("button", { name: "标记已知例外" }));

    // 处理问题时主画布被切到证据对照，回到编译中心
    fireEvent.click(screen.getByRole("tab", { name: /编译中心/ }));
    const compileButton = screen.getByRole("button", {
      name: "编译为小说",
    });
    expect(compileButton).not.toBeDisabled();
    fireEvent.click(compileButton);
    expect(screen.getByText(/已生成 小说 产物/)).toBeInTheDocument();
  });

  it("switches the relation canvas into pan mode without activating nodes", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    fireEvent.click(screen.getByRole("button", { name: "平移工具" }));
    const board = container.querySelector(
      '[aria-label="事件关系图"]',
    ) as HTMLElement;

    expect(board).toHaveAttribute("data-tool", "pan");
    expect(
      screen.getByRole("button", { name: "平移工具" }),
    ).toHaveAttribute("aria-pressed", "true");

    // 平移模式下点击节点不触发对象联动
    const node = within(board).getByRole("button", { name: /内部接应者/ });
    expect(node.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(node);
    expect(node.getAttribute("aria-pressed")).toBe("false");
  });

  it("zooms the relation graph canvas with the zoom controls", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    expect(
      screen.getByRole("button", { name: "缩放比例 100%" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(
      screen.getByRole("button", { name: "缩放比例 125%" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "缩小" }));
    expect(
      screen.getByRole("button", { name: "缩放比例 100%" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    fireEvent.click(screen.getByRole("button", { name: "缩放比例 125%" }));
    expect(
      screen.getByRole("button", { name: "缩放比例 100%" }),
    ).toBeInTheDocument();
  });

  it("describes every canvas control on hover or keyboard focus", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));

    const expectedTooltips = [
      ["选择工具", "点击切换多选；框选一组节点"],
      ["平移工具", "拖动画布"],
      ["适配全部", "适配全部节点"],
      ["全屏查看画布", "全屏查看画布"],
      ["重新整理", "重新整理布局"],
      ["撤销布局修改", "撤销布局修改"],
      ["重做布局修改", "重做布局修改"],
      ["缩小", "缩小画布"],
      ["缩放比例 100%", "恢复为 100%"],
      ["放大", "放大画布"],
    ] as const;

    expectedTooltips.forEach(([name, tooltip]) => {
      expect(screen.getByRole("button", { name })).toHaveAttribute(
        "data-tooltip",
        tooltip,
      );
    });
  });

  it("shows distinct node-type colors in a top-left legend", () => {
    const { container } = renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));

    const legend = container.querySelector(
      '[aria-label="节点类型图例"]',
    ) as HTMLElement;
    expect(legend).toBeInTheDocument();
    ["人物", "证据", "事件", "地点", "假设"].forEach((label) => {
      expect(within(legend).getByText(label)).toBeInTheDocument();
    });

    const graph = screen.getByRole("application", { name: "事件关系图" });
    const person = within(graph).getByRole("button", { name: "人物：秦彻" });
    const evidence = within(graph).getByRole("button", {
      name: "证据：07 号门禁记录",
    });
    const personLegend = within(legend).getByText("人物");
    const evidenceLegend = within(legend).getByText("证据");

    expect(person.style.getPropertyValue("--canvas-node-accent")).toBe(
      personLegend.style.getPropertyValue("--canvas-node-accent"),
    );
    expect(evidence.style.getPropertyValue("--canvas-node-accent")).toBe(
      evidenceLegend.style.getPropertyValue("--canvas-node-accent"),
    );
    expect(person.style.getPropertyValue("--canvas-node-accent")).not.toBe(
      evidence.style.getPropertyValue("--canvas-node-accent"),
    );
  });

  it("highlights the union of relationships for multiple selected nodes", async () => {
    render(
      <div style={{ width: 800, height: 500 }}>
        <WorkbenchCanvasKernel
          ariaLabel="多选关系测试画布"
          direction="LR"
          edges={[
            { id: "a-x", source: "a", target: "x", label: "A-X" },
            { id: "b-y", source: "b", target: "y", label: "B-Y" },
          ]}
          externalSelectedNodeIds={[]}
          identity={{ scope: "test:multi-select", revision: "1", view: "relations" }}
          nodes={[
            { id: "a", variant: "relationship", kind: "person", caption: "人物", label: "甲", ariaLabel: "人物：甲", accent: "#4b6fb1", selectableId: "a", width: 120, height: 52 },
            { id: "b", variant: "relationship", kind: "person", caption: "人物", label: "乙", ariaLabel: "人物：乙", accent: "#4b6fb1", selectableId: "b", width: 120, height: 52 },
            { id: "x", variant: "relationship", kind: "event", caption: "事件", label: "事件甲", ariaLabel: "事件：事件甲", accent: "#c54b4b", selectableId: "x", width: 120, height: 52 },
            { id: "y", variant: "relationship", kind: "event", caption: "事件", label: "事件乙", ariaLabel: "事件：事件乙", accent: "#c54b4b", selectableId: "y", width: 120, height: 52 },
          ]}
          onActivateNode={() => undefined}
        />
      </div>,
    );
    const graph = screen.getByRole("application", {
      name: "多选关系测试画布",
    });
    const first = within(graph).getByRole("button", { name: "人物：甲" });
    const second = within(graph).getByRole("button", { name: "人物：乙" });
    const firstRelation = within(graph).getByRole("button", {
      name: "事件：事件甲",
    });
    const secondRelation = within(graph).getByRole("button", {
      name: "事件：事件乙",
    });

    fireEvent.click(first);
    fireEvent.click(second);

    await waitFor(() => {
      expect(first.closest(".react-flow__node")).toHaveClass("selected");
      expect(second.closest(".react-flow__node")).toHaveClass("selected");
    });
    expect(firstRelation).toHaveAttribute("data-related", "true");
    expect(secondRelation).toHaveAttribute("data-related", "true");

    fireEvent.click(first);
    await waitFor(() =>
      expect(first.closest(".react-flow__node")).not.toHaveClass("selected"),
    );
    expect(second.closest(".react-flow__node")).toHaveClass("selected");
    expect(first).toHaveAttribute("aria-pressed", "false");
    expect(second).toHaveAttribute("aria-pressed", "true");
    expect(firstRelation).toHaveAttribute("data-related", "false");
    expect(secondRelation).toHaveAttribute("data-related", "true");
  });

  it("falls back with a non-blocking message when fullscreen is unavailable", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    fireEvent.click(screen.getByRole("button", { name: "全屏查看画布" }));

    expect(
      screen.getByText("当前浏览器不支持画布全屏；仍可使用适配与缩放查看。"),
    ).toHaveAttribute("role", "status");
  });

  it("enters and exits canvas fullscreen while keeping the button state in sync", async () => {
    const fullscreenElementDescriptor = Object.getOwnPropertyDescriptor(
      document,
      "fullscreenElement",
    );
    const exitFullscreenDescriptor = Object.getOwnPropertyDescriptor(
      document,
      "exitFullscreen",
    );
    let fullscreenElement: Element | null = null;

    try {
      Object.defineProperty(document, "fullscreenElement", {
        configurable: true,
        get: () => fullscreenElement,
      });
      Object.defineProperty(document, "exitFullscreen", {
        configurable: true,
        value: vi.fn(async () => {
          fullscreenElement = null;
          document.dispatchEvent(new Event("fullscreenchange"));
        }),
      });

      renderWorkbench();
      fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
      const enterButton = screen.getByRole("button", {
        name: "全屏查看画布",
      });
      const shell = enterButton.closest("[data-layout-key]") as HTMLElement;
      const requestFullscreen = vi.fn(async () => {
        fullscreenElement = shell;
        document.dispatchEvent(new Event("fullscreenchange"));
      });
      Object.defineProperty(shell, "requestFullscreen", {
        configurable: true,
        value: requestFullscreen,
      });

      fireEvent.click(enterButton);
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "退出全屏" })).toHaveAttribute(
          "data-tooltip",
          "退出全屏",
        ),
      );
      expect(requestFullscreen).toHaveBeenCalledOnce();
      expect(shell).toHaveAttribute("data-fullscreen", "true");

      fireEvent.click(screen.getByRole("button", { name: "退出全屏" }));
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "全屏查看画布" }),
        ).toBeInTheDocument(),
      );
      expect(document.exitFullscreen).toHaveBeenCalledOnce();
      expect(shell).toHaveAttribute("data-fullscreen", "false");
    } finally {
      if (fullscreenElementDescriptor) {
        Object.defineProperty(
          document,
          "fullscreenElement",
          fullscreenElementDescriptor,
        );
      } else {
        Reflect.deleteProperty(document, "fullscreenElement");
      }
      if (exitFullscreenDescriptor) {
        Object.defineProperty(
          document,
          "exitFullscreen",
          exitFullscreenDescriptor,
        );
      } else {
        Reflect.deleteProperty(document, "exitFullscreen");
      }
    }
  });

  it("treats automatic relayout as one undoable layout command", async () => {
    const identity: WorkbenchCanvasLayoutIdentity = {
      scope: `fixture:${defaultWorkbenchSeed.id}:current`,
      revision: defaultWorkbenchSeed.caseMeta.revision,
      view: "relations",
    };
    localStorage.setItem(
      workbenchCanvasLayoutStorageKey(identity),
      JSON.stringify({
        version: 1,
        identity,
        positions: { "PER-001": { x: 999, y: 777 } },
        viewport: { x: 0, y: 0, zoom: 1 },
        updatedAt: 1,
      }),
    );
    const { container } = renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    const board = container.querySelector(
      '[aria-label="事件关系图"]',
    ) as HTMLElement;
    const node = within(board).getByRole("button", { name: /秦彻/ });
    const wrapper = node.closest(".react-flow__node") as HTMLElement;

    await waitFor(() => expect(wrapper.style.transform).toMatch(/999px.*777px/));
    fireEvent.click(screen.getByRole("button", { name: "重新整理" }));
    expect(
      screen.getByRole("button", { name: "撤销布局修改" }),
    ).not.toBeDisabled();
    expect(wrapper.style.transform).not.toMatch(/999px.*777px/);

    fireEvent.click(screen.getByRole("button", { name: "撤销布局修改" }));
    expect(wrapper.style.transform).toMatch(/999px.*777px/);
    fireEvent.click(screen.getByRole("button", { name: "重做布局修改" }));
    expect(wrapper.style.transform).not.toMatch(/999px.*777px/);
  });

  it("renders the export preview with the gate checklist", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /导出预览/ }));
    expect(screen.getByText("发布门禁")).toBeInTheDocument();
    expect(screen.getByText("结构完整性")).toBeInTheDocument();
    expect(screen.getByText("语义验证")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "生成导出包" }),
    ).toBeDisabled();
    expect(screen.getByText(/先处理右侧检查器中的 S0\/S1 问题/)).toBeInTheDocument();
  });

  it("renders the dossier editor with object-derived fields", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /卷宗编辑器/ }));
    expect(screen.getByText("参与人物")).toBeInTheDocument();
    expect(screen.getByText("关联证据")).toBeInTheDocument();
    expect(screen.getByText("候选假设")).toBeInTheDocument();
    expect(screen.getAllByText("引用来源").length).toBeGreaterThan(0);
    // 默认事件 EV-1825 的对象推导字段值
    expect(screen.getByDisplayValue("秦彻、唐默")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue(/07 号门禁记录、海关电台录音 A-13/),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("内部接应者")).toBeInTheDocument();
    expect(screen.getByText(/引用 02/)).toBeInTheDocument();
  });

  it("opens the agent dialog and answers a preset instruction from the seed", async () => {
    renderWorkbench();

    fireEvent.click(
      screen.getByRole("button", { name: "打开卷宗统筹 Agent 对话" }),
    );
    expect(
      screen.getByRole("region", { name: "卷宗统筹 Agent 对话" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/我是卷宗统筹 Agent/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全卷宗体检" }));
    expect(
      await screen.findByText(/对“雾港失联案”的体检完成/),
    ).toBeInTheDocument();
    expect(screen.getByText(/S0 角色提前知道“第五人权限”/)).toBeInTheDocument();
    expect(screen.getByText(/推理路径 1 条/)).toBeInTheDocument();

    fireEvent.change(
      screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
      { target: { value: "按发布门禁检查导出就绪度。" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(
      await screen.findByText(/导出前检查（REV.12）/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭 Agent 对话" }));
    expect(
      screen.queryByRole("region", { name: "卷宗统筹 Agent 对话" }),
    ).not.toBeInTheDocument();
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

  it("collapses and restores the context inspector", () => {
    const { container } = renderWorkbench();
    const body = container.querySelector(
      '[data-inspector-open="true"]',
    ) as HTMLElement;

    fireEvent.click(
      screen.getByRole("button", { name: "收起上下文检查器" }),
    );
    expect(body).toHaveAttribute("data-inspector-open", "false");
    expect(body.style.getPropertyValue("--inspector-width")).toBe("0px");
    expect(
      screen.getByRole("button", { name: "展开上下文检查器" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "展开上下文检查器" }),
    );
    expect(body).toHaveAttribute("data-inspector-open", "true");
    expect(body.style.getPropertyValue("--inspector-width")).toBe("350px");
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

  it("enables relation-node dragging only while the select tool is active", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    const board = container.querySelector(
      '[aria-label="事件关系图"]',
    ) as HTMLElement;
    const node = within(board).getByRole("button", {
      name: /内部接应者/,
    });
    const wrapper = node.closest(".react-flow__node") as HTMLElement;

    expect(wrapper).toHaveClass("draggable");
    fireEvent.click(screen.getByRole("button", { name: "平移工具" }));
    expect(wrapper).not.toHaveClass("draggable");
  });

  it("keeps reasoning nodes and edges read-only at the domain level", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /推理图/ }));
    const board = container.querySelector(
      '[aria-label="推理画布"]',
    ) as HTMLElement;
    const node = screen.getByRole("button", {
      name: "证据：07 号门禁记录",
    });

    node.focus();
    fireEvent.keyDown(node, { key: "Delete" });
    fireEvent.keyDown(node, { key: "Backspace" });
    expect(node).toBeInTheDocument();
    expect(board.querySelectorAll(".react-flow__handle.connecting")).toHaveLength(0);
  });

  it("switches the complete workbench seed and keeps candidate navigation in the title row", () => {
    const { container } = renderWorkbench(<CandidateSeedHarness />);

    expect(
      screen.queryByRole("region", { name: "工作稿接力状态" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "载入结构候选" }));

    expect(container.querySelector('[data-workbench-seed="brief-1-structure"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /来源抽屉/ }));
    expect(screen.getAllByText("缺页校准案").length).toBeGreaterThan(0);
    expect(screen.getByText("封存前 39 分钟的校准链")).toBeInTheDocument();
    expect(screen.getByText("交接台口述记录 C-07")).toBeInTheDocument();
    const returnLink = screen.getByRole("link", { name: "← 返回候选卷" });
    expect(returnLink.parentElement?.querySelector("strong")).toHaveTextContent("缺页校准案");
    expect(screen.queryByText("预览稿")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "采用为当前工作稿" }),
    );
    expect(screen.queryByText("当前工作稿")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "载入推理候选" }));
    expect(container.querySelector('[data-workbench-seed="brief-1-reasoning"]')).toBeTruthy();
    expect(screen.getAllByText("第七码互证案").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "载入旧简报候选" }));
    expect(screen.getByRole("link", { name: "← 返回候选卷" })).toBeInTheDocument();
    expect(screen.queryByText("旧简报")).not.toBeInTheDocument();
    expect(screen.queryByText("旧简报候选仅供预览，不可采用")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "采用为当前工作稿" }),
    ).not.toBeInTheDocument();
  });
});
