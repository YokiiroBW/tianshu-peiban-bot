import { expect, test, type Page, type Route } from "@playwright/test";

function reply(route: Route, value: unknown) {
  return route.fulfill({ json: value });
}

async function signedIn(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    reply(route, {
      authenticated: true,
      csrf: "synthetic-settings",
      username: "fixture-manager",
      conversations: [],
      dialogue: { available: false, code: "model_not_configured", model: "" },
    }),
  );
}

function externalView(revision = 4) {
  return {
    revision,
    unlocked: true,
    assets: {
      configured: true,
      enabled: true,
      url: "https://assets.example.test/assetlink/v1/control",
      credential_configured: true,
      ca_configured: false,
      last_test: null,
    },
    home: {
      configured: true,
      enabled: true,
      url: "http://192.0.2.10:8123",
      credential_configured: true,
      ca_configured: false,
      last_test: null,
      allow_private_http: true,
      entities: [
        { entity_id: "sensor.fixture", label: "测试读数", kind: "sensor" },
      ],
    },
  };
}

test("旧入口和新子页面各自只显示当前设置", async ({ page }) => {
  await signedIn(page);
  const requests: string[] = [];
  page.on("request", (request) =>
    requests.push(new URL(request.url()).pathname),
  );
  await page.route("**/api/v1/connections/view", (route) =>
    reply(route, { connections: [] }),
  );
  await page.route("**/api/v1/external/view", (route) =>
    reply(route, externalView()),
  );
  await page.route("**/api/v1/bots/view", (route) =>
    reply(route, {
      available: true,
      slots: [],
      connections: [],
      management: { code: "ok", unlocked: false },
    }),
  );
  await page.route("**/api/v1/bot-adapters/view", (route) =>
    reply(route, {
      available: false,
      unlocked: false,
      actors: [],
      connections: [],
    }),
  );
  await page.route("**/api/v1/access/view", (route) =>
    reply(route, {
      available: false,
      active: { mode: "http", origin: "", certificate: null },
      saved: { mode: "http", origin: "", certificate: null },
      revision: 1,
      restart_required: false,
      certificates: [],
      listener_port: 80,
    }),
  );

  await page.goto("/#/settings/connections");
  await expect(
    page.getByRole("heading", { name: "连接设置入口" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "功能连接" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: /机器人接入/ }).last(),
  ).toHaveAttribute("href", "#/settings/channels");
  expect(requests).toContain("/api/v1/connections/view");
  expect(requests).not.toContain("/api/v1/bots/view");
  expect(requests).not.toContain("/api/v1/external/view");
  expect(requests).not.toContain("/api/v1/access/view");

  await page.goto("/#/settings/models");
  await expect(page.getByRole("heading", { name: "模型配置" })).toBeVisible();
  await page.goto("/#/settings/channels");
  await expect(page.getByRole("heading", { name: "机器人接入" })).toBeVisible();
  await expect(
    page.getByText("当前部署尚未启用网页适配器管理", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "添加适配器" })).toHaveCount(0);
  await page.getByText("旧连接管理（已配置的连接）").click();
  await expect(
    page.getByRole("heading", { name: "尚未添加机器人" }),
  ).toBeVisible();
  await expect(
    page.getByText("机器人账号、宿主实例", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("NoneBot 插件", { exact: false })).toBeVisible();
  await expect(page.getByText("AstrBot 插件", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "创建连接" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "解锁连接管理" })).toHaveCount(
    0,
  );

  await page.goto("/#/settings/assets");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await expect(
    page.getByRole("heading", { name: "AssetLink 资产连接", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("Home Assistant 基础地址")).toHaveCount(0);
  await page.goto("/#/settings/home");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await expect(
    page.getByRole("heading", { name: "Home Assistant 家庭连接", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("AssetLink HTTPS 端点")).toHaveCount(0);
  await page.goto("/#/settings/access");
  await expect(
    page.getByRole("heading", { name: "访问地址与 HTTPS" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "机器人连接" })).toHaveCount(
    0,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("切换资产与家庭页面时草稿隔离，保存只提交当前连接", async ({ page }) => {
  await signedIn(page);
  let revision = 4;
  const saves: Record<string, unknown>[] = [];
  await page.route("**/api/v1/external/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/view")) return reply(route, externalView(revision));
    if (path.endsWith("/save")) {
      saves.push(route.request().postDataJSON());
      revision++;
      return reply(route, {
        revision,
        state: "saved_unverified",
        applied: true,
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/assets");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await page
    .getByLabel("AssetLink HTTPS 端点")
    .fill("https://unsaved.example.test/assetlink/v1/control");
  await page.goto("/#/settings/home");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await expect(page.getByLabel("Home Assistant 基础地址")).toHaveValue(
    "http://192.0.2.10:8123",
  );
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("连接设置已保存并生效", { exact: false }),
  ).toBeVisible();
  expect(saves).toHaveLength(1);
  expect(saves[0]).toMatchObject({
    kind: "home",
    expected_revision: 4,
    value: {
      base_url: "http://192.0.2.10:8123",
      entities: [
        { entity_id: "sensor.fixture", label: "测试读数", kind: "sensor" },
      ],
    },
    credential: { action: "keep" },
  });
  expect(JSON.stringify(saves[0])).not.toContain("unsaved.example.test");
  expect(JSON.stringify(saves[0])).not.toContain("assetlink/v1/control");

  await page.goto("/#/settings/assets");
  await page.getByRole("button", { name: /编辑.*设置/ }).click();
  await expect(page.getByLabel("AssetLink HTTPS 端点")).toHaveValue(
    "https://assets.example.test/assetlink/v1/control",
  );
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect.poll(() => saves.length).toBe(2);
  expect(saves[1]).toMatchObject({
    kind: "assets",
    expected_revision: 5,
    value: { endpoint: "https://assets.example.test/assetlink/v1/control" },
    credential: { action: "keep" },
  });
  expect(JSON.stringify(saves[1])).not.toContain("sensor.fixture");
  expect(JSON.stringify(saves[1])).not.toContain("192.0.2.10");
});

test("未登录时新设置页仍要求登录", async ({ page }) => {
  await page.route("**/api/v1/session", (route) =>
    reply(route, { authenticated: false, csrf: "synthetic-guest" }),
  );
  await page.goto("/#/settings/channels");
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "机器人连接" })).toHaveCount(
    0,
  );
});
