import { resolve } from "node:path";

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: resolve(process.cwd(), "../../var/playwright/a-path"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: "line",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.CASEFILE_E2E_BASE_URL ?? "http://127.0.0.1:3000",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
});
