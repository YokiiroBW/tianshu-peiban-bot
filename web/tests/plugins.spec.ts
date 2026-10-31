import { test, expect, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.addInitScript(() =>
    Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined }),
  );
  await page.goto("/#/plugins");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "已安装", exact: true }),
  ).toBeVisible();
}

async function install(page: Page, name: string, instance: string) {
  await page
    .getByRole("button", { name: "发现插件", exact: true })
    .first()
    .click();
  await page
    .locator(".plugins-package")
    .filter({ has: page.getByRole("heading", { name, exact: true }) })
    .getByRole("button", { name: "安装", exact: true })
    .click();
  await page.getByRole("button", { name: "已安装", exact: true }).click();
  const card = page
    .locator(".plugins-package")
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
  await card.getByRole("button", { name: "新建实例" }).click();
  await page.getByLabel("实例名称").fill(instance);
  await page.getByRole("button", { name: "创建实例", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "插件实例" })
      .getByRole("heading", { name: instance }),
  ).toBeVisible();
}

test("real plugin UI: LAN requests, uncertain-response retry, iframe, scopes, themes and mobile", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  await install(page, "回声", "网页回声验收");
  const detail = page.getByRole("region", { name: "插件实例" });
  await detail.getByRole("button", { name: "启用", exact: true }).click();
  await expect(detail.locator(".plugins-status")).toHaveText("运行中");
  await page.locator(".plugins-action input").fill("请求只执行一次");
  let dropped = false;
  await page.route("**/api/v1/plugins/invoke", async (route) => {
    if (!dropped) {
      dropped = true;
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  await page
    .locator(".plugins-action")
    .getByRole("button", { name: "执行", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("结果尚未确认");
  await page
    .locator(".plugins-action")
    .getByRole("button", { name: "执行", exact: true })
    .click();
  await detail.getByRole("button", { name: "任务", exact: true }).click();
  await expect(page.locator(".plugins-record pre")).toContainText(
    "请求只执行一次",
  );
  await expect(page.locator(".plugins-record")).toHaveCount(1);
  await page.unroute("**/api/v1/plugins/invoke");
  await detail.getByRole("button", { name: "功能", exact: true }).click();
  const frame = page.frameLocator("iframe.plugins-view");
  await expect(frame.getByText("页面已连接", { exact: true })).toBeVisible();
  await frame.getByLabel("页面回声内容").fill("独立页面真实回执");
  await frame.getByRole("button", { name: "在独立页面执行" }).click();
  await expect(frame.locator("#echo-result")).toHaveText("独立页面真实回执");
  await expect(page.locator("iframe.plugins-view")).toHaveAttribute(
    "sandbox",
    "allow-scripts allow-forms",
  );
  expect(
    await frame.locator("body").evaluate(() => {
      try {
        return parent.document.cookie;
      } catch {
        return "isolated";
      }
    }),
  ).toBe("isolated");
  await detail.getByRole("button", { name: "作用范围", exact: true }).click();
  await page.getByRole("button", { name: "添加网页工作区" }).click();
  await page.getByLabel("工作区名称").fill("main");
  await page.getByRole("button", { name: "保存范围" }).click();
  await expect(page.getByText("操作已保存。", { exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "功能", exact: true }).click();
  await page.getByLabel("使用范围").selectOption({ label: "main" });
  await expect(frame.getByText("页面已连接", { exact: true })).toBeVisible();
  await page.evaluate(() => {
    (document.activeElement as HTMLElement)?.blur();
    scrollTo(0, 0);
  });
  await page.screenshot({
    path: info.outputPath("plugins-desktop.png"),
    fullPage: true,
  });
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
  });
  await page.screenshot({
    path: info.outputPath("plugins-dark.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("plugins-mobile.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await detail.getByRole("button", { name: "停用", exact: true }).click();
  await expect(detail.locator(".plugins-status")).toHaveText("已停用");
  expect(errors).toEqual([]);
});

test("real query plugin: generated settings form, encrypted secret and HTTP provider result", async ({
  page,
}) => {
  await login(page);
  await install(page, "HTTP 查询", "天气查询验收");
  const detail = page.getByRole("region", { name: "插件实例" });
  await expect(detail.locator(".plugins-status")).toHaveText("待配置");
  await detail.getByRole("button", { name: "配置", exact: true }).click();
  await page.getByLabel("服务地址").fill("http://127.0.0.1:4875");
  await page
    .locator(".plugins-config input[type=password]")
    .fill("synthetic-query-secret");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(
    page.getByText("访问密钥 · 已填写", { exact: true }),
  ).toBeVisible();
  await detail.getByRole("button", { name: "启用", exact: true }).click();
  await expect(detail.locator(".plugins-status")).toHaveText("运行中");
  await detail.getByRole("button", { name: "功能", exact: true }).click();
  await page.getByLabel("接口路径").fill("/weather");
  await page
    .locator(".plugins-action")
    .getByRole("button", { name: "执行", exact: true })
    .click();
  await detail.getByRole("button", { name: "任务", exact: true }).click();
  await expect(page.locator(".plugins-record pre")).toContainText("杭州");
  await expect(page.locator(".plugins-record pre")).toContainText("17");
  expect(await page.locator(".plugins-page").innerText()).not.toContain(
    "synthetic-query-secret",
  );
});

test("real role binding: choose a registered actor and explicit actions, save and reload", async ({
  page,
}) => {
  await login(page);
  await install(page, "回声", "角色能力验收");
  const detail = page.getByRole("region", { name: "插件实例" });
  await detail.getByRole("button", { name: "作用范围", exact: true }).click();
  await page.getByRole("button", { name: "添加角色能力", exact: true }).click();
  await page
    .getByRole("combobox", { name: "角色", exact: true })
    .selectOption({ label: "合成角色乙" });
  const actions = page.getByRole("group", {
    name: "允许调用的动作",
    exact: true,
  });
  const first = actions.getByRole("checkbox").first();
  await first.check();
  const firstName = await first.locator("..").innerText();
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/plugins/bindings") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "保存范围", exact: true }).click();
  const response = await saved;
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const binding = body.bindings.find(
    (row: { scope: { kind: string } }) => row.scope.kind === "character",
  );
  expect(binding.scope.character_id).toBe("actor:b");
  expect(binding.entries).toHaveLength(1);
  await detail.getByRole("button", { name: "功能", exact: true }).click();
  await detail.getByRole("button", { name: "作用范围", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "角色", exact: true }),
  ).toHaveValue("actor:b");
  await expect(
    page
      .getByRole("group", { name: "允许调用的动作", exact: true })
      .getByRole("checkbox", { name: firstName }),
  ).toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
