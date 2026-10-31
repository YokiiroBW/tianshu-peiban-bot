import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const ADMIN = "synthetic-admin";
const ADMIN_PASSWORD = "synthetic-local-password-014";

async function loginModels(page: Page) {
  await page.goto("/#/settings/models");
  await page.getByLabel("管理员账号").fill(ADMIN);
  await page.getByLabel("密码", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  // The management state itself is asserted by each test; being logged in is not authority.
  await expect(page.getByRole("button", { name: "重新读取" })).toBeVisible();
}

async function unlockModels(page: Page) {
  await page.getByLabel("管理员密码").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "解锁模型管理" }).click();
  await expect(page.getByText("管理已解锁", { exact: true })).toBeVisible();
}

function managementState(page: Page, label: string) {
  return page.locator(".rail-label").filter({ hasText: label });
}

function target(page: Page, label: string) {
  return page.locator("article.models-target").filter({ hasText: label });
}

function currentVersion(page: Page, label: string) {
  return target(page, label).locator(".models-facts dd").first();
}

/** Version lists are newest first and numbers repeat as substrings: match the label exactly. */
function versionItem(page: Page, label: string, version: number) {
  return target(page, label)
    .locator("li")
    .filter({
      has: page.locator(".models-version", {
        hasText: new RegExp(`^版本 ${version}$`),
      }),
    });
}

async function plannedVersion(page: Page) {
  const button = page.getByRole("button", { name: /^发布版本 \d+$/ });
  return Number((await button.innerText()).replace(/\D+/g, ""));
}

