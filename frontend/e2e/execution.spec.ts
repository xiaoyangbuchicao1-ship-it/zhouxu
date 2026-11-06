import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

test('live evidence task delivers real downloadable Excel and Word files', async ({ page }) => {
  test.skip(process.env.ZHOUXU_E2E_LIVE !== '1','Explicit real model/container execution opt-in');
  test.setTimeout(240000);
  await page.goto('/register');
  await page.getByLabel('邮箱地址').fill(`exec-ui-${randomUUID()}@example.test`);
  await page.getByLabel('密码',{exact:true}).fill(randomUUID()+'!');
  await page.getByRole('button',{name:'创建账户',exact:true}).click();
  await expect(page).toHaveURL(/\/workspace/);
  await page.goto('/workspace?skill=evidence-review');
  await page.getByRole('button',{name:'工作',exact:true}).click();
  await page.locator('input[type=file]').setInputFiles({name:'演示运输记录.txt',mimeType:'text/plain',buffer:Buffer.from('演示资料：2026年10月1日装货，封签ZX-UI-9081；2026年10月8日卸货发现3箱外包装受潮，内部是否受损未知。检验报告和请求发送日期未提供。')});
  await page.getByLabel('你的问题',{exact:true}).fill('根据附件实际生成Excel时间线与Word补证清单草稿。用Python读取授权材料、生成、重新打开核对并交付xlsx和docx，不要只给代码或Markdown，不推定责任。');
  await expect(page.getByRole('button',{name:'发送问题',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'发送问题',exact:true}).click();
  const excel=page.locator('.md-work-package').filter({has:page.locator('strong').filter({hasText:/\.xlsx$/})});
  const word=page.locator('.md-work-package').filter({has:page.locator('strong').filter({hasText:/\.docx$/})});
  await expect(excel.first()).toBeVisible({timeout:180000});
  await expect(word.first()).toBeVisible({timeout:180000});
  await excel.first().getByRole('button', {name:/预览/}).click();
  await expect(page.locator('iframe.md-office-preview-frame')).toBeVisible({timeout:60000});
  await expect(page.frameLocator('iframe.md-office-preview-frame').locator('img').first()).toBeVisible({timeout:60000});
  await page.getByRole('button',{name:'关闭'}).last().click();
  for (const file of [excel.first(),word.first()]) {
    const event=page.waitForEvent('download');await file.getByRole('button',{name:/下载/}).click();const download=await event;
    expect(download.suggestedFilename()).toMatch(/\.(xlsx|docx)$/);
    expect(await download.failure()).toBeNull();
  }
  await page.screenshot({animations:'disabled',path:fileURLToPath(new URL('../../docs/acceptance/execution-v1/browser.png',import.meta.url))});
  await page.setViewportSize({width:390,height:844});
  await expect(excel.first()).toBeVisible();
  await page.screenshot({animations:'disabled',path:fileURLToPath(new URL('../../docs/acceptance/execution-v1/browser-mobile.png',import.meta.url))});
});
