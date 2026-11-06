import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

test("real UI: register, author skill, retrieve corpus, calculate and version a case", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/register");
  await page.getByLabel("邮箱地址").fill(`qa-${randomUUID()}@example.test`);
  await page.getByLabel("密码", { exact: true }).fill(randomUUID() + "!");
  await page.getByRole("button", { name: "创建账户", exact: true }).click();
  await expect(page).toHaveURL(/\/workspace/);
  await page.goto("/workspace/skills");
  await expect(
    page.getByRole("heading", { name: "法律检索与问答", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".md-skill")).toHaveCount(8);
  await page.getByRole("button", { name: "创建技能", exact: true }).click();
  await page.getByLabel("技能名称").fill("QA 运单核对");
  await page.getByLabel("一句话介绍").fill("页面验收创建的私人技能");
  await page
    .getByLabel("工作规范")
    .fill("先核对真实运单，不足信息标注待确认。");
  await page.getByRole("button", { name: "保存技能", exact: true }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("heading", { name: "QA 运单核对", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "QA 运单核对", exact: true }),
  ).toBeVisible();
  await page.goto("/workspace/research");
  await page.getByLabel("海事检索关键词").fill("承运人");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect(page.locator(".md-result").first()).toBeVisible();
  await page.getByRole("button", { name: "查看原文片段" }).first().click();
  await expect(page.locator(".md-source-text")).not.toBeEmpty();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "海事案例", exact: true }).click();
  await page.getByLabel("海事检索关键词").fill("货损");
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect(page.locator(".md-result").first()).toBeVisible();
  await page.goto("/workspace/claims");
  await page.getByLabel("项目 1", { exact: true }).fill("测试货损");
  await page.getByLabel("数量 1", { exact: true }).fill("3");
  await page.getByLabel("费率 1", { exact: true }).fill("0.335");
  await page.getByRole("button", { name: "计算并展示过程" }).click();
  await expect(page.locator(".md-totals")).toContainText("1.01");
  await page.goto("/workspace/cases");
  await page.getByRole("button", { name: "建立案件", exact: true }).click();
  await page.getByLabel("案件名称").fill("QA 货损案件");
  await page.getByLabel("案情摘要").fill("仅用于系统验收，无真实客户信息。");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "建立案件", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "QA 货损案件" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存文书", exact: true }).click();
  await page.getByLabel("标题", { exact: true }).fill("测试草稿");
  await page.getByLabel("正文 / 核对说明").fill("请专业人员核对，尚未发送。");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.locator(".md-entry")).toContainText("测试草稿");
  await page.getByRole("button", { name: "编辑 / 复核", exact: true }).click();
  await page.getByLabel("状态", { exact: true }).selectOption("reviewed");
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.locator(".md-entry")).toContainText("已复核");
  await page.getByRole("button", { name: "版本记录", exact: true }).click();
  await expect(page.locator(".md-version-list article")).toHaveCount(2);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.screenshot({
    path: fileURLToPath(
      new URL("../../docs/acceptance/case-desktop.png", import.meta.url),
    ),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "一宗案件，一个工作空间。" }),
  ).toBeVisible();
  await expect(page.locator(".md-case-row").first()).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: fileURLToPath(
      new URL("../../docs/acceptance/cases-mobile.png", import.meta.url),
    ),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test('live model: case context reaches the model and answer returns to a draft', async ({page})=>{
  test.skip(process.env.ZHOUXU_E2E_LIVE!=='1','Requires explicit live-model opt-in');
  test.setTimeout(180000);
  await page.goto('/register');
  await page.getByLabel('邮箱地址').fill(`qa-live-${randomUUID()}@example.test`);
  await page.getByLabel('密码',{exact:true}).fill(randomUUID()+'!');
  await page.getByRole('button',{name:'创建账户',exact:true}).click();
  await expect(page).toHaveURL(/\/workspace/);
  await page.goto('/workspace/cases');
  await page.getByRole('button',{name:'建立案件',exact:true}).click();
  await page.getByLabel('案件名称').fill('QA 模型往返案件');
  await page.getByLabel('案情摘要').fill('仅为演示记录：封签编号 ZX-ROUNDTRIP-8264，发现受潮，尚无检验报告。');
  await page.getByRole('dialog').getByRole('button',{name:'建立案件',exact:true}).click();
  await page.getByRole('button',{name:'让智能体研判本案'}).click();
  await page.getByLabel('你的问题',{exact:true}).fill('本次仅整理工作记录，不作法律判断：请从附带的案件快照提取封签编号，并说明缺少哪份检验材料。控制在100字，不需要追问。');
  await page.getByRole('button',{name:'发送问题',exact:true}).click();
  await expect(page.locator('.ws-answer').last()).toContainText('ZX-ROUNDTRIP-8264',{timeout:120000});
  await page.getByRole('button',{name:'存入案件',exact:true}).last().click();
  await page.getByLabel('文书标题').fill('QA 模型回存草稿');
  await page.getByRole('button',{name:'保存草稿',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.goto('/workspace/cases');
  await page.getByRole('button',{name:/QA 模型往返案件/}).click();
  await expect(page.locator('.md-entry')).toContainText('QA 模型回存草稿');
  await expect(page.locator('.md-entry')).toContainText('ZX-ROUNDTRIP-8264');
  await expect(page.locator('.md-entry')).toContainText('待复核');
  await page.goto('/workspace?skill=law-research');
  await page.getByLabel('你的问题',{exact:true}).fill('请从法规库检索海商法承运人责任，引用一个真实工具来源的 Markdown 链接，只写100字，说明库内版本不等于当前效力核验。');
  await page.getByRole('button',{name:'发送问题',exact:true}).click();
  await expect(page.getByRole('button',{name:'核对原文 1',exact:true})).toBeVisible({timeout:120000});
  await page.getByRole('button',{name:'核对原文 1',exact:true}).click();
  await expect(page.getByRole('dialog').locator('.md-source-text')).not.toBeEmpty();
  await page.screenshot({path:fileURLToPath(new URL('../../docs/acceptance/live-citation.png',import.meta.url)),fullPage:true});
});