test("synthetic UI state fixture: receipt, collector, processing, segments, unknown and cancel", async ({
  page,
}) => {
  // Reproduce the LAN HTTP capability surface, without weakening any browser protection.
  await page.addInitScript(() =>
    Object.defineProperty(Crypto.prototype, "randomUUID", { value: undefined }),
  );
  await page.goto("/#/roles/chat");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.route("**/api/v1/session", async (route) => {
    const response = await route.fetch();
    const session = await response.json();
    await route.fulfill({
      response,
      json: {
        ...session,
        dialogue: { available: true, code: "ready", model: "unverified" },
      },
    });
  });
  let phase = "collector";
  await page.route("**/api/v1/snapshot", async (route) => {
    const turn = {
      turn_id: "turn:fixture",
      turn_sequence: 1,
      version: 3,
      phase: phase === "unknown" ? "closed_unknown" : "generating",
      delivery_state: phase === "unknown" ? "unknown" : "not_started",
      unresolved_delivery: phase === "unknown",
    };
    const view = {
      turn,
      messages: [
        {
          message_id: "message:fixture",
          revision: 1,
          parts: [{ kind: "text", text: "合成原文" }],
          state: "active",
          sent_at: "2026-09-14T06:00:00Z",
        },
      ],
      replies:
        phase === "unknown"
          ? [
              {
                reply_id: "reply:1",
                segment_sequence: 1,
                segment_count: 2,
                state: "sent",
                content_state: "available",
                text: "合成已送达片段",
              },
              {
                reply_id: "reply:2",
                segment_sequence: 2,
                segment_count: 2,
                state: "unknown",
                content_state: "unavailable",
                text: null,
              },
            ]
          : [],
    };
    await route.fulfill({
      json: {
        state: "current",
        submissions: [],
        snapshot: {
          observed_at: "2026-09-14T06:00:00Z",
          collectors:
            phase === "collector"
              ? [
                  {
                    collection_id: "collection:fixture",
                    revision: 1,
                    deadline_at: "2026-09-14T06:00:05Z",
                    messages: view.messages,
                  },
                ]
              : [],
          active_turns: phase === "generating" ? [view] : [],
          history: phase === "unknown" ? [view] : [],
          next_before_turn_sequence: null,
        },
      },
    });
  });
  await page.route("**/api/v1/messages", async (route) => {
    expect(Object.keys(route.request().postDataJSON()).sort()).toEqual([
      "actor",
      "client_request_id",
      "conversation",
      "text",
    ]);
    phase = "generating";
    await route.fulfill({
      json: { message_id: "message:fixture", state: "accepted", result: null },
    });
  });
  await page.route("**/api/v1/cancel", async (route) => {
    expect(route.request().postDataJSON().expected_revision).toBe(3);
    phase = "unknown";
    await route.fulfill({ json: { state: "unknown", version: 4 } });
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "正在合并续句" }),
  ).toBeVisible();
  await page.getByLabel("想说些什么").fill("合成原文");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(
    page.getByText("入站已接收，等待后台状态；尚不代表已回复。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "第 1 轮 · 后台处理中" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消第 1 轮" }).click();
  await expect(
    page.getByRole("heading", { name: "第 1 轮 · 发送结果未知" }),
  ).toBeVisible();
  await expect(page.getByText("合成已送达片段", { exact: true })).toBeVisible();
  await expect(page.getByText("正文暂不可展示")).toBeVisible();
  const diagnostics = page.getByText("版本 3 · closed_unknown / unknown", {
    exact: true,
  });
  await expect(diagnostics).toBeHidden();
  await page.getByText("状态详情", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(diagnostics).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(diagnostics).toBeHidden();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("real local login, keyboard, unavailable dialogue, reload and logout", async ({
  page,
}, testInfo) => {
  await page.goto("/#/roles/chat");
  await expect(page.getByLabel("管理员账号")).toBeEnabled();
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-local-password-014");
  await page.getByLabel("密码", { exact: true }).press("Tab");
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "角色", exact: true }),
  ).toHaveValue("actor:a");
  await page
    .getByRole("combobox", { name: "角色", exact: true })
    .selectOption("actor:b");
  await expect(
    page.getByRole("heading", { name: "对话通道尚未接通" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("想说些什么").fill("合成草稿，不发送");
  const violations = (await new AxeBuilder({ page }).analyze()).violations;
  expect(violations).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("web-console-light.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await expect(page.getByLabel("想说些什么")).toHaveValue("");
  await page.getByRole("button", { name: "外观设置" }).click();
  await page.getByLabel("深色", { exact: true }).check();
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: testInfo.outputPath("web-console-dark.png"),
    fullPage: true,
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("管理员账号")).toBeEnabled();
});

test("wrong password and network interruption never show a conversation", async ({
  page,
}) => {
  await page.goto("/#/roles/chat");
  await page.getByLabel("管理员账号").fill("synthetic-admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("synthetic-wrong-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("账号或密码不正确");
  await expect(page.getByLabel("密码", { exact: true })).toHaveValue("");
  await page.context().setOffline(true);
  await page.getByRole("button", { name: "刷新账号状态" }).click();
  await expect(
    page.getByRole("heading", { name: "暂时无法连接天枢" }),
  ).toBeVisible();
  await expect(page.getByLabel("管理员账号")).toHaveCount(0);
  await page.context().setOffline(false);
  await expect(page.getByLabel("管理员账号")).toBeEnabled();
  await expect(page.getByRole("button", { name: "退出登录" })).toHaveCount(0);
});

test("real console model management: gated unlock, preview, publish and revoke", async ({
  page,
}, testInfo) => {
  await loginModels(page);
  // An ordinary chat login never carries the management write port.
  await expect(page.getByRole("button", { name: "预览" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /^撤销版本 \d+$/ }),
  ).toHaveCount(0);
  const nativeBefore = await currentVersion(
    page,
    "原生 Responses 配置",
  ).innerText();
  await unlockModels(page);
  await page.getByLabel("配置模板").selectOption("chat-local-text");
  await page.getByRole("button", { name: "预览" }).click();
  await expect(page.locator(".models-preview")).toBeVisible();
  const version = await plannedVersion(page);
  await page.getByRole("button", { name: `发布版本 ${version}` }).click();
  await expect(page.locator(".models-notice")).toContainText(
    `已发布版本 ${version}，可用至`,
  );
  await expect(currentVersion(page, "Chat 兼容配置")).toHaveText(
    String(version),
  );
  // Chat and native keep separate version sequences: one never moves the other.
  await expect(currentVersion(page, "原生 Responses 配置")).toHaveText(
    nativeBefore,
  );
  const chatVersion = versionItem(page, "Chat 兼容配置", version);
  await expect(chatVersion.locator(".rail-label")).toHaveText("可用");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("models-published.png"),
    fullPage: true,
  });
  await chatVersion
    .getByRole("button", { name: `撤销版本 ${version}` })
    .click();
  await expect(page.locator(".models-notice")).toContainText(
    `已撤销版本 ${version}`,
  );
  await expect(
    versionItem(page, "Chat 兼容配置", version).locator(".rail-label"),
  ).toHaveText("已撤销");
  await expect(managementState(page, "管理已解锁")).toBeVisible();
  await page.getByRole("button", { name: "锁定管理" }).click();
  await expect(managementState(page, "管理待验证")).toBeVisible();
  await expect(page.getByRole("button", { name: "预览" })).toHaveCount(0);
});

test("real console: a second browser with a stale version conflicts instead of overwriting", async ({
  page,
  browser,
}) => {
  const second = await browser.newContext();
  const other = await second.newPage();
  try {
    await loginModels(page);
    await unlockModels(page);
    await loginModels(other);
    await unlockModels(other);
    for (const view of [page, other]) {
      await view.getByLabel("配置模板").selectOption("chat-local-text");
      await view.getByRole("button", { name: "预览" }).click();
      await expect(view.locator(".models-preview")).toBeVisible();
    }
    const version = await plannedVersion(page);
    await page.getByRole("button", { name: `发布版本 ${version}` }).click();
    await expect(page.locator(".models-notice")).toContainText(
      `已发布版本 ${version}`,
    );
    // The second browser still holds the version it read before the first publish.
    await other.getByRole("button", { name: /^发布版本 \d+$/ }).click();
    await expect(other.locator(".models-error")).toContainText(
      "后台版本已变化",
    );
    await expect(other.locator(".models-notice")).toContainText(
      "已按后台当前版本重新读取；请重新预览后再发布。",
    );
    await expect(
      other.getByRole("button", { name: /^发布版本 \d+$/ }),
    ).toHaveCount(0);
    await expect(currentVersion(other, "Chat 兼容配置")).toHaveText(
      String(version),
    );
  } finally {
    await second.close();
  }
});

test("real console: a retried publish replays instead of creating another version", async ({
  page,
}) => {
  await loginModels(page);
  await unlockModels(page);
  const chatBefore = await currentVersion(page, "Chat 兼容配置").innerText();
  const result = await page.evaluate(async () => {
    const send = async (path: string, body: object) => {
      const session = await (
        await fetch("/api/v1/session", { credentials: "same-origin" })
      ).json();
      const response = await fetch(`/api/v1/${path}`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": session.csrf,
        },
        body: JSON.stringify(body),
      });
      return { status: response.status, body: await response.json() };
    };
    const view = await send("models/view", {});
    const native = view.body.targets.find(
      (item: { target: string }) => item.target === "native",
    );
    const request = {
      template_id: "native-local-text",
      expected_revision: native.current_version,
      client_request_id: crypto.randomUUID(),
    };
    const first = await send("models/publish", request);
    const second = await send("models/publish", request);
    return { first, second };
  });
  expect(result.first.status).toBe(200);
  expect(result.first.body.state).toBe("published");
  expect(result.second.status).toBe(200);
  expect(result.second.body.state).toBe("replayed");
  expect(result.second.body.version).toBe(result.first.body.version);
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(currentVersion(page, "原生 Responses 配置")).toHaveText(
    String(result.first.body.version),
  );
  await expect(currentVersion(page, "Chat 兼容配置")).toHaveText(chatBefore);
});

