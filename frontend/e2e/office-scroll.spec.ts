import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

for (const viewport of [{ width: 1366, height: 620 }, { width: 390, height: 650 }]) {
  test(`skill workshop scrolls to its last action at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/register');
    await page.getByLabel('邮箱地址').fill(`qa-scroll-${randomUUID()}@example.test`);
    await page.getByLabel('密码', { exact: true }).fill(randomUUID() + '!');
    await page.getByRole('button', { name: '创建账户', exact: true }).click();
    await expect(page).toHaveURL(/\/workspace/);
    await page.goto('/workspace/skills');
    await expect(page.locator('.md-skill')).toHaveCount(8);
    const lastAction = page.locator('.md-skill').last().getByRole('button', { name: '开始任务' });
    await expect(lastAction).not.toBeInViewport();
    await page.locator('.maritime-desk').hover({ position: { x: 100, y: 200 } });
    await page.mouse.wheel(0, 3000);
    await expect(lastAction).toBeInViewport({ timeout: 3000 });
    await expect(page.getByText('技能以文字工作规范运行，不执行上传代码。', { exact: false })).toBeInViewport();
    await page.mouse.wheel(0, -3000);
    await expect(page.getByRole('heading', { name: '把海事经验，写成技能。' })).toBeInViewport();
    await page.getByRole('button', { name: '查看工作方法' }).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await page.locator('.maritime-desk').hover({ position: { x: 100, y: 200 } });
    await page.mouse.wheel(0, 3000);
    await expect(lastAction).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: fileURLToPath(new URL(`../../docs/acceptance/office-scroll-${viewport.width}.png`, import.meta.url)) });
  });
}
