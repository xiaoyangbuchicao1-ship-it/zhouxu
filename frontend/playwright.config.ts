import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 60000,
  expect: { timeout: 15000 },
  outputDir: "../.ai-tmp/maritime-platform-20261105/playwright-results",
  use: {
    baseURL: process.env.ZHOUXU_E2E_URL || "http://127.0.0.1:4185",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "off",
  },
});