test("real console: losing the session returns to login without management state", async ({
  page,
}) => {
  await loginModels(page);
  await unlockModels(page);
  await page.context().clearCookies();
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(managementState(page, "管理已解锁")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "预览" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /^发布版本 \d+$/ }),
  ).toHaveCount(0);
});

test("synthetic UI state fixture: management disabled and unauthorized states", async ({
  page,
}) => {
  await page.route("**/api/v1/models/view", async (route) => {
    await route.fulfill({
      json: {
        management: {
          available: false,
          code: "management_disabled",
          unlocked: false,
          unlock_ttl_seconds: 900,
          templates: [],
        },
        targets: [
          {
            target: "chat",
            label: "Chat 兼容配置",
            detail: "text-dialogue/v1 · companion.text",
            protocol: "openai-chat-completions",
            workload: "companion.text",
            current_version: null,
            availability: "unconfigured",
            usable_until: null,
            versions: [],
          },
        ],
      },
    });
  });
  await loginModels(page);
  await expect(
    page.getByRole("heading", { name: "这个部署没有开启网页模型管理" }),
  ).toBeVisible();
  await expect(managementState(page, "管理未开启")).toBeVisible();
  await expect(page.getByRole("button", { name: "预览" })).toHaveCount(0);
  await expect(page.getByLabel("管理员密码")).toHaveCount(0);
  await page.unroute("**/api/v1/models/view");
  await page.route("**/api/v1/models/view", async (route) => {
    await route.fulfill({
      json: {
        management: {
          available: false,
          code: "operator_not_authorized",
          unlocked: false,
          unlock_ttl_seconds: 900,
          templates: [],
        },
        targets: [],
      },
    });
  });
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(
    page.getByRole("heading", { name: "当前账号没有模型配置权限" }),
  ).toBeVisible();
  await expect(managementState(page, "无管理权限")).toBeVisible();
});

