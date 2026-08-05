import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DemoPrototypeProvider } from "@/features/demo-prototype/demo-prototype-provider";
import { IntakeCenterPrototype } from "@/features/intake-prototype/intake-center-prototype";

const { routerPush } = vi.hoisted(() => ({ routerPush: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

function renderPrototype() {
  return render(
    <DemoPrototypeProvider>
      <IntakeCenterPrototype />
    </DemoPrototypeProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  routerPush.mockReset();
});

describe("intake center prototype", () => {
  it("keeps the real A-path functions in an isolated visual prototype", () => {
    renderPrototype();

    expect(
      screen.getByRole("heading", {
        name: "把念头照亮，留下可追溯的起案依据。",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /我有一个想法/u })).toBeEnabled();
    expect(screen.getByRole("button", { name: /帮我想一个/u })).toBeDisabled();
    expect(
      screen.getByRole("radio", { name: /表达优化/u }),
    ).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /叙事增强/u }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("写下最初想法")).toHaveValue("");
    expect(screen.getByText("实时简报映射")).toBeInTheDocument();
  });

  it("runs from source and polish review through brief review and three draft candidates", () => {
    vi.useFakeTimers();
    renderPrototype();

    fireEvent.click(screen.getByRole("button", { name: "载入示例" }));
    fireEvent.click(screen.getByRole("radio", { name: /叙事增强/u }));
    fireEvent.click(
      screen.getByRole("button", { name: /生成润色校样/u }),
    );

    expect(
      screen.getByRole("heading", { name: "逐字确认 Agent 改了什么。" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("当前作者原稿")).toHaveAttribute("readonly");
    expect(
      (screen.getByLabelText("编辑 Agent 润色工作稿") as HTMLTextAreaElement)
        .value,
    ).toContain("深夜");
    expect(screen.getByText("新增细节审阅")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /采用这版校样/u }));
    fireEvent.click(screen.getByRole("button", { name: /继续关键追问/u }));

    expect(
      screen.getByRole("heading", { name: "只问会改变方向的问题。" }),
    ).toBeInTheDocument();
    const generateBrief = screen.getByRole("button", {
      name: /形成创作简报/u,
    });
    expect(generateBrief).toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", {
        name: "找出是谁伪造了那段不存在的时间。",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "稍后决定" }));
    expect(generateBrief).toBeEnabled();
    fireEvent.click(generateBrief);

    expect(
      screen.getByRole("heading", {
        name: "确认整体方向，再交给正式审阅。",
      }),
    ).toBeInTheDocument();
    expect(
      (screen.getByLabelText("一句话概念") as HTMLTextAreaElement).value,
    ).toContain("档案修复师");
    expect(screen.getByLabelText("推理目标")).toHaveValue(
      "找出是谁伪造了那段不存在的时间。",
    );
    expect(screen.getByText("约束抽屉")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /进入创作简报审阅/u }),
    );

    expect(
      screen.getByRole("heading", { name: "把生成依据逐条钉在纸面上。" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /确认并冻结/u }),
    ).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "保存审阅" }));
    const freeze = screen.getByRole("button", { name: /确认并冻结/u });
    expect(freeze).toBeEnabled();
    fireEvent.click(freeze);

    expect(
      screen.getByRole("heading", { name: "让三种创作策略同时摊开。" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /生成三份候选/u }));
    expect(screen.getByText("正在形成候选…")).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_100));

    expect(screen.getByRole("button", { name: /缺页校准稿/u })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /封存室夜班稿/u })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /第七码互证稿/u })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /缺页校准稿/u }));
    fireEvent.click(screen.getByRole("button", { name: "预览工作台" }));
    expect(routerPush).toHaveBeenCalledWith("/demo");
    fireEvent.click(
      screen.getByRole("button", { name: /采用为当前工作稿/u }),
    );
    expect(
      screen.getByRole("button", { name: /已是当前工作稿/u }),
    ).toBeInTheDocument();
  });

  it("keeps the prototype out of the real workflow and persistence layers", () => {
    const feature = readFileSync(
      resolve(
        process.cwd(),
        "features/intake-prototype/intake-center-prototype.tsx",
      ),
      "utf8",
    );
    const model = readFileSync(
      resolve(
        process.cwd(),
        "features/intake-prototype/intake-prototype-model.ts",
      ),
      "utf8",
    );
    const route = readFileSync(
      resolve(process.cwd(), "app/demo/intake/page.tsx"),
      "utf8",
    );
    const shell = readFileSync(
      resolve(process.cwd(), "components/demo-archive-shell.tsx"),
      "utf8",
    );
    const globalCss = readFileSync(
      resolve(process.cwd(), "app/globals.css"),
      "utf8",
    );

    const provider = readFileSync(
      resolve(
        process.cwd(),
        "features/demo-prototype/demo-prototype-provider.tsx",
      ),
      "utf8",
    );
    const fixture = readFileSync(
      resolve(
        process.cwd(),
        "features/analyst-workbench/analyst-fixture.ts",
      ),
      "utf8",
    );

    [feature, model, route, provider, fixture].forEach((source) => {
      expect(source).not.toContain("@/lib/api-client");
      expect(source).not.toContain("@/store/workflow-store");
      expect(source).not.toContain("localStorage");
      expect(source).not.toContain("sessionStorage");
      expect(source).not.toContain("apiRequest");
      expect(source).not.toMatch(/\bfetch\s*\(/u);
    });
    expect(route).toContain("@/features/intake-prototype/intake-center-prototype");
    expect(shell).toContain('"intake-center-v1"');
    expect(shell).toContain("<DemoPrototypeProvider>");
    expect(globalCss).toContain(
      'html:has([data-demo-kind="intake-center-v1"])',
    );
  });
});
