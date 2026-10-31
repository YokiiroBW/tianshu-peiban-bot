import { expect, test, type Page, type Route } from "@playwright/test";
import { adapterErrorMessage } from "../src/features/settings/botAdapterApi";

const actor = { id: "xiaotian", label: "小天" };
const account = { id: "100200300", platform: "qq", label: "测试机器人" };
const expiry = () => new Date(Date.now() + 5 * 60_000).toISOString();

function connection(enabled = false, revision = 1, state = "disabled") {
  return {
    id: "adapter:fixture-1",
    name: "家庭测试群",
    adapter: "astrbot",
    address: "https://bot.example.test:8443",
    account_id: account.id,
    conversation: { kind: "group", id: "123456" },
    allowed_authors: ["111", "222"],
    actor_id: actor.id,
    enabled,
    revision,
    state,
    last_error: null,
    last_checked_at: "2026-09-28T00:00:00Z",
  };
}

async function signedIn(page: Page) {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        csrf: "adapter-csrf",
        username: "fixture-manager",
        conversations: [],
        dialogue: { available: false, code: "model_not_configured", model: "" },
      },
    }),
  );
  await page.route("**/api/v1/bots/view", (route) =>
    route.fulfill({
      json: {
        available: true,
        slots: [],
        connections: [],
        management: { code: "ok", unlocked: false },
      },
    }),
  );
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({ status, json: value });
}

