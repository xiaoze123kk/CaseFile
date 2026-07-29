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
