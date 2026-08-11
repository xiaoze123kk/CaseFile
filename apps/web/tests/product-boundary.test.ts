import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

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
    expect(source("features/intake/intake-center.tsx")).toContain(
      '`/workbench?project=${activeProjectId}`',
    );
    expect(source("features/analyst-workbench/analyst-workbench.tsx")).toContain(
      'href="/"',
    );
  });

  it("keeps old addresses as non-permanent configuration redirects only", () => {
    const config = source("next.config.ts");
    [
      'source: "/demo/intake"',
      'source: "/demo"',
      'source: "/demo/:path*"',
      'source: "/brief"',
      'source: "/reasoning"',
      'source: "/quality"',
    ].forEach((entry) => expect(config).toContain(entry));
    expect(config).toContain('destination: "/"');
    expect(config).toContain('destination: "/workbench"');
    expect(config.match(/permanent: false/g)).toHaveLength(6);
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
    expect(shell).toContain("data-casefile-kind");
    expect(shell).toContain("data-casefile-visual");
    expect(shell).not.toContain("workflow-store");
    expect(`${shell}\n${session}`).not.toContain("localStorage");
    expect(`${shell}\n${session}`).not.toContain("sessionStorage");
  });

  it("keeps the real intake backend and candidate adoption boundary", () => {
    const api = source("features/case-session/case-session-api.ts");
    const session = source("features/case-session/case-session-provider.tsx");
    const mapping = source("features/case-session/case-session-mapping.ts");
    [
      "/brief-intake",
      "/tasks/brief-polish",
      "/tasks/brief-intake-questions",
      "/tasks/brief-intake-synthesize",
      "/tasks/brief-anchor-extract",
      "/tasks/generate",
      "/draft-candidates",
    ].forEach((route) => expect(api).toContain(route));
    expect(session).toContain("mapCurrentBriefDraftCandidates");
    expect(mapping).toContain("buildWorkbenchCandidates");
    expect(session).toContain("adoptDraftCandidate");
  });

  it("loads the production workbench from Current Draft and isolates fixtures", () => {
    const workbench = source("features/analyst-workbench/analyst-workbench.tsx");
    const fixture = source("features/analyst-workbench/analyst-fixture.ts");
    const mapper = source("features/analyst-workbench/workbench-real-data.ts");
    const apiClient = source("lib/api-client.ts");
    expect(workbench).toContain("fetchCaseDraft(projectId)");
    expect(workbench).toContain("mapCaseFileToWorkbenchModel");
    expect(workbench).toContain("requestedProjectId");
    expect(mapper).toContain("mapFixtureToWorkbenchModel");
    expect(apiClient).toContain("content: CaseFileDocument | null");
    expect(fixture).toContain("buildWorkbenchCandidates");
    expect(workbench).not.toContain("@/store/workflow-store");
  });

  it("preserves the two reviewed visual systems and responsive workbench", () => {
    const workbenchCss = source(
      "features/analyst-workbench/analyst-workbench.module.css",
    );
    const intakeCss = source("features/intake/intake-center.module.css");
    expect(workbenchCss).toContain("@media (max-width: 780px)");
    expect(workbenchCss).toContain("--primary: #c78b3c");
    expect(workbenchCss).toContain("grid-row: 3;");
    expect(workbenchCss).toContain("grid-row: 4;");
    expect(intakeCss).toContain('url("/intake-pencil-dossier.svg")');
    expect(source("app/globals.css")).toContain("min-width: 0");
  });

  it("keeps provider settings independent from the retired workflow store", () => {
    const settings = source("features/settings/settings-dialog.tsx");
    expect(settings).toContain("actorId: number");
    expect(settings).toContain("setProviderName");
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