test("OneBot direct connection explains the service address and probes the selected adapter", async ({
  page,
}, testInfo) => {
  await signedIn(page);
  let probed = false;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view") {
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [],
      });
    }
    if (operation === "probe") {
      expect(route.request().postDataJSON()).toMatchObject({
        adapter: "onebot",
        address: "https://onebot.example.test",
        access_key: "synthetic-key",
      });
      probed = true;
      return json(route, {
        draft_id: "draft:onebot",
        expires_at: expiry(),
        protocol: "tianshu.bot-adapter/v1",
        instance_id: "host:onebot",
        accounts: [account],
      });
    }
    throw new Error(`Unexpected OneBot request: ${operation}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("机器人平台").selectOption("onebot");
  await expect(
    panel.getByText(/填写天枢 OneBot 接入服务的 HTTP 地址/),
  ).toBeVisible();
  await panel.getByLabel("插件地址").fill("https://onebot.example.test");
  await panel.getByLabel("插件连接密钥").fill("synthetic-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(panel.getByLabel("回复角色")).toBeVisible();
  expect(probed).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("onebot-direct.png"),
    fullPage: true,
  });
});

test("backend adapter fault codes have actionable Chinese messages", () => {
  const cases: Record<string, string> = {
    adapter_unauthorized: "连接密钥",
    adapter_not_installed: "安装并启用插件",
    adapter_incompatible: "协议或版本不兼容",
    adapter_unavailable: "插件运行状态",
    adapter_unreachable: "插件地址",
    adapter_redirect: "地址发生跳转",
    external_target_changed: "网络目标已变化",
    bot_role_not_approved: "角色尚未获后台批准",
    queue_full: "已达上限",
  };
  for (const [code, phrase] of Object.entries(cases))
    expect(adapterErrorMessage(code)).toContain(phrase);
});

function probePayload() {
  return {
    draft_id: "draft:real-probe",
    expires_at: expiry(),
    protocol: "tianshu.bot-adapter/v1",
    instance_id: "host:fixture",
    accounts: [account],
  };
}

test("account observation is the default entry without conversation, author or actor", async ({
  page,
}) => {
  await signedIn(page);
  let unlocked = false;
  let saved: Record<string, unknown> | null = null;
  let createBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/bots/unlock", (route) => {
    unlocked = true;
    return json(route, { unlocked: true });
  });
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [],
        connections: [],
      });
    if (operation === "onebot-servers")
      return json(route, { available: false, servers: [] });
    if (operation === "probe") return json(route, probePayload());
    throw new Error(`Unexpected adapter request: ${operation}`);
  });
  await page.route("**/api/v1/bot-observation/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view")
      return json(route, {
        available: true,
        unlocked,
        connections: saved ? [saved] : [],
      });
    if (operation === "create") {
      createBody = route.request().postDataJSON() as Record<string, unknown>;
      saved = {
        id: "obs:fixture",
        name: "默认观察",
        adapter: "astrbot",
        account_id: account.id,
        instance_id: "host:fixture",
        enabled: true,
        revision: 1,
        host_revision: 1,
        state: "ready",
        last_error: null,
        last_checked_at: null,
        read_enabled: true,
        host_pending: 0,
        host_dropped: 0,
        group_policy: {
          version: 2,
          default: { mode: "observe", actor_id: null, keywords: [] },
          groups: {},
        },
        private_policy: {
          observe: true,
          mode: "observe_only",
          list: [],
          actor_id: null,
        },
      };
      return json(route, { connection: saved });
    }
    throw new Error(`Unexpected observation request: ${operation}`);
  });
  await page.goto("/#/settings/channels");
  const legacy = page.locator(".bot-legacy").first();
  await expect(legacy).not.toHaveAttribute("open", "");
  await expect(
    page.getByRole("region", { name: "账号级观察与回复策略" }),
  ).toBeVisible();
  await page
    .getByLabel("管理员密码（二次验证）")
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁连接管理" }).click();
  const observation = page.getByRole("region", {
    name: "账号级观察与回复策略",
  });
  await observation
    .getByRole("button", { name: "添加机器人账号", exact: true })
    .click();
  await observation.getByLabel("接入方式").selectOption("astrbot");
  await observation
    .getByLabel("观察服务 URL")
    .fill("https://bot.example.test:8443");
  await observation
    .getByLabel("观察服务密钥")
    .fill("synthetic-plugin-key-1234567890");
  await observation.getByRole("button", { name: "检测在线账号" }).click();
  await observation.getByLabel("账号观察名称").fill("默认观察");
  await observation.getByRole("button", { name: "保存账号连接" }).click();
  await expect(
    observation.getByRole("dialog").getByText("账号已接入", { exact: false }),
  ).toBeVisible();
  expect(createBody).toMatchObject({
    account_id: account.id,
    name: "默认观察",
  });
  expect(createBody).not.toHaveProperty("conversation");
  expect(createBody).not.toHaveProperty("allowed_authors");
  expect(createBody).not.toHaveProperty("actor_id");
});

test("built-in OneBot creates a server and automatically shows its connected account", async ({
  page,
}) => {
  await signedIn(page);
  let unlocked = false;
  let created: Record<string, unknown> | null = null;
  let connected = false;
  const server = () => ({
    id: "onebot:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    name: "第二个 QQ",
    token: "synthetic-generated-token",
    account_id: connected ? "10002" : null,
    connected,
    observation_id: connected ? "obs:10002" : null,
    observation_state: connected ? "ready" : null,
    ws_path: "/onebot/v11/ws/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  });
  await page.route("**/api/v1/bots/unlock", (route) => {
    unlocked = true;
    return json(route, { unlocked: true });
  });
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [],
        connections: [],
      });
    if (operation === "onebot-create") {
      created = route.request().postDataJSON() as Record<string, unknown>;
      return json(route, {
        available: true,
        ws_port: 18765,
        servers: [server()],
      });
    }
    if (operation === "onebot-servers")
      return json(route, {
        available: true,
        ws_port: 18765,
        servers: created ? [server()] : [],
      });
    throw new Error(`Unexpected adapter request: ${operation}`);
  });
  await page.route("**/api/v1/bot-observation/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view")
      return json(route, { available: true, unlocked, connections: [] });
    throw new Error(`Unexpected observation request: ${operation}`);
  });
  await page.goto("/#/settings/channels");
  await page
    .getByLabel("管理员密码（二次验证）")
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁连接管理" }).click();
  const observation = page.getByRole("region", {
    name: "账号级观察与回复策略",
  });
  await observation
    .getByRole("button", { name: "添加机器人账号", exact: true })
    .click();
  await observation.getByLabel("机器人名称").fill("第二个 QQ");
  await observation
    .getByRole("button", { name: "创建机器人", exact: true })
    .click();
  expect(created).toMatchObject({ name: "第二个 QQ", token: "" });
  const robot = observation.getByRole("region", { name: "第二个 QQ" });
  await expect(robot.getByLabel("反向 WebSocket 地址")).toHaveValue(
    "ws://127.0.0.1:18765/onebot/v11/ws/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  );
  await expect(robot.getByText("等待客户端连接")).toBeVisible();
  await expect(
    observation.getByRole("button", { name: "检测在线账号" }),
  ).toHaveCount(0);
  connected = true;
  await expect(robot.getByText("已连接 QQ 10002")).toBeVisible({
    timeout: 6000,
  });
  await observation.getByLabel("接入方式").selectOption("astrbot");
  await expect(observation.getByLabel("观察服务 URL")).toBeVisible();
});

test("observation cards render connected, archive, error and locked states", async ({
  page,
}, testInfo) => {
  await signedIn(page);
  let unlocked = false;
  let lockedState = false;
  const policy = {
    observe: true,
    mode: "observe_only",
    list: [],
    actor_id: null,
  };
  const observed = () => ({
    id: "obs:fixture",
    name: "家庭观察",
    adapter: "nonebot",
    account_id: "1365939091",
    instance_id: "host:fixture",
    enabled: true,
    revision: 2,
    host_revision: 2,
    state: lockedState ? "unknown" : "ready",
    last_error: lockedState
      ? "adapter_unreachable: fixture-host-long-identifier"
      : null,
    last_checked_at: "2026-09-29T00:00:00Z",
    read_enabled: !lockedState,
    host_pending: 2,
    host_dropped: 0,
    group_policy: {
      version: 2,
      default: { mode: "observe", actor_id: null, keywords: [] },
      groups: {},
    },
    private_policy: policy,
  });
  await page.route("**/api/v1/bots/unlock", (route) => {
    unlocked = true;
    return json(route, { unlocked: true });
  });
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view")
      return json(route, {
        available: true,
        unlocked: unlocked && !lockedState,
        actors: [{ id: "xiaotian", label: "小天" }],
        connections: [],
      });
    throw new Error(`Unexpected adapter request: ${operation}`);
  });
  await page.route("**/api/v1/bot-observation/*", (route) => {
    const operation = new URL(route.request().url()).pathname.split("/").pop();
    if (operation === "view")
      return json(route, {
        available: true,
        unlocked: unlocked && !lockedState,
        connections: [observed()],
      });
    if (operation === "discovered")
      return json(route, {
        items: [
          {
            conversation: "group:123456789012345678",
            author: "987654321",
            count: 3,
            last_at: 1790630400,
            decision: {
              observe: true,
              reply_permitted: false,
              reply_triggered: false,
            },
          },
        ],
        next_cursor: null,
      });
    if (operation === "archive")
      return json(route, {
        memory_state: "available",
        backlog: { pending_memory: 2, failed: 0, revoked: 0 },
        archive_next_cursor: null,
        items: [
          {
            source_ref: "source:fixture-long-identifier-123456789",
            author: "987654321",
            archive_state: "pending_memory",
            error_code: null,
          },
        ],
        archive_items: [
          {
            source_ref: "source:fixture-1",
            author: "987654321",
            content_state: "text",
            text: "仅供隔离页面测试的合成内容",
            sent_at: "2026-09-29 08:00",
          },
        ],
      });
    throw new Error(`Unexpected observation request: ${operation}`);
  });

  await page.goto("/#/settings/channels");
  await page
    .getByLabel("管理员密码（二次验证）")
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "解锁连接管理" }).click();
  const observation = page.getByRole("region", {
    name: "账号级观察与回复策略",
  });
  await observation.getByRole("button", { name: "读取发现列表" }).click();
  await observation.getByRole("button", { name: "查看归档状态" }).click();
  await expect(
    observation.getByText("运行正常", { exact: true }),
  ).toBeVisible();
  await expect(observation.getByText("Memory 可读取")).toBeVisible();
  await observation
    .getByRole("button", { name: "群聊与私聊策略", exact: true })
    .click();
  await expect(
    observation.getByRole("link", { name: "用户 → 群画像" }),
  ).toBeVisible();
  await expect(
    observation.getByRole("group", { name: "私聊策略" }),
  ).toBeVisible();
  expect(
    await observation
      .getByLabel("已接入账号")
      .evaluate((select) => getComputedStyle(select).borderRadius),
  ).not.toBe("0px");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await observation.screenshot({
    path: testInfo.outputPath("observation-ready.png"),
  });

  lockedState = true;
  await page.reload();
  await expect(
    observation.getByText("状态待核对", { exact: true }),
  ).toBeVisible();
  await expect(
    observation.getByRole("alert").filter({ hasText: "adapter_unreachable" }),
  ).toBeVisible();
  await observation
    .getByRole("button", { name: "群聊与私聊策略", exact: true })
    .click();
  await expect(
    observation.getByRole("button", { name: "保存私聊策略" }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await observation.screenshot({
    path: testInfo.outputPath("observation-locked-error.png"),
  });
});

async function fillWizard(page: Page) {
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("插件地址").fill("https://bot.example.test:8443");
  await panel.getByLabel("插件连接密钥").fill("fixture-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await panel.getByLabel("连接名称").fill("家庭测试群");
  await panel.getByLabel("回复角色").selectOption(actor.id);
  await panel.getByLabel("群 ID").fill("123456");
  await panel.getByLabel("明确允许的作者 ID").fill("111, 222");
  return panel;
}

test("adapter wizard detects a real returned account, saves disabled, then explicitly enables and disables", async ({
  page,
}, testInfo) => {
  await signedIn(page);
  let unlocked = false;
  let saved: ReturnType<typeof connection> | null = null;
  const requests: { path: string; body: Record<string, unknown> }[] = [];
  await page.route("**/api/v1/bots/unlock", (route) => {
    expect(route.request().headers()["x-csrf-token"]).toBe("adapter-csrf");
    expect(route.request().postDataJSON()).toEqual({
      password: "synthetic-local-password-014",
    });
    unlocked = true;
    return json(route, { unlocked: true });
  });
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop()!;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    expect(route.request().headers()["x-csrf-token"]).toBe("adapter-csrf");
    requests.push({ path, body });
    if (path === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [actor],
        connections: saved ? [saved] : [],
      });
    if (path === "probe") {
      expect(body).toMatchObject({
        adapter: "astrbot",
        address: "https://bot.example.test:8443",
        access_key: "secret-only-in-request",
        allow_private_http: false,
        ca_pem: null,
      });
      return json(route, {
        draft_id: "draft:real-probe",
        expires_at: expiry(),
        protocol: "tianshu.bot-adapter/v1",
        instance_id: "host:fixture",
        accounts: [account],
      });
    }
    if (path === "create") {
      expect(body).toMatchObject({
        draft_id: "draft:real-probe",
        name: "家庭测试群",
        account_id: account.id,
        conversation: { kind: "group", id: "123456" },
        allowed_authors: ["111", "222"],
        actor_id: actor.id,
      });
      expect(body.client_request_id).toEqual(expect.any(String));
      saved = connection();
      return json(route, { connection: saved });
    }
    if (path === "enable" || path === "disable") {
      expect(body).toMatchObject({
        id: "adapter:fixture-1",
        expected_revision: saved?.revision,
      });
      expect(body.client_request_id).toEqual(expect.any(String));
      saved = connection(
        path === "enable",
        (saved?.revision ?? 0) + 1,
        path === "enable" ? "ready" : "disabled",
      );
      return json(route, { connection: saved });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel
    .getByLabel("管理员密码（二次验证）")
    .fill("synthetic-local-password-014");
  await panel.getByRole("button", { name: "解锁连接管理" }).click();
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await expect(panel.getByLabel("机器人平台")).toHaveValue("astrbot");
  await expect(panel.getByText("协议路径由天枢自动处理")).not.toBeVisible();
  await panel.getByLabel("插件地址").fill("https://bot.example.test:8443");
  await panel.getByLabel("插件连接密钥").fill("secret-only-in-request");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(panel.getByLabel("插件连接密钥")).toHaveValue("");
  await expect(
    panel.getByRole("option", { name: /测试机器人/ }),
  ).toBeAttached();
  await panel.getByLabel("连接名称").fill("家庭测试群");
  await panel.getByLabel("回复角色").selectOption("xiaotian");
  await panel.getByLabel("群 ID").fill("123456");
  await panel.getByLabel("明确允许的作者 ID").fill("111, 222\n111");
  await page.screenshot({
    path: testInfo.outputPath("bot-adapter-wizard.png"),
    fullPage: true,
  });
  await panel.getByRole("button", { name: "保存为停用" }).click();
  await expect(
    panel.getByText("连接已保存且保持停用", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("已保存 · 未启用")).toBeVisible();
  expect(requests.filter((item) => item.path === "create")).toHaveLength(1);
  expect(
    JSON.stringify(requests.filter((item) => item.path === "create")),
  ).not.toContain("secret-only-in-request");
  await panel.getByRole("button", { name: "启用", exact: true }).click();
  await expect(panel.getByText("已启用", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "停用", exact: true }).click();
  await expect(panel.getByText("已保存 · 未启用")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("bot-adapter-saved.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("adapter errors, empty SDK accounts, expired draft and cancellation never save", async ({
  page,
}) => {
  await signedIn(page);
  let probeCount = 0;
  let createCount = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [],
      });
    if (path === "probe") {
      probeCount++;
      if (probeCount === 1)
        return json(route, { code: "adapter_unauthorized" }, 401);
      if (probeCount === 2)
        return json(route, {
          draft_id: "empty",
          expires_at: expiry(),
          protocol: "tianshu.bot-adapter/v1",
          instance_id: "host:fixture",
          accounts: [],
        });
      return json(route, {
        draft_id: "expired",
        expires_at: new Date(Date.now() + 200).toISOString(),
        protocol: "tianshu.bot-adapter/v1",
        instance_id: "host:fixture",
        accounts: [account],
      });
    }
    if (path === "create") createCount++;
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("插件地址").fill("http://192.168.1.10:8080");
  await panel.getByLabel("插件连接密钥").fill("bad-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByText("局域网 HTTP 需要显式勾选", { exact: false }),
  ).toBeVisible();
  expect(probeCount).toBe(0);
  await panel.getByLabel("允许局域网 HTTP（仅私有或本机地址）").check();
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByText("插件拒绝了连接密钥", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByLabel("插件连接密钥")).toHaveValue("");
  await panel.getByLabel("插件连接密钥").fill("valid-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByText("SDK 当前没有在线 QQ 机器人账号", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByRole("button", { name: "保存为停用" })).toHaveCount(
    0,
  );
  await panel.getByLabel("插件连接密钥").fill("valid-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(panel.getByRole("button", { name: "保存为停用" })).toBeVisible();
  await expect(panel.getByText("草稿已过期", { exact: false })).toBeVisible();
  await expect(panel.getByRole("button", { name: "保存为停用" })).toHaveCount(
    0,
  );
  await panel.getByRole("button", { name: "取消" }).click();
  await expect(panel.getByLabel("插件地址")).toHaveCount(0);
  await page.goto("/#/settings/assets");
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await expect(panel.getByLabel("插件连接密钥")).toHaveValue("");
  expect(createCount).toBe(0);
});

test("unknown write result is not reported as enabled or automatically retried", async ({
  page,
}) => {
  await signedIn(page);
  let writes = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [connection()],
      });
    if (path === "enable") {
      writes++;
      return json(
        route,
        { code: "dependency_unavailable", execution_state: "unknown" },
        503,
      );
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "启用", exact: true }).click();
  await expect(
    panel.getByText("操作结果无法确认", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("已启用", { exact: true })).toHaveCount(0);
  expect(writes).toBe(1);
});

test("unreachable address and incompatible protocol explain why a draft was not created", async ({
  page,
}) => {
  await signedIn(page);
  let attempts = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [],
      });
    if (path === "probe") {
      attempts++;
      if (attempts === 1)
        return json(route, { code: "adapter_unreachable" }, 503);
      return json(route, {
        draft_id: "wrong-version",
        expires_at: expiry(),
        protocol: "incompatible/v2",
        instance_id: "host:fixture",
        accounts: [account],
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("插件地址").fill("https://bot.example.test:8443");
  await panel.getByLabel("插件连接密钥").fill("fixture-key");
  await panel.getByLabel("插件连接密钥").press("Enter");
  await expect(
    panel.getByText("无法连接插件地址", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByLabel("插件连接密钥")).toHaveValue("");
  await panel.getByLabel("插件连接密钥").fill("fixture-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByText("插件协议版本不兼容", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByRole("button", { name: "保存为停用" })).toHaveCount(
    0,
  );
});

test("missing or incompatible installed plugin reports the backend fault instead of a generic failure", async ({
  page,
}) => {
  await signedIn(page);
  let attempts = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [],
      });
    if (path === "probe") {
      attempts++;
      return json(
        route,
        {
          code:
            attempts === 1 ? "adapter_not_installed" : "adapter_incompatible",
        },
        attempts === 1 ? 404 : 502,
      );
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("插件地址").fill("https://bot.example.test:8443");
  await panel.getByLabel("插件连接密钥").fill("fixture-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByText("安装并启用插件", { exact: false }),
  ).toBeVisible();
  await panel.getByLabel("插件连接密钥").fill("fixture-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByText("插件协议或版本不兼容", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByRole("button", { name: "保存为停用" })).toHaveCount(
    0,
  );
});

for (const [receiptState, readState] of [
  ["unknown", "disabled"],
  ["disabled", "unknown"],
] as const) {
  test(`create receipt ${receiptState} and readback ${readState} do not claim saved`, async ({
    page,
  }) => {
    await signedIn(page);
    let saved = false;
    let writes = 0;
    await page.route("**/api/v1/bot-adapters/*", (route) => {
      const path = new URL(route.request().url()).pathname.split("/").pop();
      if (path === "view")
        return json(route, {
          available: true,
          unlocked: true,
          actors: [actor],
          connections: saved ? [connection(false, 1, readState)] : [],
        });
      if (path === "probe") return json(route, probePayload());
      if (path === "create") {
        writes++;
        saved = true;
        return json(route, { connection: connection(false, 1, receiptState) });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    await page.goto("/#/settings/channels");
    await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
    const panel = await fillWizard(page);
    await panel.getByRole("button", { name: "保存为停用" }).click();
    await expect(
      panel.getByText("保存回执与当前状态未能一致核对", { exact: false }),
    ).toBeVisible();
    await expect(
      panel.getByText("连接已保存且保持停用", { exact: false }),
    ).toHaveCount(0);
    await expect(panel.getByText("结果待核对", { exact: true })).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "启用", exact: true }),
    ).toBeDisabled();
    expect(writes).toBe(1);
  });
}

for (const operation of ["enable", "disable"] as const) {
  test(`${operation} needs matching final state in both receipt and readback`, async ({
    page,
  }) => {
    await signedIn(page);
    const original =
      operation === "disable" ? connection(true, 1, "ready") : connection();
    const readback =
      operation === "disable"
        ? connection(false, 2, "unknown")
        : connection(true, 2, "ready");
    const receipt =
      operation === "disable"
        ? connection(false, 2, "disabled")
        : connection(true, 2, "unknown");
    let written = false;
    await page.route("**/api/v1/bot-adapters/*", (route) => {
      const path = new URL(route.request().url()).pathname.split("/").pop();
      if (path === "view")
        return json(route, {
          available: true,
          unlocked: true,
          actors: [actor],
          connections: [written ? readback : original],
        });
      if (path === operation) {
        written = true;
        return json(route, { connection: receipt });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    await page.goto("/#/settings/channels");
    await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
    const panel = page.getByRole("region", { name: "机器人适配器管理" });
    await panel
      .getByRole("button", {
        name: operation === "enable" ? "启用" : "停用",
        exact: true,
      })
      .click();
    await expect(
      panel.getByText("操作回执与当前状态尚未一致确认", { exact: false }),
    ).toBeVisible();
    await expect(
      panel.getByText(
        operation === "enable" ? "适配器已启用" : "适配器已停用",
        { exact: false },
      ),
    ).toHaveCount(0);
    await expect(panel.getByText("结果待核对", { exact: true })).toBeVisible();
  });
}

test("management expiry during probe restores unlock and clears sensitive draft", async ({
  page,
}) => {
  await signedIn(page);
  let unlocked = true;
  await page.route("**/api/v1/bots/unlock", (route) => {
    unlocked = true;
    return json(route, { unlocked: true });
  });
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [actor],
        connections: [],
      });
    if (path === "probe") {
      unlocked = false;
      return json(route, { code: "management_required" }, 403);
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("插件地址").fill("https://bot.example.test:8443");
  await panel.getByLabel("插件连接密钥").fill("secret-value");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await expect(
    panel.getByRole("button", { name: "解锁连接管理" }),
  ).toBeVisible();
  await expect(panel.getByLabel("插件连接密钥")).toHaveCount(0);
  await panel
    .getByLabel("管理员密码（二次验证）")
    .fill("synthetic-local-password-014");
  await panel.getByRole("button", { name: "解锁连接管理" }).click();
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await expect(panel.getByLabel("插件连接密钥")).toHaveValue("");
  await expect(panel.getByLabel("插件地址")).toHaveValue("");
});

test("management expiry after create preserves unknown warning and requires a fresh probe", async ({
  page,
}) => {
  await signedIn(page);
  let unlocked = true;
  let writes = 0;
  await page.route("**/api/v1/bots/unlock", (route) => {
    unlocked = true;
    return json(route, { unlocked: true });
  });
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [actor],
        connections: [],
      });
    if (path === "probe") return json(route, probePayload());
    if (path === "create") {
      writes++;
      unlocked = false;
      return json(
        route,
        { code: "management_required", execution_state: "unknown" },
        403,
      );
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = await fillWizard(page);
  await panel.getByRole("button", { name: "保存为停用" }).click();
  await expect(
    panel.getByText("保存结果无法确认", { exact: false }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "解锁连接管理" }),
  ).toBeVisible();
  await panel
    .getByLabel("管理员密码（二次验证）")
    .fill("synthetic-local-password-014");
  await panel.getByRole("button", { name: "解锁连接管理" }).click();
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await expect(panel.getByLabel("插件连接密钥")).toHaveValue("");
  await expect(panel.getByRole("button", { name: "保存为停用" })).toHaveCount(
    0,
  );
  expect(writes).toBe(1);
});

test("management expiry during enable reopens unlock without a second write", async ({
  page,
}) => {
  await signedIn(page);
  let unlocked = true;
  let writes = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [actor],
        connections: [connection()],
      });
    if (path === "enable") {
      writes++;
      unlocked = false;
      return json(
        route,
        { code: "management_required", execution_state: "unknown" },
        403,
      );
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "启用", exact: true }).click();
  await expect(
    panel.getByText("操作结果无法确认", { exact: false }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "解锁连接管理" }),
  ).toBeVisible();
  expect(writes).toBe(1);
});

test("private conversation explicitly uses the chosen contact as the allowed author", async ({
  page,
}) => {
  await signedIn(page);
  let saved: ReturnType<typeof connection> | null = null;
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: saved ? [saved] : [],
      });
    if (path === "probe") return json(route, probePayload());
    if (path === "create") {
      submitted = route.request().postDataJSON();
      saved = {
        ...connection(),
        conversation: { kind: "private", id: "555001" },
        allowed_authors: ["555001"],
      };
      return json(route, { connection: saved });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "添加适配器" }).click();
  await panel.getByLabel("插件地址").fill("https://bot.example.test:8443");
  await panel.getByLabel("插件连接密钥").fill("fixture-key");
  await panel.getByRole("button", { name: "检测连接并读取账号" }).click();
  await panel.getByLabel("会话类型").selectOption("private");
  await panel.getByLabel("联系人 ID", { exact: true }).fill("555000");
  await expect(
    panel.getByLabel("私聊允许作者（由联系人 ID 确定）"),
  ).toHaveValue("555000");
  await expect(
    panel.getByLabel("私聊允许作者（由联系人 ID 确定）"),
  ).toHaveJSProperty("readOnly", true);
  await expect(panel.getByLabel("明确允许的作者 ID")).toHaveCount(0);
  await panel.getByLabel("联系人 ID", { exact: true }).fill("555001");
  await expect(
    panel.getByLabel("私聊允许作者（由联系人 ID 确定）"),
  ).toHaveValue("555001");
  await panel.getByLabel("会话类型").selectOption("group");
  await expect(panel.getByLabel("明确允许的作者 ID")).toHaveValue("");
  await panel.getByLabel("会话类型").selectOption("private");
  await panel.getByLabel("连接名称").fill("测试私聊");
  await expect(panel.getByLabel("连接名称")).toHaveAttribute("maxlength", "64");
  await panel.getByLabel("回复角色").selectOption(actor.id);
  await panel.getByRole("button", { name: "保存为停用" }).click();
  await expect(
    panel.getByText("连接已保存且保持停用", { exact: false }),
  ).toBeVisible();
  expect(submitted).toMatchObject({
    conversation: { kind: "private", id: "555001" },
    allowed_authors: ["555001"],
  });
});

test("unknown backend phase requires one explicit reconcile and confirms the original revision", async ({
  page,
}, testInfo) => {
  await signedIn(page);
  let current = connection(false, 7, "unknown");
  let reconciles = 0;
  let ordinaryWrites = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [current],
      });
    if (path === "reconcile") {
      reconciles++;
      const body = route.request().postDataJSON();
      expect(body).toMatchObject({
        id: current.id,
        expected_revision: 7,
        client_request_id: expect.any(String),
      });
      expect(Object.keys(body).sort()).toEqual([
        "client_request_id",
        "expected_revision",
        "id",
      ]);
      current = connection(true, 7, "ready");
      return json(route, { connection: current });
    }
    if (path === "enable" || path === "disable") ordinaryWrites++;
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await expect(
    panel.getByText("若上次是启用，恢复成功后将恢复正常消息处理", {
      exact: false,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("bot-adapter-recovery.png"),
    fullPage: true,
  });
  await expect(
    panel.getByRole("button", { name: "启用", exact: true }),
  ).toBeDisabled();
  await panel.getByRole("button", { name: "刷新状态" }).click();
  expect(reconciles).toBe(0);
  await panel.getByRole("button", { name: "核对并恢复" }).click();
  await expect(panel.getByText("上一次配置已恢复，适配器已启用")).toBeVisible();
  await expect(panel.getByText("已启用", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "核对并恢复" })).toHaveCount(
    0,
  );
  expect(reconciles).toBe(1);
  expect(ordinaryWrites).toBe(0);
});

test("reconcile can confirm a pending disable without choosing a new target", async ({
  page,
}) => {
  await signedIn(page);
  let current = connection(true, 9, "unknown");
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [current],
      });
    if (path === "reconcile") {
      current = connection(false, 9, "disabled");
      return json(route, { connection: current });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await expect(
    panel.getByRole("button", { name: "停用", exact: true }),
  ).toBeDisabled();
  await panel.getByRole("button", { name: "核对并恢复" }).click();
  await expect(panel.getByText("上一次配置已核对，适配器已停用")).toBeVisible();
  await expect(panel.getByText("已保存 · 未启用")).toBeVisible();
});

test("reconcile receipt and readback must agree on revision, enabled and settled state", async ({
  page,
}) => {
  await signedIn(page);
  let current = connection(false, 4, "unknown");
  let reconciles = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [current],
      });
    if (path === "reconcile") {
      reconciles++;
      current = connection(true, 4, "ready");
      return json(route, { connection: connection(true, 5, "ready") });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "核对并恢复" }).click();
  await expect(
    panel.getByText("恢复结果仍待核对", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("结果待核对", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "核对并恢复" })).toHaveCount(
    0,
  );
  expect(reconciles).toBe(1);
});

test("reconcile returning unknown stays pending and never retries itself", async ({
  page,
}) => {
  await signedIn(page);
  const pending = connection(false, 3, "unknown");
  let reconciles = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [pending],
      });
    if (path === "reconcile") {
      reconciles++;
      return json(route, { connection: pending });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "核对并恢复" }).click();
  await expect(
    panel.getByText("恢复结果仍待核对", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("结果待核对", { exact: true })).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "核对并恢复" }),
  ).toBeDisabled();
  expect(reconciles).toBe(1);
});

test("409 result_unknown on ordinary enable only exposes recovery after readback says unknown", async ({
  page,
}) => {
  await signedIn(page);
  let current = connection();
  let ordinaryWrites = 0;
  let reconciles = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked: true,
        actors: [actor],
        connections: [current],
      });
    if (path === "enable") {
      ordinaryWrites++;
      current = connection(false, 1, "unknown");
      return json(route, { code: "result_unknown" }, 409);
    }
    if (path === "reconcile") reconciles++;
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "启用", exact: true }).click();
  await expect(
    panel.getByText("操作结果无法确认", { exact: false }),
  ).toBeVisible();
  await expect(panel.getByText("页面结果待核对，请先刷新状态")).toBeVisible();
  await expect(panel.getByRole("button", { name: "核对并恢复" })).toHaveCount(
    0,
  );
  await panel.getByRole("button", { name: "刷新状态" }).click();
  await expect(panel.getByRole("button", { name: "核对并恢复" })).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "启用", exact: true }),
  ).toBeDisabled();
  expect(ordinaryWrites).toBe(1);
  expect(reconciles).toBe(0);
});

test("reconcile version conflict asks for refresh and management expiry asks for unlock", async ({
  page,
}) => {
  await signedIn(page);
  let mode: "conflict" | "locked" = "conflict";
  let unlocked = true;
  let reconciles = 0;
  await page.route("**/api/v1/bot-adapters/*", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "view")
      return json(route, {
        available: true,
        unlocked,
        actors: [actor],
        connections: [connection(false, 2, "unknown")],
      });
    if (path === "reconcile") {
      reconciles++;
      if (mode === "conflict")
        return json(route, { code: "version_conflict" }, 409);
      unlocked = false;
      return json(
        route,
        { code: "management_required", execution_state: "unknown" },
        403,
      );
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await panel.getByRole("button", { name: "核对并恢复" }).click();
  await expect(
    panel.getByText("连接已在另一窗口改变，请刷新后核对", { exact: false }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "核对并恢复" }),
  ).toBeDisabled();
  expect(reconciles).toBe(1);
  mode = "locked";
  await panel.getByRole("button", { name: "刷新状态" }).click();
  await panel.getByRole("button", { name: "核对并恢复" }).click();
  await expect(
    panel.getByText("恢复结果无法确认", { exact: false }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "解锁连接管理" }),
  ).toBeVisible();
  expect(reconciles).toBe(2);
});

test("draft connection has no recovery action", async ({ page }) => {
  await signedIn(page);
  await page.route("**/api/v1/bot-adapters/view", (route) =>
    json(route, {
      available: true,
      unlocked: true,
      actors: [actor],
      connections: [connection(false, 1, "draft")],
    }),
  );
  await page.goto("/#/settings/channels");
  await page.getByText("高级兼容：精确范围适配器", { exact: true }).click();
  const panel = page.getByRole("region", { name: "机器人适配器管理" });
  await expect(panel.getByRole("button", { name: "核对并恢复" })).toHaveCount(
    0,
  );
  await expect(
    panel.getByRole("button", { name: "启用", exact: true }),
  ).toBeDisabled();
});
