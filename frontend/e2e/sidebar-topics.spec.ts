import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

test('conversation topic persists and pinned heading aligns with history controls', async ({page}) => {
  test.skip(process.env.ZHOUXU_E2E_LIVE !== '1', 'Requires an explicitly enabled live model submission');
  await page.goto('/register');
  await page.getByLabel('邮箱地址').fill(`sidebar-${randomUUID()}@example.test`);
  await page.getByLabel('密码',{exact:true}).fill(randomUUID()+'!');
  await page.getByRole('button',{name:'创建账户',exact:true}).click();
  await expect(page).toHaveURL(/\/workspace/);
  const question='用一句话解释提单的作用';
  await page.getByLabel('你的问题',{exact:true}).fill(question);
  await page.getByRole('button',{name:'发送问题',exact:true}).click();
  const history=page.locator('.ws-history');
  await expect(history.locator('.ws-history-link')).toHaveText(question);
  await history.getByRole('button',{name:`${question}，更多操作`}).click();
  await page.getByRole('menuitem',{name:'置顶对话',exact:true}).click();
  await expect(page.locator('.ws-pinned-heading')).toHaveText('置顶');
  await expect(page.getByRole('button',{name:'刷新对话列表'})).toHaveCount(0);
  await expect(page.locator('.ws-history > .ws-section-label')).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.ws-history-link')).toHaveText(question);
  await expect(page.locator('.ws-pinned-heading')).toBeVisible();
  await page.screenshot({animations:'disabled',path:fileURLToPath(new URL('../../docs/acceptance/method-v2/sidebar-topics.png',import.meta.url))});
});