test("synthetic UI state fixture: an expired session blocks management without false success", async ({
  page,
}) => {
  await page.route("**/api/v1/models/view", async (route) => {
    await route.fulfill({ status: 401, json: { code: "session_expired" } });
  });
  await loginModels(page);
  // The management read never becomes a silent empty panel or an unlocked state.
  await expect(page.locator(".models-error")).toContainText("登录已过期");
  await expect(managementState(page, "管理已解锁")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "解锁模型管理" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "预览" })).toHaveCount(0);
  await expect(page.locator(".models-target")).toHaveCount(0);
  await page.unroute("**/api/v1/models/view");
  await page.getByRole("button", { name: "重新读取" }).click();
  await expect(currentVersion(page, "Chat 兼容配置")).toBeVisible();
  await expect(page.locator(".models-error")).toHaveCount(0);
});

/* ------------------------------------------------------------------ household devices
 *
 * The synthetic Home Assistant REST surface lives in the same fixture process
 * (tests/backend/home_fixtures.py, port 4817). These cases are real browser sessions against
 * the real connector: a receipt is never displayed as an execution, and a cancelled or
 * unknown command is never resent on its own.
 */

const HOME = "http://127.0.0.1:4817";
const HOME_ENTITIES: [string, string][] = [
  ["light.study", "off"],
  ["switch.kettle", "off"],
  ["sensor.living_temperature", "23.5"],
];

type HomeLog = {
  mode: string;
  requests: { method: string; path: string }[];
  states: Record<string, { state: string }>;
};

/** The synthetic HA is one long-lived process: every case starts from a known state. */
async function resetHome(request: APIRequestContext) {
  await request.post(`${HOME}/fixture/mode`, {
    data: { mode: "normal", service_mode: null },
  });
  for (const [entity_id, state] of HOME_ENTITIES) {
    await request.post(`${HOME}/fixture/state`, { data: { entity_id, state } });
  }
  await request.post(`${HOME}/fixture/log/clear`, { data: {} });
}

async function homeMode(request: APIRequestContext, data: object) {
  await request.post(`${HOME}/fixture/mode`, { data });
}

/** Every HA API call the connector made, and the fixture's own entity states. */
async function homeLog(request: APIRequestContext): Promise<HomeLog> {
  return (await (await request.get(`${HOME}/fixture/log`)).json()) as HomeLog;
}

async function homeServices(request: APIRequestContext) {
  const log = await homeLog(request);
  return log.requests.filter(
    (row) => row.method === "POST" && row.path.startsWith("/api/services/"),
  );
}

function entityCard(page: Page, label: string) {
  return page.locator("article.home-entity").filter({ hasText: label });
}

async function loginHome(page: Page) {
  await page.goto("/#/home/devices");
  await page.getByLabel("管理员账号").fill(ADMIN);
  await page.getByLabel("密码", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator("article.home-entity").first()).toBeVisible();
}

async function unlockHome(page: Page) {
  await page.getByLabel("管理员密码").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "解锁设备控制" }).click();
  await expect(page.getByRole("button", { name: "锁定控制" })).toBeVisible();
}

