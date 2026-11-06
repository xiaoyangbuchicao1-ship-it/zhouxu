// Browser regression against the actual Vite-served component and styles.
// Requires a running dev server and Playwright (an external module path is optional).
import { parseArgs } from "node:util";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";

const { values } = parseArgs({ options: { url: { type: "string" }, playwright: { type: "string" } } });
assert.ok(values.url, "Provide --url for the running frontend dev server");
const { chromium } = await import(values.playwright ? pathToFileURL(resolve(values.playwright)).href : "playwright");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1000, height: 600 } });
const page = await context.newPage();

try {
  await page.goto(new URL("/login", values.url).href);
  await page.evaluate(async () => {
    const React = await import("/node_modules/.vite/deps/react.js");
    const DOM = await import("/node_modules/.vite/deps/react-dom_client.js");
    const { RunProgress } = await import("/src/components/workspace/RunProgress.tsx");
    await import("/src/pages/workspace.css");
    const host = document.createElement("div");
    host.className = "workspace";
    host.style.cssText = "position:fixed;inset:0;background:#080d12;padding:80px;display:block;z-index:9999";
    document.body.append(host);
    const root = (DOM.default || DOM).createRoot(host);
    window.renderProgressProbe = live => root.render((React.default || React).createElement(RunProgress, {
      live, activities: [{ id: "probe", label: "正在生成回答", stage: "reasoning", status: live ? "running" : "completed" }],
    }));
    window.renderProgressProbe(true);
  });
  const icon = page.locator(".ws-progress-current .ws-activity-indicator");
  await icon.waitFor();
  for (const mode of [null, "no-preference", "reduce"]) {
    await page.emulateMedia({ reducedMotion: mode });
    await page.waitForTimeout(350);
    const inspect = () => icon.evaluate(el => ({
      reduced: matchMedia("(prefers-reduced-motion: reduce)").matches,
      transform: getComputedStyle(el).transform,
      dots: [...el.children].map(dot => ({
        opacity: getComputedStyle(dot).opacity,
        duration: getComputedStyle(dot).animationDuration,
        iterations: getComputedStyle(dot).animationIterationCount,
      })),
    }));
    const box = await icon.boundingBox();
    const clip = { x: Math.floor(box.x) - 3, y: Math.floor(box.y) - 3, width: Math.ceil(box.width) + 6, height: Math.ceil(box.height) + 6 };
    const before = await inspect();
    const a = await page.screenshot({ clip });
    await page.waitForTimeout(230);
    const after = await inspect();
    const b = await page.screenshot({ clip });
    assert.ok(!a.equals(b), `${mode || "platform-default"}: activity indicator pixels remained static`);
    if (after.reduced) {
      assert.equal(after.transform, "none", "Reduced mode must avoid rotation");
      assert.ok(after.dots.some((dot, i) => dot.opacity !== before.dots[i].opacity), "Reduced-mode feedback must remain visibly active");
      assert.ok(after.dots.every(dot => dot.iterations === "infinite" && parseFloat(dot.duration) >= 1), "Global one-frame reset must not freeze essential feedback");
    } else {
      assert.notEqual(before.transform, after.transform, "Normal-mode rotation must advance");
    }
    console.log(JSON.stringify({ mode: mode || "platform-default", passed: true, before, after }));
  }
  await page.evaluate(() => window.renderProgressProbe(false));
  await icon.waitFor({ state: "detached" });
  assert.equal(await page.getByRole("status").count(), 0, "Completed tasks must stop their activity feedback");
  console.log(JSON.stringify({ case: "completed-task", passed: true }));
} finally {
  await context.close();
  await browser.close();
}
