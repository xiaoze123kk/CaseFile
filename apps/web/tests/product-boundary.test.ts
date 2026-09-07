import { cleanup, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import VisualIntakePage from "@/app/visual-intake/page";
import nextConfig from "../next.config";

vi.mock("@/features/intake/intake-center", () => ({
  IntakeCenter: () => createElement("main", null, "intake route"),
}));
vi.mock("@/features/intake/visual-intake-demo", () => ({
  VisualIntakeDemo: () => createElement("main", null, "visual intake route"),
}));

afterEach(cleanup);

describe("product routes", () => {
  it("serves the intake at the root", () => {
    render(createElement(HomePage));
    expect(screen.getByRole("main")).toHaveTextContent("intake route");
  });

  it("serves the visual experiment on its own route", () => {
    render(createElement(VisualIntakePage));
    expect(screen.getByRole("main")).toHaveTextContent("visual intake route");
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

});
