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
import { ReasoningGraphView } from "@/features/analyst-workbench/workbench-reasoning-graph";
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
  it("merges the workbench and dossier into one mode with the object index in its rail", () => {
    renderWorkbench();
    expect(
      screen.getByRole("complementary", { name: "当前模式导航" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /卷宗\s*对象档案/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("tab", { name: /^工作台$/ }),
    );
    expect(screen.getByRole("region", { name: "对象目录结果" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /切换项目/u }),
    ).not.toBeInTheDocument();
  });

  it("groups analysis tools separately from compile tools", () => {
    const { container } = renderWorkbench();
    const canvas = container.querySelector("#analyst-canvas") as HTMLElement;
    const tablist = screen.getByRole("tablist", { name: "分析工具" });

    expect(within(tablist).getAllByRole("tab")).toHaveLength(5);
    expect(
      within(tablist).queryByRole("tab", { name: /卷宗编辑器/ }),
    ).not.toBeInTheDocument();
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
    expect(
      screen.getByRole("heading", { name: "第五人权限如何进入码头" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "线索对比" }),
    ).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("tab", { name: "编译作品" }));
    expect(
      within(screen.getByRole("tablist", { name: "编译工具" })).getAllByRole("tab"),
    ).toHaveLength(1);
    const compileTools = screen.getByRole("tablist", { name: "编译工具" });
    expect(screen.queryByRole("tab", { name: /导出预览/ })).not.toBeInTheDocument();
    expect(within(compileTools).getByRole("tab", { name: /编译中心/ }).querySelector('[data-view="compile"]')).toBeInTheDocument();
  });

  it("renders the evidence comparison matrix with per-cell assessments", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /证据对比/ }));

    expect(
      screen.getByRole("button", { name: "假设：内部接应者" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "假设：外部入侵者" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "信息：07 号门禁记录" }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: "07 号门禁记录 对 内部接应者：支持 · 强",
      }),
    );

    const detail = screen.getByRole("complementary", {
      name: "证据判定依据",
    });
    expect(
      within(detail).getByText(/门禁覆盖签名属于内部 R4 权限/),
    ).toBeInTheDocument();
    expect(within(detail).getByText(/支持 · 强/)).toBeInTheDocument();
  });

  it("switches the evidence view between the matrix and validation issues", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /证据对比/ }));

    expect(
      screen.getByRole("heading", { name: "第五人权限如何进入码头" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "待处理问题" }));
    expect(screen.getByText("事件前已知")).toBeInTheDocument();
    expect(screen.getByText("证据实际进入")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "线索对比" }));
    expect(
      screen.getByRole("heading", { name: "第五人权限如何进入码头" }),
    ).toBeInTheDocument();
  });

  it("moves from an S0 issue to evidence comparison and explicit patch approval", () => {
    renderWorkbench();

    expect(screen.getByText("雾港失联前 34 分钟")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: /验证\s*2\s*个问题/,
      }),
    );

    expect(
      screen.getByRole("heading", {
        name: "角色提前知道“第五人权限”",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /角色提前知道“第五人权限”/,
      }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("事件前已知")).toBeInTheDocument();
    expect(screen.getByText("证据实际进入")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "请求 Agent 补丁" }));
    expect(screen.getByText("Agent 建议已生成，等待人工批准。")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "批准并局部重算" }),
    );

    expect(
      screen.getByRole("button", { name: /验证\s*1\s*个问题/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText("批准 Agent 补丁")).not.toBeInTheDocument();
  });

  it("opens the command palette with Ctrl K and restores trigger focus", async () => {
    renderWorkbench();
    const trigger = screen.getByRole("button", { name: /搜索对象或命令/ });
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

  it("provides a text alternative for the graph without the removed source drawer", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    expect(screen.getByText("查看关系表与文字摘要")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /来源抽屉/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps top navigation compact and places reset in a dismissible more menu", () => {
    renderWorkbench();
    const header = screen.getByRole("tablist", { name: "主要工作模式" }).closest("header")!;
    const modes = within(header).getByRole("tablist", { name: "主要工作模式" });
    expect(within(modes).getAllByRole("tab").map((tab) => tab.textContent?.trim())).toEqual(["工作台", "分析", "编译作品"]);
    expect(within(header).getByRole("link", { name: "返回建案中心" })).toHaveAttribute("href", "/");
    const more = within(header).getByLabelText("更多工作台操作");
    const details = more.closest("details")!;
    expect(details.open).toBe(false);
    fireEvent.click(more);
    expect(details.open).toBe(true);
    expect(within(details).getByRole("button", { name: "重置工作台数据" })).toBeInTheDocument();
    fireEvent.keyDown(more, { key: "Escape" });
    expect(details.open).toBe(false);
    expect(more).toHaveFocus();
    fireEvent.click(more);
    fireEvent.blur(more, { relatedTarget: within(header).getByRole("button", { name: "打开模型服务设置" }) });
    expect(details.open).toBe(false);
  });

  it("pauses relationship motion without changing the canvas layout or selection", () => {
    const { container } = renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    const toggle = screen.getByRole("button", { name: "关系图动效" });
    const shell = container.querySelector('[data-layout-key][data-scene="relations"]');
    const before = [...container.querySelectorAll('.react-flow__node')].map((node) => node.getAttribute("style"));
    const selectedBefore = [...container.querySelectorAll('[data-selected="true"]')];
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(shell).toHaveAttribute("data-motion", "paused");
    expect([...container.querySelectorAll('.react-flow__node')].map((node) => node.getAttribute("style"))).toEqual(before);
    expect([...container.querySelectorAll('[data-selected="true"]')]).toEqual(selectedBefore);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(shell).toHaveAttribute("data-motion", "running");
  });

  it("renders the reasoning canvas with evidence steps and outcome without reasoning tables", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /推理分析/ }));
    expect(screen.getByText("证据如何收束到假设")).toBeInTheDocument();
    expect(screen.queryByText(/推理表 ·/)).not.toBeInTheDocument();
    expect(screen.getByText("复听")).toBeInTheDocument();
    expect(screen.getAllByText("解释竞争").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "证据：07 号门禁记录" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "假设：内部接应者" }),
    ).toBeInTheDocument();
  });

  it("exposes three competing reasoning paths and links evidence to the object rail", () => {
    const { container } = renderWorkbench(<CandidateSeedHarness />);
    fireEvent.click(screen.getByRole("button", { name: "载入推理候选" }));
    fireEvent.click(screen.getByRole("tab", { name: /推理分析/ }));

    expect(screen.getAllByText("3 条路径").length).toBeGreaterThan(0);
    expect(screen.queryByText(/推理表 ·/)).not.toBeInTheDocument();
    expect(screen.getAllByText("已排除").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "假设：隐藏的第四索引" }),
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

  it("switches to the explicit competition matrix and keeps object selection shared", () => {
    const onSelectObject = vi.fn();
    const seed = {
      ...defaultWorkbenchSeed,
      reasoningGroups: [
        {
          resolutionSpecId: "res_access",
          question: "谁进入了受限区域？",
          hypotheses: [
            { id: "hyp_insider", title: "内部人员进入", outcome: "supported" as const },
            { id: "hyp_outsider", title: "外部人员闯入", outcome: "contested" as const },
          ],
          information: [
            { id: "info_gate", title: "门禁记录", reliability: "high" },
          ],
          assessments: [
            {
              hypothesisId: "hyp_insider",
              informationId: "info_gate",
              effect: "supports" as const,
              strength: "strong" as const,
              rationale: "刷卡权限与进入时间一致。",
            },
          ],
        },
      ],
    };

    render(
      <ReasoningGraphView
        layoutScope="matrix-test"
        onSelectObject={onSelectObject}
        seed={seed}
        selectedObjectId={null}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "竞争矩阵" }));
    const canvas = screen.getByRole("application", { name: "竞争矩阵画布" });
    expect(canvas).toBeInTheDocument();
    expect(
      within(canvas).getByRole("button", { name: "假设：内部人员进入" }),
    ).toBeInTheDocument();
    expect(
      within(canvas).getByRole("button", { name: "假设：外部人员闯入" }),
    ).toBeInTheDocument();
    expect(
      within(canvas).getByRole("button", { name: "信息：门禁记录" }),
    ).toBeInTheDocument();
    // 每个单元格是一条带标签的交互边，未评估单元格同样可见。
    expect(
      screen.getByRole("button", {
        name: "门禁记录 对 内部人员进入：支持 · 强",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "门禁记录 对 外部人员闯入：未评估",
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "假设：内部人员进入" }));
    expect(onSelectObject).toHaveBeenLastCalledWith("hyp_insider");

    fireEvent.click(
      screen.getByRole("button", {
        name: "门禁记录 对 内部人员进入：支持 · 强",
      }),
    );
    expect(onSelectObject).toHaveBeenLastCalledWith("info_gate");
    const detail = screen.getByRole("complementary", { name: "判定依据" });
    expect(within(detail).getByText(/判定依据 · 内部人员进入/)).toBeInTheDocument();
    expect(within(detail).getByText("支持 · 强")).toBeInTheDocument();
    expect(within(detail).getByText(/刷卡权限与进入时间一致/)).toBeInTheDocument();
  });

  it("shows conclusion values with creator-facing labels instead of storage keys", () => {
    render(
      <ReasoningGraphView
        layoutScope="conclusion-display"
        onSelectObject={vi.fn()}
        selectedObjectId={null}
        seed={{
          ...defaultWorkbenchSeed,
          reasoningGroups: [
            {
              resolutionSpecId: "res_access",
              question: "谁进入了受限区域？",
              hypotheses: [],
              information: [],
              assessments: [],
              conclusion: {
                resolutionSpecId: "res_access",
                question: "谁进入了受限区域？",
                outcome: "answer",
                reviewStatus: "proposed",
                summary: "进入者是值班员。",
                values: [{ label: "嫌疑人", value: "值班员" }],
                selectedHypothesisIds: [],
                supportingReasoningPathIds: [],
                relatedEventIds: [],
                rationale: "门禁记录与值班时段一致。",
                unresolvedGaps: [],
              },
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "展开结论" }));

    expect(screen.getByText("嫌疑人")).toBeInTheDocument();
    expect(screen.getByText("值班员")).toBeInTheDocument();
    expect(screen.queryByText("slot_perpetrator")).not.toBeInTheDocument();
    expect(screen.queryByText("ent_167_002")).not.toBeInTheDocument();
  });

  it("renders the three honest matrix empty states without fixture inference", () => {
    const onSelectObject = vi.fn();
    const baseProps = {
      layoutScope: "matrix-empty",
      onSelectObject,
      selectedObjectId: null,
    };

    const { rerender } = render(
      <ReasoningGraphView
        {...baseProps}
        seed={{ ...defaultWorkbenchSeed, reasoningGroups: [] }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "竞争矩阵" }));
    expect(screen.getByText("当前工作稿还没有可比较的假设。")).toBeInTheDocument();

    rerender(
      <ReasoningGraphView
        {...baseProps}
        seed={{
          ...defaultWorkbenchSeed,
          reasoningGroups: [
            {
              resolutionSpecId: "res_single",
              question: "单一解释",
              hypotheses: [{ id: "hyp_one", title: "唯一假设", outcome: "supported" }],
              information: [],
              assessments: [],
              conclusion: {
                resolutionSpecId: "res_single",
                question: "单一解释",
                outcome: "undetermined",
                reviewStatus: "confirmed",
                summary: "当前仍需保留唯一可用解释。",
                values: [],
                selectedHypothesisIds: ["hyp_one"],
                supportingReasoningPathIds: ["path_one"],
                relatedEventIds: [],
                rationale: "尚无第二个可比较解释。",
                unresolvedGaps: ["缺少替代解释。"],
              },
            },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "竞争矩阵" }));
    expect(
      screen.getByText("当前问题只有一个假设，至少需要两个解释才能比较。"),
    ).toBeInTheDocument();
    expect(screen.getByText("作者已确认")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开结论" }));
    expect(screen.getByText("当前仍需保留唯一可用解释。")).toBeInTheDocument();

    rerender(
      <ReasoningGraphView
        {...baseProps}
        seed={{
          ...defaultWorkbenchSeed,
          reasoningGroups: [
            {
              resolutionSpecId: "res_missing",
              question: "尚无评估",
              hypotheses: [
                { id: "hyp_one", title: "假设一", outcome: "supported" },
                { id: "hyp_two", title: "假设二", outcome: "contested" },
              ],
              information: [],
              assessments: [],
            },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "竞争矩阵" }));
    expect(screen.getByText("已有竞争解释，但尚未生成显式证据评估。")).toBeInTheDocument();
  });

  it("keeps the timeline focused on events without a synchronized graph", () => {
    const { container } = renderWorkbench();

    expect(screen.getByRole("heading", { name: "雾港失联前 34 分钟" })).toBeInTheDocument();
    expect(screen.queryByText("同步关系图")).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-testid="timeline-resize-handle"]'),
    ).not.toBeInTheDocument();
    expect(
      container.querySelector('[aria-label="实体关系图"]'),
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

  it("presents work entries without previews, options or chat", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: "编译作品" }));
    expect(screen.getByRole("heading", { name: "选择作品形式" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "编译预览" })).not.toBeInTheDocument();
    expect(screen.queryByText("编译选项")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "CaseFile Agent 聊天框" })).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "对象上下文" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^小说/ })).toBeEnabled();
    for (const name of [/^剧本/, /^互动脚本/, /^作者卷宗/, /^测试材料/]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    fireEvent.click(screen.getByRole("tab", { name: "分析" }));
    expect(screen.getByRole("region", { name: "CaseFile Agent 聊天框" })).toBeInTheDocument();
  });

  it("opens the novel workspace and returns to the entry selector", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: "编译作品" }));
    fireEvent.click(screen.getByRole("button", { name: /^小说/ }));
    expect(screen.getByRole("main", { name: "小说协作工作台" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编译中心" }));
    expect(screen.getByRole("heading", { name: "选择作品形式" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "CaseFile Agent 聊天框" })).not.toBeInTheDocument();
  });
  it("switches the relation canvas into pan mode without activating nodes", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    fireEvent.click(screen.getByRole("button", { name: "平移工具" }));
    const board = container.querySelector(
      '[aria-label="实体关系图"]',
    ) as HTMLElement;

    expect(board).toHaveAttribute("data-tool", "pan");
    expect(
      screen.getByRole("button", { name: "平移工具" }),
    ).toHaveAttribute("aria-pressed", "true");

    // 平移模式下点击节点不触发对象联动
    const node = within(board).getByRole("button", { name: /唐默/ });
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

  it("shows entities and location points as a circular constellation", () => {
    const { container } = renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));

    const legend = container.querySelector(
      '[aria-label="节点类型图例"]',
    ) as HTMLElement;
    expect(legend).toBeInTheDocument();
    expect(within(legend).getByText("人物")).toBeInTheDocument();
    expect(within(legend).getByText("地点")).toBeInTheDocument();

    const graph = screen.getByRole("application", { name: "实体关系图" });
    const person = within(graph).getByRole("button", { name: "人物：秦彻" });
    const location = within(graph).getByRole("button", {
      name: "地点：07 号检修通道",
    });
    const personLegend = within(legend).getByText("人物");
    const locationLegend = within(legend).getByText("地点");

    expect(
      within(graph).queryByRole("button", {
        name: /证据：|事件：|假设：/,
      }),
    ).not.toBeInTheDocument();

    expect(person.style.getPropertyValue("--canvas-node-accent")).toBe(
      personLegend.style.getPropertyValue("--canvas-node-accent"),
    );
    expect(location.style.getPropertyValue("--canvas-node-accent")).toBe(
      locationLegend.style.getPropertyValue("--canvas-node-accent"),
    );
    expect(person.style.getPropertyValue("--canvas-node-accent")).not.toBe(
      location.style.getPropertyValue("--canvas-node-accent"),
    );
    const personWrapper = person.closest(".react-flow__node") as HTMLElement;
    expect(personWrapper.style.width).toBe(personWrapper.style.height);
    expect(Number.parseFloat(personWrapper.style.width)).toBeGreaterThanOrEqual(36);
  });

  it("dims unrelated nodes after focusing one relationship point", () => {
    renderWorkbench();
    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));

    const graph = screen.getByRole("application", { name: "实体关系图" });
    const investigator = within(graph).getByRole("button", {
      name: "人物：秦彻",
    });
    const relatedPerson = within(graph).getByRole("button", {
      name: "人物：唐默",
    });
    const unrelatedLocation = within(graph).getByRole("button", {
      name: "地点：07 号检修通道",
    });

    fireEvent.click(investigator);

    expect(investigator).toHaveAttribute("data-active", "true");
    expect(investigator).toHaveAttribute("data-dimmed", "false");
    expect(relatedPerson).toHaveAttribute("data-related", "true");
    expect(relatedPerson).toHaveAttribute("data-dimmed", "false");
    expect(unrelatedLocation).toHaveAttribute("data-dimmed", "true");

    fireEvent.click(investigator);
    expect(unrelatedLocation).toHaveAttribute("data-dimmed", "false");
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
      '[aria-label="实体关系图"]',
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

  it("opens compile directly and removes export preview from navigation and commands", () => {
    renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: "编译作品" }));
    expect(screen.getByRole("heading", { name: "选择作品形式" })).toBeInTheDocument();
    expect(screen.queryByText("导出预览")).not.toBeInTheDocument();
    expect(screen.queryByText("发布门禁")).not.toBeInTheDocument();
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    expect(screen.getByRole("dialog", { name: "定位对象、视图或问题" })).toBeInTheDocument();
    expect(screen.queryByText("导出预览")).not.toBeInTheDocument();
  });

  it("uses the bottom composer as the primary Agent entry and continues in the desk", async () => {
    renderWorkbench();
    fireEvent.click(
      screen.getByRole("tab", {
        name: /^工作台$/u,
      }),
    );

    const dock = screen.getByRole("region", { name: "CaseFile Agent 聊天框" });
    const homeHeading = screen.getByRole("heading", {
      name: "从故事未解之处继续",
    });
    const homeAction = screen.getByRole("button", { name: /继续上次分析/ });
    expect(dock.querySelector('[data-surface="dock"]')).toBeInTheDocument();
    expect(within(dock).getByTestId("agent-mascot")).toHaveAttribute(
      "src",
      expect.stringContaining("casefile-agent-mascot-3d.png"),
    );
    expect(
      screen.queryByRole("button", { name: "打开卷宗统筹 Agent 对话" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "当前模式导航" })).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "对象上下文" })).toBeInTheDocument();

    fireEvent.change(
      within(dock).getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
      { target: { value: "检查当前卷宗。" } },
    );
    fireEvent.click(within(dock).getByRole("button", { name: "发送" }));
    expect(
      screen
        .getByRole("region", { name: "卷宗统筹 Agent 对话" })
        .closest('[data-surface="center"]'),
    ).toBeInTheDocument();
    const conversationCanvas = document.querySelector(
      '[data-conversation-active="true"][data-mode="workbench"]',
    );
    expect(conversationCanvas).toBeInTheDocument();
    expect(homeHeading.closest("[hidden]")).toBeInTheDocument();
    expect(homeAction.closest("[hidden]")).toBeInTheDocument();
    expect(screen.getByTestId("agent-mascot")).toBeInTheDocument();
    expect(screen.queryByLabelText("统筹指令")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("当前上下文")).not.toBeInTheDocument();
    expect(screen.getByText("Enter 发送 · Shift+Enter 换行")).toBeInTheDocument();
    expect(
      await screen.findByText(/已收到：检查当前卷宗。/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: /^分析$/u }),
    );
    expect(
      screen.getByRole("tab", { name: /^分析$/u }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByRole("region", { name: "卷宗统筹 Agent 对话" }).closest('[data-surface="side"]'),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", {
        name: /^工作台$/u,
      }),
    );
    expect(
      screen
        .getByRole("region", { name: "卷宗统筹 Agent 对话" })
        .closest('[data-surface="center"]'),
    ).toBeInTheDocument();
    expect(screen.getByText(/已收到：检查当前卷宗。/)).toBeInTheDocument();

    fireEvent.change(
      screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" }),
      { target: { value: "请做一次全卷宗体检。" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
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

    fireEvent.click(screen.getByRole("button", { name: "收起 Agent 对话" }));
    expect(
      screen.queryByRole("region", { name: "卷宗统筹 Agent 对话" }),
    ).not.toBeInTheDocument();
    expect(
      screen
        .getByRole("region", { name: "CaseFile Agent 聊天框" })
        .querySelector('[data-surface="dock"]'),
    ).toBeInTheDocument();
    expect(homeHeading.closest("[hidden]")).not.toBeInTheDocument();
    expect(homeAction.closest("[hidden]")).not.toBeInTheDocument();
    expect(screen.getByTestId("agent-mascot")).toBeInTheDocument();
  });

  it("focuses the primary dock composer with Ctrl+Shift+K", () => {
    renderWorkbench();

    const input = screen.getByRole("textbox", { name: "给卷宗统筹 Agent 的指令" });
    input.blur();
    fireEvent.keyDown(window, { ctrlKey: true, key: "K", shiftKey: true });

    expect(input).toHaveFocus();
    expect(screen.getByRole("tab", { name: /时间线/ })).toBeInTheDocument();
  });

  it("resizes the inspector by dragging the split handle", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("button", { name: "展开对象上下文" }));
    screen
      .getAllByRole("button", { name: "收起对象上下文" })
      .forEach((button) => {
        expect(
          button.querySelector('svg[data-icon="panel-collapse-right"]'),
        ).toBeInTheDocument();
      });

    const handle = container.querySelector(
      '[data-testid="inspector-resize-handle"]',
    ) as HTMLElement;
    const body = handle.parentElement as HTMLElement;
    expect(body.style.getPropertyValue("--inspector-width")).toBe("344px");

    // 检查器在右侧：向右拖 80px → 宽度减小
    fireEvent.pointerDown(handle, { clientX: 200, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 280, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });

    expect(body.style.getPropertyValue("--inspector-width")).toBe("300px");

    fireEvent.pointerDown(handle, { clientX: 200, pointerId: 2 });
    fireEvent.pointerMove(handle, { clientX: -200, pointerId: 2 });
    fireEvent.pointerUp(handle, { pointerId: 2 });

    expect(body.style.getPropertyValue("--inspector-width")).toBe("700px");

    fireEvent.pointerDown(handle, { clientX: 200, pointerId: 3 });
    fireEvent.pointerMove(handle, { clientX: -400, pointerId: 3 });
    fireEvent.pointerUp(handle, { pointerId: 3 });

    expect(body.style.getPropertyValue("--inspector-width")).toBe("840px");
  });

  it("collapses and restores the context inspector", () => {
    const { container } = renderWorkbench();
    const body = container.querySelector(
      '[data-inspector-open="false"]',
    ) as HTMLElement;

    fireEvent.click(
      screen.getByRole("button", { name: "展开对象上下文" }),
    );
    expect(body).toHaveAttribute("data-inspector-open", "true");
    expect(body.style.getPropertyValue("--inspector-width")).toBe("344px");

    fireEvent.click(
      within(screen.getByRole("complementary", { name: "对象上下文" })).getByRole(
        "button",
        { name: "收起对象上下文" },
      ),
    );
    expect(body).toHaveAttribute("data-inspector-open", "false");
    expect(body.style.getPropertyValue("--inspector-width")).toBe("0px");
  });

  it("uses dedicated mirrored icons to collapse and restore mode navigation", () => {
    const { container } = renderWorkbench();
    const body = container.querySelector(
      '[data-navigator-open="true"]',
    ) as HTMLElement;
    const rail = screen.getByRole("complementary", {
      name: "当前模式导航",
    });
    const collapseButton = within(rail).getByRole("button", {
      name: "收起当前模式导航",
    });

    expect(
      collapseButton.querySelector('svg[data-icon="panel-collapse-left"]'),
    ).toBeInTheDocument();
    fireEvent.click(collapseButton);

    expect(body).toHaveAttribute("data-navigator-open", "false");
    const expandButton = screen.getByRole("button", {
      name: "展开当前模式导航",
    });
    expect(
      expandButton.querySelector('svg[data-icon="panel-expand-right"]'),
    ).toBeInTheDocument();
  });

  it("resizes the object rail by dragging the split handle", () => {
    const { container } = renderWorkbench();

    const handle = container.querySelector(
      '[data-testid="rail-resize-handle"]',
    ) as HTMLElement;
    const body = handle.parentElement as HTMLElement;
    expect(body.style.getPropertyValue("--rail-width")).toBe("224px");

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 220, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });

    expect(body.style.getPropertyValue("--rail-width")).toBe("344px");

    fireEvent.pointerDown(handle, { clientX: 100, pointerId: 2 });
    fireEvent.pointerMove(handle, { clientX: 500, pointerId: 2 });
    fireEvent.pointerUp(handle, { pointerId: 2 });

    expect(body.style.getPropertyValue("--rail-width")).toBe("640px");
  });

  it("enables relation-node dragging only while the select tool is active", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /关系图/ }));
    const board = container.querySelector(
      '[aria-label="实体关系图"]',
    ) as HTMLElement;
    const node = within(board).getByRole("button", {
      name: /唐默/,
    });
    const wrapper = node.closest(".react-flow__node") as HTMLElement;

    expect(wrapper).toHaveClass("draggable");
    fireEvent.click(screen.getByRole("button", { name: "平移工具" }));
    expect(wrapper).not.toHaveClass("draggable");
  });

  it("keeps reasoning nodes and edges read-only at the domain level", () => {
    const { container } = renderWorkbench();

    fireEvent.click(screen.getByRole("tab", { name: /推理分析/ }));
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

  it("keeps the reasoning process graph as a canvas shell even without reasoning data", () => {
    render(
      <ReasoningGraphView
        layoutScope="empty-reasoning"
        onSelectObject={() => undefined}
        seed={{
          ...defaultWorkbenchSeed,
          reasoningPaths: [],
          reasoningGroups: [],
          conclusions: [],
          graphNodes: [],
          graphEdges: [],
        }}
        selectedObjectId={null}
      />,
    );

    expect(screen.getByLabelText("推理画布")).toBeInTheDocument();
    expect(
      screen.getByText("当前工作稿还没有可展示的推理内容。"),
    ).toBeInTheDocument();
  });

  it("moves the object context back and forward across directory selections", () => {
    renderWorkbench();
    fireEvent.click(
      screen.getByRole("tab", { name: /^工作台$/ }),
    );
    const directory = () =>
      screen.getByRole("region", { name: "对象目录结果" });
    const entityKind = within(directory()).getByRole("button", {
      name: /实体，/,
    });
    if (entityKind.getAttribute("aria-expanded") !== "true") {
      fireEvent.click(entityKind);
    }
    fireEvent.click(
      within(screen.getByLabelText("实体子类型筛选")).getByRole("button", {
        name: /全部实体，/,
      }),
    );
    fireEvent.click(within(directory()).getByRole("button", { name: /林岚/ }));
    expect(within(directory()).getByRole("button", { name: /林岚/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(directory()).getByRole("button", { name: /秦彻/ })).toHaveAttribute("aria-pressed", "false");
    const context = screen.getByRole("region", {
      name: "对象上下文（本地样例）",
    });
    const heading = () => within(context).getByRole("heading", { level: 2 });
    const initialTitle = heading().textContent ?? "";
    const backButton = screen.getByRole("button", {
      name: "后退到上一个对象",
    });
    const forwardButton = screen.getByRole("button", {
      name: "前进到下一个对象",
    });

    expect(forwardButton).toBeDisabled();

    fireEvent.click(
      within(directory()).getByRole("button", { name: /秦彻/ }),
    );
    const firstTitle = heading().textContent;
    expect(firstTitle).not.toBe(initialTitle);
    expect(within(directory()).getByRole("button", { name: /秦彻/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(directory()).getByRole("button", { name: /林岚/ })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(within(directory()).getByRole("button", { name: /信息，/ }));
    fireEvent.click(
      within(screen.getByLabelText("信息子类型筛选")).getByRole("button", {
        name: /全部信息，/,
      }),
    );
    fireEvent.click(
      within(directory()).getByRole("button", { name: /07 号门禁记录/ }),
    );
    const secondTitle = heading().textContent;
    expect(secondTitle).not.toBe(firstTitle);

    fireEvent.click(backButton);
    expect(heading()).toHaveTextContent(firstTitle as string);

    fireEvent.click(backButton);
    expect(heading()).toHaveTextContent(initialTitle);

    fireEvent.click(forwardButton);
    expect(heading()).toHaveTextContent(firstTitle as string);

    fireEvent.click(forwardButton);
    expect(heading()).toHaveTextContent(secondTitle as string);
    expect(forwardButton).toBeDisabled();
  });

  it("keeps local-sample related events in the compact summary", () => {
    renderWorkbench();
    const inspector = screen.getByRole("complementary", { name: "对象上下文" });
    const keyRelations = within(inspector).getByRole("region", { name: "关键关联" });
    expect(within(keyRelations).getAllByRole("button").length).toBeGreaterThan(0);
    expect(
      within(inspector).queryByRole("tablist", { name: "对象上下文视图" }),
    ).not.toBeInTheDocument();
  });

  it("switches the complete workbench seed and keeps candidate navigation in the title row", () => {
    const { container } = renderWorkbench(<CandidateSeedHarness />);

    expect(
      screen.queryByRole("region", { name: "工作稿接力状态" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "载入结构候选" }));

    expect(container.querySelector('[data-workbench-seed="brief-1-structure"]')).toBeTruthy();
    expect(screen.getAllByText("缺页校准案").length).toBeGreaterThan(0);
    expect(screen.getByText("封存前 39 分钟的校准链")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /来源抽屉/ })).not.toBeInTheDocument();
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