test("real household devices: readings, read-only sensors and gated control", async ({
  page,
  request,
}, testInfo) => {
  await resetHome(request);
  await loginHome(page);
  const light = entityCard(page, "书房灯");
  await expect(light.locator(".rail-label")).toHaveText("当前");
  await expect(light.locator('[data-field="reading"]')).toHaveText("已关闭");
  await expect(light.locator('[data-field="observed"]')).not.toHaveText(
    "没有采样时间",
  );
  const sensor = entityCard(page, "客厅温度");
  await expect(sensor.locator('[data-field="reading"]')).toHaveText("23.5°C");
  await expect(sensor).toContainText("只读传感器：不提供开关动作");
  await expect(sensor.getByRole("button")).toHaveCount(0);
  // An ordinary login reads states but never carries the control port.
  await expect(page.getByRole("button", { name: "打开书房灯" })).toHaveCount(0);
  await expect(
    page.locator(".rail-label", { hasText: "控制待验证" }),
  ).toBeVisible();
  await unlockHome(page);
  await page.getByRole("button", { name: "打开书房灯" }).click();
  // The receipt is an acceptance; the device state is only claimed after a later read.
  await expect(page.locator(".home-notice")).toContainText("已受理");
  await expect(light.locator('[data-field="reading"]')).toHaveText("已关闭");
  await expect(page.locator(".home-controls li").first()).toContainText(
    "已受理，等待设备反馈",
  );
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("home-devices.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "外观设置" }).click();
  await page.getByLabel("深色", { exact: true }).check();
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: testInfo.outputPath("home-devices-dark.png"),
    fullPage: true,
  });
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "外观设置" }).click();
  await page.getByLabel("跟随系统", { exact: true }).check();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "重新读取设备状态" }).click();
  await expect(light.locator('[data-field="reading"]')).toHaveText("已开启");
  await expect(page.locator(".home-controls li").first()).toContainText(
    "后续观测已确认目标状态",
  );
  await page.getByRole("button", { name: "锁定控制" }).click();
  await expect(
    page.locator(".rail-label", { hasText: "控制待验证" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "打开书房灯" })).toHaveCount(0);
});

test("real household devices: a second browser with a stale reading conflicts", async ({
  page,
  browser,
  request,
}) => {
  await resetHome(request);
  const second = await browser.newContext();
  const other = await second.newPage();
  try {
    await loginHome(page);
    await loginHome(other);
    await unlockHome(page);
    await unlockHome(other);
    await page.getByRole("button", { name: "打开书房灯" }).click();
    await expect(page.locator(".home-notice")).toContainText("已受理");
    await page.getByRole("button", { name: "重新读取设备状态" }).click();
    await expect(
      entityCard(page, "书房灯").locator('[data-field="reading"]'),
    ).toHaveText("已开启");
    // The second browser still holds the reading it saw before the light changed.
    await other.getByRole("button", { name: "关闭书房灯" }).click();
    await expect(other.locator(".home-error")).toContainText(
      "设备状态已被改变",
    );
    // The conflict re-reads the device instead of overwriting it.
    await expect(
      entityCard(other, "书房灯").locator('[data-field="reading"]'),
    ).toHaveText("已开启");
    await other.getByRole("button", { name: "关闭书房灯" }).click();
    await expect(other.locator(".home-notice")).toContainText("已受理");
    await other.getByRole("button", { name: "重新读取设备状态" }).click();
    await expect(
      entityCard(other, "书房灯").locator('[data-field="reading"]'),
    ).toHaveText("已关闭");
  } finally {
    await second.close();
  }
});

test("real household devices: a cancelled wait claims nothing and resends nothing", async ({
  page,
  request,
}) => {
  await resetHome(request);
  await homeMode(request, { mode: "normal", service_mode: "hold" });
  await loginHome(page);
  await unlockHome(page);
  await page.getByRole("button", { name: "打开书房灯" }).click();
  await page.getByRole("button", { name: "取消等待" }).click();
  await expect(page.locator(".home-notice")).toContainText("已取消等待");
  // Nothing is displayed as success, and the device has not moved while HA holds the call.
  await expect(page.locator(".home-notice")).not.toContainText("已受理");
  await expect(
    entityCard(page, "书房灯").locator('[data-field="reading"]'),
  ).toHaveText("已关闭");
  await expect(page.locator(".home-controls li").first()).toContainText(
    "正在执行，尚无回执",
  );
  expect(await homeServices(request)).toHaveLength(1);
  // HA finishes the command it already received; only a later read can show that.
  await request.post(`${HOME}/fixture/release`, { data: {} });
  await expect
    .poll(async () => (await homeLog(request)).states["light.study"].state, {
      timeout: 15000,
    })
    .toBe("on");
  await homeMode(request, { mode: "normal", service_mode: null });
  await page.getByRole("button", { name: "重新读取设备状态" }).click();
  await expect(
    entityCard(page, "书房灯").locator('[data-field="reading"]'),
  ).toHaveText("已开启");
  await page.getByRole("button", { name: "重新读取设备状态" }).click();
  await expect(page.locator(".home-controls li").first()).toContainText(
    "已受理，等待设备反馈",
  );
  // The cancelled client id is never re-sent by the page: the log still holds one call.
  expect(await homeServices(request)).toHaveLength(1);
});

