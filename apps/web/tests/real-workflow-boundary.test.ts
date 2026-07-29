import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

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

  it("keeps the frozen frontend-template demo reachable from the global toolbar", () => {
    const source = readFileSync(
      resolve(process.cwd(), "components", "archive-shell.tsx"),
      "utf8",
    );

    expect(source).toContain('href="/demo"');
    expect(source).toContain("前端模板 · 演示模式");
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

    expect(source).toContain("code={`${collections.length} COLLECTIONS`}");
    expect(source).not.toContain('code="12 COLLECTIONS"');
    expect(source).not.toContain('["phases"');
  });
});
