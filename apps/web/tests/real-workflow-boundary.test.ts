import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { ApiError, errorMessage } from "@/lib/api-client";

const realWorkflowPages = [
  "intake-workspace.tsx",
  "brief-review-workspace.tsx",
  "real-workbench.tsx",
] as const;

function readWorkflowSource(fileName: string) {
  return readFileSync(resolve(process.cwd(), "features", "workflow", fileName), "utf8");
}

describe("real workflow boundary", () => {
  it.each(realWorkflowPages)(
    "keeps %s on the real API session instead of Prototype fixtures",
    (fileName) => {
      const source = readWorkflowSource(fileName);

      expect(source).toContain("@/store/workflow-store");
      expect(source).toContain("@/lib/api-client");
      expect(source).not.toContain("@/store/prototype-store");
      expect(source).not.toContain("@/lib/prototype-model");
      expect(source).not.toMatch(/["']\/demo(?:\/|["'])/);
    },
  );

  it("keeps the shared archive UI independent from both stores", () => {
    const source = readFileSync(
      resolve(process.cwd(), "components", "archive-ui.tsx"),
      "utf8",
    );

    expect(source).not.toContain("@/store/prototype-store");
    expect(source).not.toContain("@/store/workflow-store");
    expect(source).not.toContain("@/lib/prototype-model");
  });

  it("keeps the frozen frontend-template demo reachable from the real-mode block", () => {
    const source = readFileSync(
      resolve(process.cwd(), "components", "archive-shell.tsx"),
      "utf8",
    );

    expect(source).toContain('href="/demo"');
    expect(source).toContain("演示模式 ↗");
    expect(source).toContain('className="real-mode-demo-link"');
    expect(source).not.toContain("real-utility-bar");
    expect(source).not.toContain("real-utility-copy");
    expect(source).not.toContain("模型与 API</button>");
  });

  it("does not render the retired workflow progress strip on real pages", () => {
    const realWorkspaces = [
      "features/workflow/intake-workspace.tsx",
      "features/workflow/brief-review-workspace.tsx",
      "features/workflow/real-workbench.tsx",
    ];

    realWorkspaces.forEach((file) => {
      const source = readFileSync(resolve(process.cwd(), file), "utf8");
      expect(source).not.toContain("CaseSpine");
    });

    const realLayouts = [
      [
        "features/workflow/intake-workspace.module.css",
        "grid-template-rows: 88px minmax(0, 1fr);",
      ],
      [
        "features/workflow/brief-workspace.module.css",
        "grid-template-rows: auto minmax(0, 1fr) 42px;",
      ],
      [
        "features/workflow/real-workbench.module.css",
        "grid-template-rows: 88px minmax(0, 1fr);",
      ],
    ] as const;

    realLayouts.forEach(([file, expectedRows]) => {
      const source = readFileSync(resolve(process.cwd(), file), "utf8");
      expect(source).toContain(expectedRows);
    });
  });

  it("keeps the Agent composer compact without redundant shortcut copy", () => {
    const source = readFileSync(
      resolve(process.cwd(), "features/workflow/agent-workspace.tsx"),
      "utf8",
    );

    expect(source).toContain("rows={2}");
    expect(source).not.toContain(
      "Enter 发送 · Shift + Enter 换行 · 修改始终需要人工决定",
    );
    expect(source).toContain('aria-label="给 Agent 一条指令"');
    expect(source).not.toContain("<label htmlFor=\"agent-workbench-composer\">");
    expect(source).toContain('<svg aria-hidden="true" viewBox="0 0 16 16">');
    expect(source).toContain('title="发送消息"');
  });

  it("keeps intake as three service-recoverable steps", () => {
    const source = readWorkflowSource("intake-workspace.tsx");

    expect(source).toContain(
      'type IntakeStep = "idea" | "questions" | "confirmation";',
    );
    expect(source).toContain('useState<IntakeStep>("idea")');
    expect(source).toContain("<IntakeIdeaStep");
    expect(source).toContain("<IntakeQuestionsStep");
    expect(source).toContain("<IntakeConfirmationStep");
    expect(source).toContain("stageToStep");
    expect(source).toContain('brief_review: "confirmation"');
    expect(source).not.toContain('router.replace("/brief")');
    expect(source).toContain("highestReachableIndex");
    expect(source).toContain("index <= highestReachableIndex");
    expect(source).not.toContain("希望玩家推理什么");
  });

  it("aligns the four intake entries with F-101 decision guidance", () => {
    const source = readWorkflowSource("intake-workspace.tsx");
    const expectedPaths = [
      ["我有一个想法", "已有灵感，整理成简报", "输入一句描述"],
      ["帮我想一个", "没有方向，生成多个创意", "输入偏好限制"],
      ["我有已有内容", "有现成素材，解析成简报", "上传或粘贴"],
      ["我已经准备好", "方向明确，进入工作台", "选模板/空白"],
    ];

    expectedPaths.flat().forEach((copy) => expect(source).toContain(copy));
    expect(source).toContain("{path.summary}");
    expect(source).toContain("{path.inputHint}");
    expect(source).toContain('path.active ? "进行中" : "规划中"');
    expect(source).not.toContain("我有一份文稿");
    expect(source).not.toContain("我有若干线索");
    expect(source).not.toContain("我想复盘旧案");
  });

  it("keeps an adopted intake reviewable without exposing rejected Agent actions", () => {
    const workspace = readWorkflowSource("intake-workspace.tsx");
    const ideaStep = readWorkflowSource("intake-idea-step.tsx");

    expect(workspace).toContain(
      'const intakeClosed = intake?.stage === "brief_review";',
    );
    expect(workspace).toContain("sourceText={ideaSourceText}");
    expect(ideaStep).toContain("readOnly={closed}");
    expect(ideaStep).toContain("这份建案已进入正式审阅");
    expect(ideaStep).toContain("新建案件再润色");
    expect(ideaStep).toMatch(/closed \? \([\s\S]*新建案件再润色[\s\S]*\) : \(/);
    expect(ideaStep).not.toContain("readOnlySource");
  });

  it("expands Agent polish into an inline left-right comparison", () => {
    const workspace = readWorkflowSource("intake-workspace.tsx");
    const ideaStep = readWorkflowSource("intake-idea-step.tsx");
    const css = readWorkflowSource("brief-intake-workspace.module.css");

    expect(workspace).toContain("setPolishReviewOpen(true)");
    expect(workspace).toContain("setPolishDraft(polishResult.polished_text)");
    expect(ideaStep).toContain("data-comparing={comparisonOpen}");
    expect(ideaStep).toContain('aria-label="Agent 润色左右对照"');
    expect(ideaStep).toContain('aria-label="当前作者原稿"');
    expect(ideaStep).toContain('aria-label="编辑 Agent 润色工作稿"');
    expect(ideaStep).not.toContain("polishBackdrop");
    expect(ideaStep).not.toContain('role="dialog"');
    expect(css).toContain("width: min(1120px, calc(100% - 32px));");
    expect(css).toContain(
      "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);",
    );
  });

  it("keeps Agent collaboration focused on the conversation", () => {
    const agentSource = readFileSync(
      resolve(process.cwd(), "features/workflow/agent-workspace.tsx"),
      "utf8",
    );
    const workbenchSource = readFileSync(
      resolve(process.cwd(), "features/workflow/real-workbench.tsx"),
      "utf8",
    );

    expect(agentSource).not.toContain("完整卷宗上下文");
    expect(agentSource).not.toContain("新的协作记录");
    expect(agentSource).not.toContain("每次发送前读取最新 CaseFile");
    expect(agentSource).not.toContain("conversationHeader");
    expect(workbenchSource).toContain('className={styles.panelThreadToggle}');
    expect(workbenchSource).toContain("railOpen={agentThreadRailOpen}");
  });

  it("keeps the three real workbench panels adjustable and persistent", () => {
    const source = readFileSync(
      resolve(process.cwd(), "features/workflow/real-workbench.tsx"),
      "utf8",
    );

    expect(source.match(/role="separator"/g)).toHaveLength(2);
    expect(source).toContain("resizeWorkbenchPanels");
    expect(source).toContain("PANEL_WIDTH_STORAGE_KEY");
    expect(source).toContain("ResizeObserver");
    expect(source).toContain("ArrowLeft");
    expect(source).toContain("ArrowRight");
  });

  it("selects an isolated shell for every demo route", () => {
    const source = readFileSync(
      resolve(process.cwd(), "components", "app-shell.tsx"),
      "utf8",
    );
    const demoShell = readFileSync(
      resolve(process.cwd(), "components", "demo-archive-shell.tsx"),
      "utf8",
    );

    expect(source).toContain('pathname.startsWith("/demo/")');
    expect(source).toContain("<DemoArchiveShell>");
    expect(source).toContain("<ArchiveShell>");
    expect(demoShell).toContain('data-template-commit="960481d"');
    expect(demoShell).toContain("@/store/prototype-store");
    expect(demoShell).not.toContain("@/store/workflow-store");
    expect(demoShell).not.toContain("@/lib/api-client");
  });

  it.each([
    ["app/demo/page.tsx", "@/features/intake/intake-home"],
    ["app/demo/brief/page.tsx", "@/features/intake/brief-editor"],
    ["app/demo/workbench/page.tsx", "@/features/workbench/workbench-page"],
    ["app/demo/reasoning/page.tsx", "@/features/reasoning/reasoning-lab"],
    ["app/demo/quality/page.tsx", "@/features/quality/quality-workspace"],
  ])("keeps %s on the frozen Prototype template", (fileName, featureImport) => {
    const source = readFileSync(resolve(process.cwd(), fileName), "utf8");

    expect(source).toContain(featureImport);
    expect(source).not.toContain("redirect(");
    expect(source).not.toContain("@/lib/api-client");
    expect(source).not.toContain("@/store/workflow-store");
  });

  it("keeps TaskRun recovery on the real API and independent from Prototype state", () => {
    const source = readWorkflowSource("task-recovery.ts");

    expect(source).toContain("@/lib/api-client");
    expect(source).not.toContain("@/store/prototype-store");
    expect(source).not.toContain("@/lib/prototype-model");
  });

  it("keeps a failed polish alert in document flow so the retry action remains clickable", () => {
    const source = readWorkflowSource("intake-workspace.module.css");

    expect(source).toMatch(/\.formError\s*{[^}]*position:\s*relative;/s);
  });

  it("keeps the optional constraint drawer collapsed by default", () => {
    const source = readWorkflowSource("intake-confirmation-step.tsx");

    expect(source).toContain('className={styles.constraintsDrawer}');
    expect(source).toContain("必须保留、禁止出现、规模、人数、时长与内容尺度");
    expect(source).not.toContain("open={true}");
  });

  it("uses the reviewed pencil dossier artwork behind the intake sheet", () => {
    const css = readWorkflowSource("brief-intake-workspace.module.css");
    const artwork = readFileSync(
      resolve(process.cwd(), "public", "intake-pencil-dossier.svg"),
      "utf8",
    );

    expect(css).toContain('url("/intake-pencil-dossier.svg")');
    expect(css).toContain("width: min(760px, calc(100% - 120px));");
    expect(artwork).toContain("CASE NOTES");
    expect(artwork).toContain("TOP SECRET");
    expect(artwork).toContain("EVIDENCE");
  });

  it("keeps the desktop intake sheet inside its stage without a second scrollbar", () => {
    const css = readWorkflowSource("brief-intake-workspace.module.css");

    expect(css).toContain("grid-template-columns: 186px minmax(0, 1fr);");
    expect(css).toMatch(
      /@media \(max-width: 1280px\)[\s\S]*grid-template-columns: 160px minmax\(0, 1fr\);/,
    );
    expect(css).toMatch(
      /\.threadStage\s*{[^}]*display:\s*grid;[^}]*grid-template-rows:\s*minmax\(0, 1fr\);[^}]*overflow:\s*hidden;/s,
    );
    expect(css).toMatch(/\.stepSheet\s*{[^}]*min-height:\s*0;/s);
    expect(css).toMatch(
      /@media \(max-width: 680px\)[\s\S]*\.threadStage\s*{[^}]*display:\s*block;[^}]*overflow:\s*auto;/,
    );
  });

  it("opens shared settings from Agent fallback states", () => {
    const shell = readFileSync(
      resolve(process.cwd(), "components", "archive-shell.tsx"),
      "utf8",
    );
    const intake = readWorkflowSource("intake-workspace.tsx");

    expect(shell).toContain('addEventListener("casefile:open-settings"');
    expect(shell).toContain('removeEventListener("casefile:open-settings"');
    expect(intake).toContain(
      'window.dispatchEvent(new Event("casefile:open-settings"))',
    );
  });

  it("offers retry, settings, and an artificial-free manual path after synthesis fails", () => {
    const source = readWorkflowSource("intake-questions-step.tsx");

    expect(source).toContain('synthesizeTask?.status === "failed"');
    expect(source).toContain("重试生成简报");
    expect(source).toContain("检查设置");
    expect(source).toContain("人工整理");
    expect(source).toContain("onManualContinue");
  });

  it("shows intake pending decisions as a read-only queue in formal review", () => {
    const review = readWorkflowSource("brief-review-workspace.tsx");
    const queue = readWorkflowSource("brief-pending-decisions.tsx");

    expect(review).toContain("/brief-intake");
    expect(review).toContain("<BriefPendingDecisions");
    expect(queue).toContain('aria-label="待决定事项（只读）"');
    expect(queue).toContain("这些事项不会阻止正式审阅");
  });

  it("keeps implementation identifiers and infrastructure terms out of formal review copy", () => {
    const review = readWorkflowSource("brief-review-workspace.tsx");
    const candidates = readWorkflowSource("draft-candidate-panel.tsx");

    expect(review).not.toContain('value: `项目-${workflow.projectId}`');
    expect(review).not.toContain("PostgreSQL / 任务运行 / SSE");
    expect(review).not.toContain("任务编号");
    expect(review).not.toContain("Brief 版本");
    expect(review).not.toContain("旧 Snapshot");
    expect(candidates).not.toContain("Brief v");
    expect(candidates).not.toContain("候选 #");
  });

  it("derives the target-neutral Core collection count from the live index", () => {
    const source = readWorkflowSource("real-workbench.tsx");

    expect(source).toContain(
      "code={`${collections.length} 组集合 · ${totalObjects} 个对象`}",
    );
    expect(source).not.toContain('code="12 COLLECTIONS"');
    expect(source).not.toContain('["phases"');
  });

  it("keeps the real-mode interface Chinese-first", () => {
    const source = [
      readFileSync(resolve(process.cwd(), "components", "archive-shell.tsx"), "utf8"),
      readFileSync(resolve(process.cwd(), "components", "archive-ui.tsx"), "utf8"),
      ...[
        "intake-workspace.tsx",
        "intake-idea-step.tsx",
        "intake-questions-step.tsx",
        "intake-confirmation-step.tsx",
        "brief-review-workspace.tsx",
        "real-workbench.tsx",
        "settings-dialog.tsx",
      ].map(readWorkflowSource),
    ].join("\n");
    const retiredEnglishLabels = [
      "07 MODULES",
      "REAL MODE",
      "CASE OPENING",
      "BRIEF REVIEW",
      "DRAFT DESK",
      "USER SETTINGS / LOCAL",
      "CORE BRIEF / TARGET AGNOSTIC",
      "CREATIVE INTENT",
      "REASONING PROPOSITION",
      "RESOLUTION MODE",
      "AUTHOR ANSWER",
      "BOUNDARY TEXT",
      "TASKRUN / POSTGRESQL",
      "NO PROVIDER",
      "AGENT RUNNING",
      "OBJECT / INSPECTOR",
      "READ ONLY",
      "REVISION GUARD",
    ];

    for (const label of retiredEnglishLabels) {
      expect(source).not.toContain(label);
    }
    expect(source).toContain("创作简报审阅");
    expect(source).not.toContain("<small>{module.code}</small>");
    expect(source).toContain('"model.started": "模型开始处理"');
    expect(source).toContain("Agent 润色");
    expect(source).toContain("事实时间线");
    expect(source).not.toContain("版本保护");
  });

  it.each([
    ["request_invalid", "提交内容不符合接口要求，请检查后重试。"],
    ["draft_revision_conflict", "草稿已被更新，请刷新后重新提交。"],
    ["database_unavailable", "数据库暂时不可用，请稍后重试。"],
  ])("localizes the stable API error %s", (code, expected) => {
    const error = new ApiError(400, { code, message: "English fallback", details: {} });

    expect(errorMessage(error)).toBe(expected);
  });
});
