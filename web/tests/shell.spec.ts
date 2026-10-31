import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { modules, resolveRoute } from "../src/app/modules";

// The static shell suite explicitly supplies a synthetic session; no production preview bypass.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/infrastructure/view", (route) =>
    route.fulfill({
      json: {
        configured: false,
        revision: 0,
        catalog: null,
        nodes: [],
        max_nodes: 64,
      },
    }),
  );
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        csrf: "synthetic-shell",
        username: "preview",
        conversations: [],
        dialogue: {
          available: false,
          model: "not_configured",
          code: "model_not_configured",
        },
      },
    }),
  );
});

test("route boundaries reject unknown and malformed addresses", () => {
  expect(resolveRoute("")?.module.id).toBe("workbench");
  expect(resolveRoute("#/settings/connections")?.section).toBe(1);
  expect(resolveRoute("#/settings/weather")?.section).toBe(9);
  expect(resolveRoute("#/people")).toMatchObject({
    module: { id: "memory" },
    section: 0,
  });
  for (const hash of [
    "#/missing",
    "#/room/0",
    "#/settings/-1",
    `#/settings/${modules.find((module) => module.id === "settings")!.sections.length}`,
    "#/home/devices/extra",
    "#/%ZZ",
  ])
    expect(resolveRoute(hash)).toBeNull();
});

test("anonymous entry gates every workspace without loading business media", async ({
  page,
}) => {
  await page.route("**/api/v1/session", (route) =>
    route.fulfill({
      json: { authenticated: false, csrf: "synthetic-anonymous" },
    }),
  );
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  for (const path of ["/", "/#/roles/room", "/#/workbench", "/#/roles/life"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "登录天枢" })).toBeVisible();
    await expect(
      page.locator(".app-shell, .sidebar, canvas, video, audio"),
    ).toHaveCount(0);
    await expect(page.getByRole("navigation")).toHaveCount(0);
    await expect(page.locator('a[href="#/roles/room"]')).toHaveCount(0);
  }
  expect(requests.some((url) => /RoomPage-/.test(url))).toBe(false);
  expect(
    requests.filter(
      (url) => /\/api\/|^https:/.test(url) && !url.endsWith("/api/v1/session"),
    ),
  ).toEqual([]);
});

test("connection summary distinguishes configured, failed and absent capabilities", async ({
  page,
}) => {
  await page.route("**/api/v1/connections/view", (route) =>
    route.fulfill({
      json: {
        connections: [
          {
            id: "knowledge",
            state: "unverified",
            code: "read_not_observed",
            detail: null,
            checked_at: null,
          },
          {
            id: "life",
            state: "unavailable",
            code: "timeout",
            detail: null,
            checked_at: "2026-09-27T00:00:00Z",
          },
          {
            id: "memory_profiles",
            state: "not_configured",
            code: "memory_not_configured",
            detail: null,
            checked_at: null,
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/access/view", (route) =>
    route.fulfill({
      json: {
        available: false,
        active: { mode: "http", origin: "", certificate: null },
        saved: { mode: "http", origin: "", certificate: null },
        revision: 1,
        restart_required: false,
        certificates: [],
        listener_port: 80,
      },
    }),
  );
  await page.goto("/#/settings/connections");
  await expect(page.locator(".connection-list li")).toHaveCount(3);
  await expect(page.locator(".connection-list")).toContainText("尚未验证");
  await expect(page.locator(".connection-list")).toContainText("暂时不可用");
  await expect(page.locator(".connection-list")).toContainText("未配置");
  await page.goto("/#/system/containers");
  await expect(
    page.getByText("节点观测尚未配置", {
      exact: false,
    }),
  ).toBeVisible();
});

test("drawer keyboard trap, Escape return, theme persistence and motion", async ({
  page,
}) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "外观设置", exact: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "外观设置" });
  await expect(dialog).toBeVisible();
  for (let index = 0; index < 9; index++) {
    await page.keyboard.press("Tab");
    expect(
      await dialog.evaluate((element) =>
        element.contains(document.activeElement),
      ),
    ).toBe(true);
  }
  await dialog.getByRole("radio", { name: "深色", exact: true }).check();
  await dialog.getByRole("checkbox", { name: /减少透明度/ }).check();
  expect(
    await dialog.evaluate(
      (element) => getComputedStyle(element).animationDuration,
    ),
  ).toBe("0s");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute(
    "data-transparency",
    "reduce",
  );
});

test("mobile menu closes after keyboard navigation and route focus moves", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile");
  await page.goto("/");
  await page.getByRole("button", { name: "打开导航" }).click();
  const dialog = page.getByRole("dialog", { name: "导航", exact: true });
  await dialog.getByRole("link", { name: "用户", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(dialog).not.toBeVisible();
  await expect(page.locator("main")).toBeFocused();
  await expect(
    page.getByRole("heading", { name: "用户", exact: true }),
  ).toBeVisible();
});

test("failed chunk retains navigation and reload recovery", async ({
  page,
}) => {
  await page.route("**/RoomPage-*.js", (route) => route.abort());
  await page.goto("/#/roles/room");
  await expect(page.getByRole("alert")).toContainText("页面未能加载");
  await page.unroute("**/RoomPage-*.js");
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "小屋环境预览" }),
  ).toBeVisible();
});

test("pending chunk shows loading then real unconfigured state", async ({
  page,
}) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/RoomPage-*.js", async (route) => {
    await pending;
    await route.continue();
  });
  await page.goto("/#/roles/room", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("status")).toContainText("正在加载页面");
  release();
  await expect(
    page.getByRole("heading", { name: "小屋环境预览" }),
  ).toBeVisible();
});

test("unknown route, offline notice and blocked preference storage", async ({
  page,
  context,
}) => {
  await page.addInitScript(() => {
    Storage.prototype.setItem = () => {
      throw new Error("blocked");
    };
  });
  await page.goto("/#/no-such-module");
  await expect(
    page.getByRole("heading", { name: "找不到这个页面" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "返回工作台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "开始使用天枢" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "外观设置", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("浏览器未允许保存偏好");
  await page.keyboard.press("Escape");
  await context.setOffline(true);
  await expect(page.getByRole("status")).toContainText("浏览器当前离线");
  await context.setOffline(false);
  await expect(
    page.getByText("浏览器当前离线。已加载的页面仍可浏览。"),
  ).not.toBeVisible();
});

test("representative light/dark layout, 200% text and accessibility", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "开始使用天枢" }),
  ).toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page.getByRole("button", { name: "外观设置", exact: true }).click();
    await page
      .getByRole("radio", {
        name: theme === "light" ? "浅色" : "深色",
        exact: true,
      })
      .check();
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.keyboard.press("Escape");
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(`${theme}.png`),
      fullPage: true,
    });
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "200%";
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.evaluate(() => {
      document.documentElement.style.fontSize = "";
    });
  }
});

test("signed-in login entry returns to a valid destination and rejects external targets", async ({
  page,
}) => {
  await page.goto("/#/login?next=" + encodeURIComponent("#/system/containers"));
  await expect(page).toHaveURL(/#\/home\/1$/);
  await expect(page.locator(".app-shell")).toBeVisible();
  await page.goto("/#/login?next=" + encodeURIComponent("https://example.com"));
  await expect(page).toHaveURL(/#\/workbench$/);
  await expect(page.locator(".auth-screen")).toHaveCount(0);
});
