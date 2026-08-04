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
        "grid-template-rows: 88px minmax(0, 1fr) 44px;",
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

  it("persists the optional boundary disclosure state while TaskRun polling rerenders", () => {
    const source = readWorkflowSource("intake-workspace.tsx");

    expect(source).toContain("const [boundaryOpen, setBoundaryOpen]");
    expect(source).toContain("setBoundaryOpen(event.currentTarget.open)");
    expect(source).toContain(
      "open={boundaryOpen || Boolean(brief.boundary_text)}",
    );
  });

  it("derives the target-neutral Core collection count from the live index", () => {
    const source = readWorkflowSource("real-workbench.tsx");

    expect(source).toContain("code={`${collections.length} 组集合`}");
    expect(source).not.toContain('code="12 COLLECTIONS"');
    expect(source).not.toContain('["phases"');
  });

  it("keeps the real-mode interface Chinese-first", () => {
    const source = [
      readFileSync(resolve(process.cwd(), "components", "archive-shell.tsx"), "utf8"),
      readFileSync(resolve(process.cwd(), "components", "archive-ui.tsx"), "utf8"),
      ...[
        "intake-workspace.tsx",
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
