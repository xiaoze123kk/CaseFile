import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import nextConfig from "../next.config";

function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

describe("official two-page product boundary", () => {
  it("serves intake from the root and the analyst workbench from /workbench", () => {
    expect(source("app/page.tsx")).toContain(
      '@/features/intake/intake-center',
    );
    expect(source("app/workbench/page.tsx")).toContain(
      '@/features/analyst-workbench/analyst-workbench',
    );
  });

  it("keeps old addresses as non-permanent redirects", async () => {
    expect(await nextConfig.redirects?.()).toEqual([
      { source: "/demo/intake", destination: "/", permanent: false },
      { source: "/demo", destination: "/workbench", permanent: false },
      { source: "/demo/:path*", destination: "/workbench", permanent: false },
      { source: "/brief", destination: "/", permanent: false },
      { source: "/reasoning", destination: "/workbench", permanent: false },
      { source: "/quality", destination: "/workbench", permanent: false },
    ]);
  });

  it("removes the old creation UI instead of hiding it behind the new routes", () => {
    [
      "app/brief/page.tsx",
      "app/demo/page.tsx",
      "app/reasoning/page.tsx",
      "app/quality/page.tsx",
      "components/archive-shell.tsx",
      "components/archive-ui.tsx",
      "features/workflow/intake-workspace.tsx",
      "features/workflow/brief-review-workspace.tsx",
      "features/workflow/real-workbench.tsx",
      "store/workflow-store.tsx",
    ].forEach((path) => expect(existsSync(resolve(process.cwd(), path))).toBe(false));
  });

  it("uses one in-memory product session without browser storage", () => {
    const shell = source("components/product-shell.tsx");
    const session = source("features/case-session/case-session-provider.tsx");
    expect(shell).toContain("<CaseSessionProvider>");
    expect(shell).not.toContain("workflow-store");
    expect(`${shell}\n${session}`).not.toContain("localStorage");
    expect(`${shell}\n${session}`).not.toContain("sessionStorage");
  });

  it("keeps the visual intake demo on an isolated local-fixture route", () => {
    const route = source("app/visual-intake/page.tsx");
    const demo = source("features/intake/visual-intake-demo.tsx");
    expect(route).toContain('@/features/intake/visual-intake-demo');
    expect(demo).not.toContain("@/lib/api-client");
    expect(demo).not.toContain("CaseSession");
    expect(demo).not.toContain("localStorage");
    expect(demo).not.toContain("sessionStorage");
    expect(demo).not.toMatch(/\bfetch\s*\(/u);
  });

  // Runtime loading/adoption is covered by case-session and production-workbench tests.
  it("keeps intake views behind their session adapter", () => {
    for (const path of ["features/intake/intake-center.tsx", "features/intake/intake-model.ts", "app/page.tsx"]) {
      const content = source(path);
      expect(content).not.toContain("@/lib/api-client");
      expect(content).not.toContain("@/store/workflow-store");
      expect(content).not.toContain("localStorage");
      expect(content).not.toContain("sessionStorage");
      expect(content).not.toMatch(/\bfetch\s*\(/u);
    }
  });

  it("keeps provider settings independent from the retired workflow store", () => {
    const settings = source("features/settings/settings-dialog.tsx");
    expect(settings).not.toContain("useWorkflowSession");
    expect(settings).not.toContain("workflow-store");
  });

  it("removes prototype naming from active product modules", () => {
    const active = [
      source("components/product-shell.tsx"),
      source("features/case-session/case-session-provider.tsx"),
      source("features/case-session/case-session-api.ts"),
      source("features/case-session/case-session-mapping.ts"),
      source("features/intake/intake-center.tsx"),
      source("features/intake/intake-model.ts"),
      source("features/analyst-workbench/analyst-workbench.tsx"),
      source("features/analyst-workbench/workbench-agent-panel.tsx"),
      source("features/analyst-workbench/workbench-canvas-controls.tsx"),
      source("features/analyst-workbench/workbench-reasoning-graph.tsx"),
      source("features/analyst-workbench/workbench-relationship-graph.tsx"),
      source("features/analyst-workbench/workbench-secondary-views.tsx"),
    ].join("\n");
    expect(active).not.toMatch(/demo-prototype|intake-prototype|DemoPrototype|Prototype[A-Z]/);
    expect(active).not.toMatch(/["']\/demo(?:\/|["'])/);
  });
});
