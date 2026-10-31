/** Real console/session/CSRF/GraphQL adapter/queue/files; only external peers are synthetic. */
import { test, expect } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

test("automatic English and Japanese aliases select a Chinese version and keep tracking locked", async ({
  page,
}, info) => {
  await page.goto("/#/media/comics");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  for (const query of ["Star Courier", "星間郵便"]) {
    await page.getByLabel("作品或作者", { exact: true }).fill(query);
    await page.getByRole("button", { name: "搜索", exact: true }).click();
    await expect(page.getByLabel("自动别名识别")).toContainText(
      "已识别：星际邮差",
    );
    await expect(page.locator(".comic-card")).toHaveCount(1);
    await expect(page.locator(".comic-card")).toContainText("中文 · 源声明");
  }
  await page.getByRole("button", { name: "查看章节与预览" }).click();
  await expect(
    page.getByRole("combobox", { name: "中文版本", exact: true }),
  ).toContainText("中文 · 测试版本");
  await expect(page.getByText(/追更固定 中文 · 测试版本/)).toBeVisible();
  await page.getByLabel("首次订阅范围").selectOption("new");
  await page
    .getByRole("button", { name: "订阅追更", exact: true })
    .last()
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已保存追更设置" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "返回列表" }).click();
  await expect(page.getByLabel("作品或作者", { exact: true })).toHaveValue(
    "星間郵便",
  );
  await expect(page.locator(".comic-card")).toHaveCount(1);
  await page
    .getByRole("navigation", { name: "漫画订阅功能" })
    .getByRole("button", { name: "订阅追更" })
    .click();
  await expect(
    page.getByText("固定版本：中文 · 测试版本", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("chinese-version-subscription.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "删除订阅", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "订阅已删除" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "还没有订阅", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("固定版本：中文 · 测试版本", { exact: true }),
  ).toHaveCount(0);
});

test("comic search, page preview, subscription, lossless folder import and reader receipts", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/#/media/comics");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "漫画订阅", exact: true }),
  ).toBeVisible();
  await page.getByLabel("作品或作者", { exact: true }).fill("星际");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.locator(".comic-card")).toHaveCount(1);
  await expect(page.locator(".comic-card")).toContainText("星际邮差");
  await expect(page.locator(".comic-card img")).toHaveJSProperty(
    "naturalWidth",
    81,
  );
  await page.screenshot({
    path: info.outputPath("comics-search.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "查看章节与预览" }).click();
  await expect(page.locator(".comic-chapter")).toHaveCount(2);
  await page.getByRole("button", { name: "预览前 3 页" }).first().click();
  await expect(page.locator(".comic-preview img")).toHaveCount(3);
  await expect(
    page.getByRole("img", { name: "第 1 页", exact: true }),
  ).toHaveJSProperty("naturalWidth", 81);
  await page.getByRole("button", { name: "关闭预览" }).click();
  await page
    .getByRole("button", { name: "订阅追更", exact: true })
    .last()
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "已保存追更设置" }),
  ).toBeVisible();
  await expect
    .poll(
      async () => {
        await page.getByRole("button", { name: "刷新章节与阅读状态" }).click();
        return page
          .getByRole("link", { name: "在 Komga 阅读", exact: true })
          .count();
      },
      { timeout: 45000, intervals: [1000, 2000] },
    )
    .toBe(2);
  await expect(
    page.getByRole("link", { name: "在 Komga 阅读", exact: true }).first(),
  ).toHaveAttribute("href", /\/book\/.+\/read$/);
  await page.screenshot({
    path: info.outputPath("comics-detail.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "返回列表" }).click();
  await page
    .getByRole("navigation", { name: "漫画订阅功能" })
    .getByRole("button", { name: "订阅追更" })
    .click();
  await expect(page.locator(".comic-stack article")).toContainText("星际邮差");
  await page.getByRole("button", { name: "暂停追更" }).click();
  await expect(page.getByRole("button", { name: "继续追更" })).toBeVisible();
  await page.getByRole("button", { name: "继续追更" }).click();
  await page.getByRole("button", { name: "下载源", exact: true }).click();
  await page.getByLabel("下载源语言").selectOption("zh");
  await page.getByLabel("验证状态").selectOption("reader");
  await expect(
    page.locator('.comic-source-health [data-stage="search"]'),
  ).toContainText("1 条匹配");
  await expect(
    page.locator('.comic-source-health [data-stage="download"]'),
  ).toContainText("3 页");
  await expect(
    page.locator('.comic-source-health [data-stage="archive"]'),
  ).toContainText("已验证");
  await expect(
    page.locator('.comic-source-health [data-stage="reader"]'),
  ).toContainText("已验证");
  await page.getByLabel("验证状态").selectOption("unchecked");
  await expect(page.locator(".comic-source-health")).toHaveCount(0);
  await page.getByRole("button", { name: "导入图片与 CBZ" }).click();
  await page.getByRole("button", { name: "检查导入", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "导入预览 · 1 章" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "无损转换并归档" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "原图片和漫画包保持原样" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "漫画书库", exact: true }).click();
  await expect(
    page.locator(".comic-card").filter({ hasText: "我的合成图集" }),
  ).toBeVisible();
  await page
    .locator(".comic-card")
    .filter({ hasText: "我的合成图集" })
    .getByRole("button", { name: "查看章节与预览" })
    .click();
  await expect
    .poll(
      async () => {
        await page.getByRole("button", { name: "刷新章节与阅读状态" }).click();
        return page
          .getByRole("link", { name: "在 Komga 阅读", exact: true })
          .count();
      },
      { timeout: 30000, intervals: [1000, 2000] },
    )
    .toBe(1);
  const fixture = JSON.parse(
    readFileSync(resolve(".runtime/comic-browser-input.json"), "utf8"),
  );
  expect(readdirSync(resolve(fixture.input, "我的合成图集")).sort()).toEqual([
    "1.png",
    "10.png",
    "2.png",
  ]);
  await page.setViewportSize({ width: 393, height: 852 });
  await page.screenshot({
    path: info.outputPath("comics-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});

test("comic preview files require the live console login", async ({
  request,
}) => {
  const response = await request.get(
    "/api/v1/media/comics/files/" + "a".repeat(24) + "/cover",
  );
  expect(response.status()).toBe(401);
});

test("Chinese search accepts traditional spellings and aliases and survives returning from details", async ({
  page,
}) => {
  await page.goto("/#/media/comics");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText(/搜索范围：中文（简体与繁体）/)).toBeVisible();
  await expect(
    page.getByRole("option", { name: "聚合中文与多语言源" }),
  ).toHaveCount(1);
  await page.getByLabel("作品或作者", { exact: true }).fill("星際");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(/搜索完成/)).toBeVisible();
  await expect(
    page
      .getByRole("combobox", { name: "下载源", exact: true })
      .locator("option"),
  ).toHaveCount(2);
  await expect(page.locator(".comic-card")).toHaveCount(1);
  await page.getByRole("button", { name: "查看章节与预览" }).click();
  await expect(page.locator(".comic-chapter")).toHaveCount(2);
  await page.getByRole("button", { name: "返回列表" }).click();
  await expect(page.getByLabel("作品或作者", { exact: true })).toHaveValue(
    "星際",
  );
  await expect(page.locator(".comic-card")).toHaveCount(1);
  await page.getByLabel("作品或作者", { exact: true }).fill("另一个译名");
  await page.getByText("添加其他译名或原名", { exact: true }).click();
  await page.getByLabel("作品别名（可选，每行一个，最多 2 个）").fill("星際");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(/搜索完成/)).toBeVisible();
  await expect(page.locator(".comic-card")).toHaveCount(1);
  await page.getByRole("button", { name: "下载源", exact: true }).click();
  await expect(page.getByLabel("下载源语言")).toHaveValue("zh");
  await expect(page.locator(".comic-extension-details")).not.toHaveAttribute(
    "open",
  );
  await expect(
    page.locator('.comic-source-health [data-stage="search"]'),
  ).toContainText("1 条匹配");
});

test("source failure filter shows the actual stage and preserves enabled sources", async ({
  page,
}) => {
  await page.goto("/#/media/comics");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("作品或作者", { exact: true }).fill("访问拒绝演练");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(/搜索完成/)).toBeVisible();
  await page.getByRole("button", { name: "下载源", exact: true }).click();
  await page.getByLabel("下载源语言").selectOption("zh");
  await page.getByLabel("验证状态").selectOption("attention");
  const row = page
    .locator("article")
    .filter({ has: page.locator(".comic-source-health") });
  await expect(row).toHaveCount(1);
  await expect(row.locator('[data-stage="search"]')).toContainText("HTTP 403");
  await expect(row.getByRole("checkbox")).toBeChecked();
  await expect(row).not.toContainText("must-not-appear");
  await expect(row).not.toContainText("fixture.invalid");
});

test("comic detail continues a failed chapter using its original task", async ({
  page,
  request,
}) => {
  const fixture = JSON.parse(
    readFileSync(resolve(".runtime/comic-browser-input.json"), "utf8"),
  );
  const controlUrl = fixture.site_url + "/__fixture/comic-retry";
  expect(
    (await request.post(controlUrl, { data: { action: "arm" } })).ok(),
  ).toBe(true);
  await page.goto("/#/media/comics");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("作品或作者", { exact: true }).fill("星际");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(/搜索完成/)).toBeVisible();
  await page.getByRole("button", { name: "查看章节与预览" }).click();
  await page.getByRole("button", { name: "刷新章节与阅读状态" }).click();
  const chapter = page
    .locator(".comic-chapter")
    .filter({ hasText: "第3章 星间来信" });
  await chapter.getByRole("checkbox").check();
  await page.getByRole("button", { name: "下载所选 1 章" }).click();
  await page.getByRole("button", { name: "刷新章节与阅读状态" }).click();
  await expect(
    chapter.getByRole("button", { name: "继续此章任务" }),
  ).toBeVisible({ timeout: 15000 });
  await expect(chapter).toContainText("已保留 1 / 3 页");
  expect(
    (
      await request.post(controlUrl, {
        data: { action: "recover" },
      })
    ).ok(),
  ).toBe(true);
  await chapter.getByRole("button", { name: "继续此章任务" }).click();
  await expect(
    chapter.getByRole("link", { name: "在 Komga 阅读", exact: true }),
  ).toBeVisible({ timeout: 25000 });
  await expect(chapter).toContainText("已归档 3 页");
  await expect(page.locator(".comic-chapter")).toHaveCount(3);
});

test("extension stores install asynchronously and keep ordinary and private plugins separate", async ({
  page,
}) => {
  await page.goto("/#/media/comics");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "下载源", exact: true }).click();
  await page.getByText("插件仓库与每日维护", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "扩展仓库与自动更新" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "安装兼容插件并启用每日更新" })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "2 / 2 个插件已安装" }),
  ).toBeVisible({ timeout: 45000 });
  await expect(
    page.getByRole("heading", { name: "合成普通插件", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "合成私藏插件", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("收藏分类").selectOption("private");
  await expect(
    page.getByRole("heading", { name: "合成私藏插件", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "合成普通插件", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "暂停自动维护", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "自动维护已暂停" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 393, height: 852 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