test("real household devices: an unknown outcome is shown as unknown and not resent", async ({
  page,
  request,
}) => {
  await resetHome(request);
  await loginHome(page);
  await unlockHome(page);
  await homeMode(request, { mode: "normal", service_mode: "server_error" });
  await page.getByRole("button", { name: "打开书房灯" }).click();
  await expect(page.locator(".home-error")).toContainText("结果未知");
  await expect(page.locator(".home-controls li").first()).toContainText(
    "结果未知，不会自动重发",
  );
  expect(await homeServices(request)).toHaveLength(1);
  // Waiting does not produce an automatic resend, even though the outcome is unknown.
  await page.waitForTimeout(1500);
  expect(await homeServices(request)).toHaveLength(1);
  // Only the operator asking again sends a second command, and then it is a new request.
  await homeMode(request, { mode: "normal", service_mode: null });
  await page.getByRole("button", { name: "打开书房灯" }).click();
  await expect(page.locator(".home-notice")).toContainText("已受理");
  expect(await homeServices(request)).toHaveLength(2);
});

test("real household devices: an unreachable connector shows offline and recovers", async ({
  page,
  request,
}) => {
  await resetHome(request);
  await loginHome(page);
  await homeMode(request, { mode: "server_error" });
  await page.getByRole("button", { name: "重新读取设备状态" }).click();
  const light = entityCard(page, "书房灯");
  await expect(light.locator(".rail-label")).toHaveText("无法读取");
  // A reading kept from before the connector lost contact stays labelled as the last one.
  await expect(light.locator('[data-field="reading"]')).toHaveText(
    "上次读数：已关闭",
  );
  await expect(
    entityCard(page, "客厅温度").locator('[data-field="reading"]'),
  ).toHaveText("上次读数：23.5°C");
  await homeMode(request, { mode: "normal" });
  await page.getByRole("button", { name: "重新读取设备状态" }).click();
  await expect(light.locator(".rail-label")).toHaveText("当前");
  await expect(light.locator('[data-field="reading"]')).toHaveText("已关闭");
});

test("synthetic UI state fixture: read-only deployment and an expired login", async ({
  page,
}) => {
  await page.route("**/api/v1/home/refresh", async (route) => {
    await route.fulfill({
      json: {
        connector: {
          available: true,
          code: "control_disabled",
          enabled: false,
          control_available: false,
          unlocked: false,
          unlock_ttl_seconds: 900,
          stale_after_seconds: 120,
          timeout_seconds: 4,
          entities: 1,
          templates: 0,
          readable: true,
        },
        entities: [
          {
            entity_id: "light.study",
            label: "书房灯",
            kind: "light",
            unit: null,
            availability: "current",
            code: "ok",
            state: "off",
            value: null,
            observed_at: "2026-09-15T06:00:00Z",
            attempt_at: "2026-09-15T06:00:00Z",
            revision: 1,
            control: true,
            templates: [],
          },
        ],
        controls: [],
      },
    });
  });
  await loginHome(page);
  await expect(
    page.locator(".rail-label", { hasText: "仅读取，未开启控制" }),
  ).toBeVisible();
  await expect(page.getByLabel("管理员密码")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "打开书房灯" })).toHaveCount(0);
  await expect(page.locator("article.home-entity")).toContainText(
    "不显示开关动作",
  );
  await page.unroute("**/api/v1/home/refresh");
  // A login that is gone while the page is open shows the real state, never a stale panel.
  await page.route("**/api/v1/home/refresh", async (route) => {
    await route.fulfill({ status: 401, json: { code: "session_expired" } });
  });
  await page.route("**/api/v1/session", async (route) => {
    await route.fulfill({ json: { authenticated: false, csrf: "stub-csrf" } });
  });
  await page.getByRole("button", { name: "重新读取设备状态" }).click();
  await expect(page).toHaveURL(/#\/login\?next=/);
  await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
  await expect(page.getByLabel("管理员账号")).toBeVisible();
  await expect(page.locator("article.home-entity")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "锁定控制" })).toHaveCount(0);
});
